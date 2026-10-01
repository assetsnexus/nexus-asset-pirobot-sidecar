"""Optional vendor hardware executor — imports Adeept modules without editing them.

When GPIO / HAT libraries are absent (compose dry-run, CI), actions are logged only.
Mirrors webServer_HAT_V3.1 robotCtrl / switchCtrl without patching that file.
Adds open-loop timed motion, sensor sampling, and WS2812 police lights in the overlay.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Optional

from .config import OverlayMotionConfig
from .metric_slots import (
    SERVO_ARM,
    SERVO_CAMERA,
    SERVO_GRAB,
    SERVO_HAND,
    SERVO_LOOK,
    SPEED_LEFT,
    SPEED_RIGHT,
    build_rasptank_metric_store,
)
from .motion import MotionController
from .odometry import side_speeds_mps, speed_mps
from .sensors import SensorSuite
from .servos import arm_rest_deg, park_arm_upright

logger = logging.getLogger(__name__)

# Vendor switch.py drives these BCM lines (LED ports 1–3).
_LED_GPIO = {1: 9, 2: 25, 3: 11}


def _claim_output_quiet(lgpio: Any, handle: int, gpio: int, level: int) -> None:
    """lgpio prints xGpioHandleRequest to stderr before raising."""
    saved = os.dup(sys.stderr.fileno())
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            os.dup2(devnull.fileno(), sys.stderr.fileno())
            lgpio.gpio_claim_output(handle, gpio, level)
    finally:
        os.dup2(saved, sys.stderr.fileno())
        os.close(saved)


def _pinctrl_level(gpio: int, level: int) -> bool:
    """Drive a header GPIO by writing the pad registers.

    GPIO 9 and 11 are SPI0 MISO and SCLK. The kernel answers a GPIO line
    request with EINVAL, so lgpio cannot own them. pinctrl still sets the
    pad, which is how the HAT LEDs on those pins are switched.
    """
    pinctrl = shutil.which("pinctrl")
    if pinctrl is None:
        logger.warning("pinctrl is not installed; GPIO %s stays unclaimed", gpio)
        return False
    proc = subprocess.run(
        [pinctrl, "set", str(gpio), "op", "pn", "dh" if level else "dl"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        logger.warning("pinctrl set %s failed: %s", gpio, detail)
        return False
    return True


class _DirectLeds:
    """Drive the HAT LEDs. lgpio for lines the kernel will grant, pinctrl otherwise."""

    def __init__(self) -> None:
        import lgpio

        self._lgpio = lgpio
        chip = 4 if os.path.exists("/dev/gpiochip4") else 0
        self._handle = lgpio.gpiochip_open(chip)
        if self._handle < 0:
            raise RuntimeError(f"gpiochip_open({chip}) failed: {self._handle}")
        self._pins: dict[int, int] = {}
        self._pad_pins: dict[int, int] = {}
        for port, gpio in _LED_GPIO.items():
            try:
                _claim_output_quiet(lgpio, self._handle, gpio, 0)
                self._pins[port] = gpio
            except Exception as exc:
                if _pinctrl_level(gpio, 0):
                    self._pad_pins[port] = gpio
                    logger.info(
                        "LED port %s GPIO %s via pinctrl (gpiolib: %s)", port, gpio, exc
                    )
                else:
                    logger.warning("LED port %s GPIO %s unavailable: %s", port, gpio, exc)
        if not self._pins and not self._pad_pins:
            raise RuntimeError("no LED GPIO lines claimed")

    def switch(self, port: int, status: int) -> None:
        gpio = self._pins.get(port)
        if gpio is not None:
            self._lgpio.gpio_write(self._handle, gpio, 1 if status else 0)
            return
        pad = self._pad_pins.get(port)
        if pad is None:
            logger.info("LED port %s is not claimed", port)
            return
        _pinctrl_level(pad, 1 if status else 0)


def _quiet_servo_prints(servo_cls: type) -> None:
    """Vendor pause/resume print a banner on every stop. Keep the flag behavior."""

    def pause(self: Any) -> None:
        self._ServoCtrl__flag.clear()

    def resume(self: Any) -> None:
        self._ServoCtrl__flag.set()

    servo_cls.pause = pause  # type: ignore[method-assign]
    servo_cls.resume = resume  # type: ignore[method-assign]


class HardwareExecutor:
    """Best-effort drive path using vendor move / switch / RPIservo / robotLight."""

    def __init__(
        self,
        motion_config: Optional[OverlayMotionConfig] = None,
        sensors: Optional[SensorSuite] = None,
    ) -> None:
        self._ready = False
        self._speed = 60
        self._direction = "no"
        self._turn = "no"
        self._move = None
        self._switch = None
        self._sc = None  # RPIservo.ServoCtrl
        self._ws2812 = None
        self._ws2812_ready = False
        self._init_error: Optional[str] = None
        self._motion_cfg = motion_config or OverlayMotionConfig()
        self.sensors = sensors or SensorSuite()
        self._distance_m = 0.0
        self._speed_mps = 0.0
        self._last_odom_t: Optional[float] = None
        # Generic typed metric slots (range / boolean / speed); not name tables.
        self._metrics = build_rasptank_metric_store()
        self._motion: Optional[MotionController] = None
        self._deadman = None
        self._publish_fast = None
        self._idle_police = None

    def attach_runtime(self, deadman=None, publish_fast=None, idle_police=None) -> None:
        self._deadman = deadman
        self._publish_fast = publish_fast
        self._idle_police = idle_police
        if self._motion is not None:
            self._motion.attach(
                set_timed_motion_active=(
                    deadman.set_timed_motion_active if deadman is not None else None
                ),
                publish_fast=publish_fast,
            )

    def setup(self) -> bool:
        if self._ready:
            return True
        if self._init_error is not None:
            self._ensure_motion()
            return False
        from .sensors import ensure_pin_factory

        ensure_pin_factory()
        try:
            import move

            move.setup()
        except Exception as exc:
            self._init_error = str(exc)
            logger.exception(
                "motor setup failed (MQTT will log drive actions only)"
            )
            self._ensure_motion()
            return False
        self._move = move
        self._ready = True
        try:
            self._switch = _DirectLeds()
        except Exception:
            logger.exception("GPIO switches unavailable; motors stay enabled")
        try:
            import RPIservo

            _quiet_servo_prints(RPIservo.ServoCtrl)
            sc = RPIservo.ServoCtrl()
            sc.moveInit()
            # Shoulder (ch0) must rest upright; holding the arm forward stalls/overheats.
            park_arm_upright(sc, deg=arm_rest_deg())
            sc.start()
            self._sc = sc
        except Exception:
            logger.exception("servo setup unavailable; motors stay enabled")
        self._setup_lights()
        logger.info(
            "ANX hardware executor ready (motors=yes switches=%s servos=%s)",
            self._switch is not None,
            self._sc is not None,
        )
        self._ensure_motion()
        return True

    def _setup_lights(self) -> None:
        try:
            import robotLight

            ws = None
            if hasattr(robotLight, "Adeept_SPI_LedPixel"):
                ws = robotLight.Adeept_SPI_LedPixel(16, 255)
                if hasattr(ws, "check_spi_state") and ws.check_spi_state() == 0:
                    ws.led_close()
                    ws = None
                elif hasattr(ws, "start"):
                    ws.start()
            if ws is None and hasattr(robotLight, "RobotWS2812"):
                ws = robotLight.RobotWS2812()
                if hasattr(ws, "start"):
                    ws.start()
            if ws is not None:
                self._ws2812 = ws
                self._ws2812_ready = True
                if hasattr(ws, "breath"):
                    ws.breath(70, 70, 255)
        except Exception as exc:
            logger.warning("WS2812 unavailable: %s", exc)
            self._ws2812_ready = False

    def _ensure_motion(self) -> MotionController:
        if self._motion is not None:
            return self._motion

        def drive_forward(pwm: int) -> None:
            self._direction = "forward"
            self._apply_move(pwm, 1, "mid")

        def drive_backward(pwm: int) -> None:
            self._direction = "backward"
            self._apply_move(pwm, -1, "no")

        def spin_left(pwm: int) -> None:
            self._turn = "left"
            self._apply_move(pwm, 1, "left")

        def spin_right(pwm: int) -> None:
            self._turn = "right"
            self._apply_move(pwm, 1, "right")

        def motor_stop() -> None:
            self._direction = "no"
            self._turn = "no"
            self._speed_mps = 0.0
            if self._move is not None:
                self._move.motorStop()

        self._motion = MotionController(
            drive_forward=drive_forward,
            drive_backward=drive_backward,
            spin_left=spin_left,
            spin_right=spin_right,
            motor_stop=motor_stop,
            get_pwm=lambda: self._speed,
            read_ultrasonic_mm=self.sensors.ultrasonic_mm,
            set_speed_mps=self._set_speed_mps,
            accumulate_distance=self._accumulate_distance,
            set_timed_motion_active=(
                self._deadman.set_timed_motion_active if self._deadman is not None else None
            ),
            publish_fast=self._publish_fast,
            track_width_m=self._motion_cfg.track_width_m,
            speed_at_full_pwm_mps=self._motion_cfg.speed_at_full_pwm_mps,
            obstacle_stop_mm=self._motion_cfg.obstacle_stop_mm,
        )
        return self._motion

    def _apply_move(self, pwm: int, direction: int, turn: str) -> None:
        if self._move is not None:
            self._move.move(pwm, direction, turn)
        self._speed_mps = speed_mps(pwm, self._motion_cfg.speed_at_full_pwm_mps)
        self._last_odom_t = time.monotonic()

    def _set_speed_mps(self, v: float) -> None:
        self._speed_mps = float(v)
        if v == 0.0:
            self._direction = "no"
            self._turn = "no"

    def _accumulate_distance(self, v: float, dt: float) -> None:
        if dt > 0 and v:
            self._distance_m += abs(v) * dt

    def _tick_odometry(self) -> None:
        now = time.monotonic()
        if self._last_odom_t is not None and self._speed_mps and self._direction != "no":
            # Only integrate continuous RC drive here; timed motion accumulates itself.
            if self._motion is None or not self._motion.active:
                self._distance_m += abs(self._speed_mps) * (now - self._last_odom_t)
        self._last_odom_t = now

    def __call__(self, action: str, value: Any = None, steps: Any = None) -> None:
        # Dry-run / live: update typed metric slots from action bindings.
        # Hardware servo positions override range slots in _servo_telemetry.
        self._metrics.apply_action(action)
        if self._idle_police is not None:
            if action == "police":
                self._idle_police.note_police_command(True)
            elif action == "police_off":
                self._idle_police.note_police_command(False)
            elif action not in (
                "DS",
                "TS",
                "stop",
                "allStop",
                "armStop",
                "handStop",
                "LRstop",
                "GLstop",
                "UDstop",
            ):
                # Failsafe stops must not look like remote control (would cancel idle lights).
                self._idle_police.note_control()
        if action in (
            "turn_left_90",
            "turn_right_90",
            "drive_cm",
            "drive_sequence",
            "stop",
            "police",
            "police_off",
        ):
            self._handle_overlay_action(action, value, steps)
            return

        if not self._ready and not self.setup():
            logger.info("robot action %s value=%s (no hardware)", action, value)
            if action in ("forward", "backward", "left", "right"):
                # Still track open-loop speed for dry-run telemetry.
                self._update_drive_state(action)
            elif action in ("DS", "TS"):
                self._speed_mps = 0.0
                if action == "DS":
                    self._direction = "no"
                if action == "TS":
                    self._turn = "no"
            elif action == "wsB":
                self._set_pwm(value)
            return

        move = self._move
        switch = self._switch
        sc = self._sc
        if move is None:
            logger.info("robot action %s value=%s (no motors)", action, value)
            return

        if action == "wsB":
            self._set_pwm(value)
            return

        if action == "forward":
            self._direction = "forward"
            move.move(self._speed, 1, "mid")
            self._speed_mps = speed_mps(self._speed, self._motion_cfg.speed_at_full_pwm_mps)
            self._last_odom_t = time.monotonic()
        elif action == "backward":
            self._direction = "backward"
            move.move(self._speed, -1, "no")
            self._speed_mps = speed_mps(self._speed, self._motion_cfg.speed_at_full_pwm_mps)
            self._last_odom_t = time.monotonic()
        elif action == "DS":
            self._direction = "no"
            self._speed_mps = 0.0 if self._turn == "no" else self._speed_mps
            if self._turn == "no":
                move.motorStop()
                self._speed_mps = 0.0
        elif action == "left":
            self._turn = "left"
            move.move(self._speed, 1, "left")
            self._speed_mps = speed_mps(self._speed, self._motion_cfg.speed_at_full_pwm_mps)
        elif action == "right":
            self._turn = "right"
            move.move(self._speed, 1, "right")
            self._speed_mps = speed_mps(self._speed, self._motion_cfg.speed_at_full_pwm_mps)
        elif action == "TS":
            self._turn = "no"
            if self._direction == "no":
                move.motorStop()
                self._speed_mps = 0.0
        elif sc is None and action in (
            "armUp",
            "armDown",
            "armStop",
            "handUp",
            "handDown",
            "handStop",
            "lookleft",
            "lookright",
            "LRstop",
            "grab",
            "loose",
            "GLstop",
            "up",
            "down",
            "UDstop",
            "home",
        ):
            logger.info("robot action %s (no servos)", action)
        elif action == "armUp":
            sc.singleServo(0, 1, 2)
        elif action == "armDown":
            sc.singleServo(0, -1, 2)
        elif action == "armStop":
            sc.stopWiggle()
        elif action == "handUp":
            sc.singleServo(1, -1, 2)
        elif action == "handDown":
            sc.singleServo(1, 1, 2)
        elif action == "handStop":
            sc.stopWiggle()
        elif action == "lookleft":
            sc.singleServo(2, 1, 2)
        elif action == "lookright":
            sc.singleServo(2, -1, 2)
        elif action == "LRstop":
            sc.stopWiggle()
        elif action == "grab":
            sc.singleServo(3, 1, 2)
        elif action == "loose":
            sc.singleServo(3, -1, 2)
        elif action == "GLstop":
            sc.stopWiggle()
        elif action == "up":
            sc.singleServo(4, -1, 1)
        elif action == "down":
            sc.singleServo(4, 1, 1)
        elif action == "UDstop":
            sc.stopWiggle()
        elif action == "home":
            # moveServoInit expects a list of channel IDs.
            sc.moveServoInit(list(range(5)))
            park_arm_upright(sc, deg=arm_rest_deg())
        elif switch is None and action.startswith("Switch_"):
            logger.info("robot action %s (no GPIO switches)", action)
        elif action == "Switch_1_on":
            switch.switch(1, 1)
        elif action == "Switch_1_off":
            switch.switch(1, 0)
        elif action == "Switch_2_on":
            switch.switch(2, 1)
        elif action == "Switch_2_off":
            switch.switch(2, 0)
        elif action == "Switch_3_on":
            switch.switch(3, 1)
        elif action == "Switch_3_off":
            switch.switch(3, 0)
        else:
            logger.debug("hardware: unhandled action %s", action)

    def _update_drive_state(self, action: str) -> None:
        if action == "forward":
            self._direction = "forward"
            self._speed_mps = speed_mps(self._speed, self._motion_cfg.speed_at_full_pwm_mps)
        elif action == "backward":
            self._direction = "backward"
            self._speed_mps = speed_mps(self._speed, self._motion_cfg.speed_at_full_pwm_mps)
        elif action == "left":
            self._turn = "left"
            self._speed_mps = speed_mps(self._speed, self._motion_cfg.speed_at_full_pwm_mps)
        elif action == "right":
            self._turn = "right"
            self._speed_mps = speed_mps(self._speed, self._motion_cfg.speed_at_full_pwm_mps)
        self._last_odom_t = time.monotonic()

    def _set_pwm(self, value: Any) -> None:
        try:
            self._speed = max(0, min(100, int(value)))
        except (TypeError, ValueError):
            pass

    def _handle_overlay_action(self, action: str, value: Any, steps: Any) -> None:
        # Ensure motion exists even without GPIO (dry-run / tests).
        if not self._ready:
            self.setup()
        motion = self._ensure_motion()
        if action == "stop":
            motion.stop()
            return
        if action == "police":
            self._police_on()
            return
        if action == "police_off":
            self._police_off()
            return
        if action == "turn_left_90":
            motion.start_turn_left_90()
            return
        if action == "turn_right_90":
            motion.start_turn_right_90()
            return
        if action == "drive_cm":
            motion.start_drive_cm(value)
            return
        if action == "drive_sequence":
            motion.start_sequence(steps if isinstance(steps, list) else None)
            return

    def _police_on(self) -> None:
        if not self._ws2812_ready:
            self._setup_lights()
        if self._ws2812 is not None and hasattr(self._ws2812, "police"):
            try:
                self._ws2812.police()
                return
            except Exception as exc:
                logger.debug("police failed: %s", exc)
        logger.info("robot action police (no WS2812)")

    def _police_off(self) -> None:
        if self._ws2812 is not None:
            try:
                if hasattr(self._ws2812, "breath"):
                    self._ws2812.breath(70, 70, 255)
                elif hasattr(self._ws2812, "pause"):
                    self._ws2812.pause()
                return
            except Exception as exc:
                logger.debug("police_off failed: %s", exc)
        logger.info("robot action police_off (no WS2812)")

    def set_idle_police(self, active: bool) -> None:
        """Overlay idle indicator — same LED modes as police / police_off."""
        if active:
            self._police_on()
        else:
            self._police_off()

    def _sample_hardware_servos(self) -> None:
        """When the HAT is live, range slots track nowPos instead of dry-run nudges."""
        if self._sc is None:
            return
        pos = getattr(self._sc, "nowPos", None)
        if pos is None:
            return
        ids = (SERVO_ARM, SERVO_HAND, SERVO_LOOK, SERVO_GRAB, SERVO_CAMERA)
        for i, metric_id in enumerate(ids):
            if i >= len(pos):
                break
            try:
                self._metrics.set_number(metric_id, float(pos[i]))
            except (TypeError, ValueError):
                pass

    def odometry_state(self) -> dict[str, Any]:
        self._tick_odometry()
        drive = "stop"
        if self._turn in ("left", "right"):
            drive = self._turn
        elif self._direction == "forward":
            drive = "forward"
        elif self._direction == "backward":
            drive = "backward"
        left_mps, right_mps = side_speeds_mps(
            self._speed if drive != "stop" else 0,
            self._direction,
            self._turn,
            self._motion_cfg.speed_at_full_pwm_mps,
        )
        if drive == "stop":
            left_mps = 0.0
            right_mps = 0.0
        self._metrics.set_number(SPEED_LEFT, left_mps)
        self._metrics.set_number(SPEED_RIGHT, right_mps)
        self._sample_hardware_servos()
        published = self._metrics.publish_map()
        state = {
            "speed_mps": self._speed_mps if drive != "stop" else 0.0,
            "distance_m": self._distance_m,
            "odometry_source": "open_loop_pwm",
            "speed_setting": self._speed,
            "drive_direction": drive,
            "motor_left_speed": self._speed if left_mps else 0,
            "motor_right_speed": self._speed if right_mps else 0,
        }
        state.update(published)
        return state


def build_sample_fn(executor: Optional[HardwareExecutor] = None):
    """Telemetry sample using vendor info.py + overlay sensors / odometry."""

    hw = executor

    def sample(control_source: str, deadman_trips: int) -> dict:
        from .telemetry import build_telemetry

        host: dict = {}
        try:
            import info

            host = {
                "cpu_temp_c": float(info.get_cpu_tempfunc()),
                "cpu_percent": float(info.get_cpu_use()),
                "ram_percent": float(info.get_ram_info()),
            }
        except Exception:
            host = {}

        sensors: dict = {}
        state: dict = {}
        if hw is not None:
            try:
                sensors = hw.sensors.sample()
            except Exception:
                sensors = {}
            try:
                state = hw.odometry_state()
            except Exception:
                state = {"odometry_source": "open_loop_pwm"}
        else:
            state = {"odometry_source": "open_loop_pwm"}

        return build_telemetry(
            control_source=control_source,
            deadman_trips=deadman_trips,
            host=host,
            sensors=sensors,
            state=state,
        )

    return sample
