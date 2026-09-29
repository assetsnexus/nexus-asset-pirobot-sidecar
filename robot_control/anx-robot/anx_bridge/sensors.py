"""GPIO / I2C sensor sampling for the overlay (imports vendor-style libs; no vendor edits).

Ultrasonic: GPIO 23 trigger / 24 echo, max 2 m → millimetres, timed from lgpio echo edges.
Battery: ADS7830 I2C 0x48 ch0, volts = raw/65535*8.4.
Line IR: GPIO 17 (right) / 27 (middle) / 22 (left).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def ensure_pin_factory() -> None:
    """Open the header gpiochip. Pi 5 kernels alias it to gpiochip0; older ones use gpiochip4."""
    if getattr(ensure_pin_factory, "done", False):
        return
    try:
        from gpiozero import Device
        from gpiozero.pins.lgpio import LGPIOFactory

        chip = 4 if os.path.exists("/dev/gpiochip4") else 0
        Device.pin_factory = LGPIOFactory(chip=chip)
        logger.info("gpiozero pin factory lgpio chip=%s", chip)
    except Exception as exc:
        logger.warning("gpiozero pin factory unavailable: %s", exc)
    ensure_pin_factory.done = True

ULTRA_TRIGGER = 23
ULTRA_ECHO = 24
ULTRA_MAX_M = 2.0
# Round-trip speed of sound: 343 m/s → 0.1715 mm per echo microsecond.
_ULTRA_MM_PER_US = 0.1715
_ULTRA_MIN_PULSE_US = 80

LINE_LEFT_GPIO = 22
LINE_MIDDLE_GPIO = 27
LINE_RIGHT_GPIO = 17

BATTERY_I2C_ADDR = 0x48
BATTERY_FULL_V = 8.4
BATTERY_EMPTY_V = 6.0


class _HcSr04:
    """HC-SR04 range from lgpio echo edges.

    gpiozero's DistanceSensor warns unless the pin factory is pigpio. pigpio
    times pins by DMA on the old SoC GPIO block and does not run on the Pi 5
    RP1. lgpio timestamps the echo edges from the kernel, which is the timed
    path on this board.
    """

    def __init__(self, trigger: int, echo: int, max_m: float) -> None:
        import lgpio

        self._lgpio = lgpio
        chip = 4 if os.path.exists("/dev/gpiochip4") else 0
        self._handle = lgpio.gpiochip_open(chip)
        if self._handle < 0:
            raise RuntimeError(f"gpiochip_open({chip}) failed: {self._handle}")
        lgpio.gpio_claim_output(self._handle, trigger, 0)
        lgpio.gpio_claim_alert(self._handle, echo, lgpio.BOTH_EDGES)
        self._trigger = trigger
        self._max_mm = max_m * 1000.0
        self._timeout_s = (self._max_mm / _ULTRA_MM_PER_US) / 1_000_000.0 + 0.02
        self._lock = threading.Lock()
        self._measure = threading.Lock()
        self._done = threading.Event()
        self._armed = False
        self._rise_us: Optional[int] = None
        self._width_us: Optional[int] = None
        self._callback = lgpio.callback(self._handle, echo, lgpio.BOTH_EDGES, self._on_edge)

    def _on_edge(self, _chip: int, _gpio: int, level: int, tick: int) -> None:
        if level == 2:
            return
        with self._lock:
            if not self._armed:
                return
            if level == 1 and self._rise_us is None:
                self._rise_us = tick
                return
            if level == 0 and self._rise_us is not None:
                width = (tick - self._rise_us) & 0xFFFFFFFF
                self._rise_us = None
                if width < _ULTRA_MIN_PULSE_US:
                    return
                self._width_us = width
                self._armed = False
                self._done.set()

    def distance_mm(self) -> Optional[float]:
        with self._measure:
            return self._read_mm()

    def _read_mm(self) -> Optional[float]:
        with self._lock:
            self._rise_us = None
            self._width_us = None
            self._armed = True
            self._done.clear()
        lg = self._lgpio
        lg.gpio_write(self._handle, self._trigger, 0)
        time.sleep(2e-6)
        lg.gpio_write(self._handle, self._trigger, 1)
        time.sleep(10e-6)
        lg.gpio_write(self._handle, self._trigger, 0)
        if not self._done.wait(self._timeout_s):
            with self._lock:
                self._armed = False
            return self._max_mm
        width = self._width_us
        if width is None:
            return None
        return min(self._max_mm, width * _ULTRA_MM_PER_US)


class SensorSuite:
    """Best-effort sensors; each channel degrades independently when hardware is absent."""

    def __init__(
        self,
        *,
        ultrasonic_mm_fn: Optional[Callable[[], Optional[float]]] = None,
        battery_fn: Optional[Callable[[], tuple[Optional[float], Optional[float]]]] = None,
        line_fn: Optional[Callable[[], tuple[Optional[int], Optional[int], Optional[int]]]] = None,
    ) -> None:
        self._ultrasonic_mm_fn = ultrasonic_mm_fn
        self._battery_fn = battery_fn
        self._line_fn = line_fn
        self._ultra = None
        self._battery_chan = None
        self._line_left = None
        self._line_middle = None
        self._line_right = None
        self._init_attempted = False

    def setup(self) -> None:
        if self._init_attempted:
            return
        self._init_attempted = True
        ensure_pin_factory()
        if self._ultrasonic_mm_fn is None:
            self._setup_ultrasonic()
        if self._battery_fn is None:
            self._setup_battery()
        if self._line_fn is None:
            self._setup_line()

    def _setup_ultrasonic(self) -> None:
        try:
            self._ultra = _HcSr04(ULTRA_TRIGGER, ULTRA_ECHO, ULTRA_MAX_M)
            logger.info(
                "ultrasonic HC-SR04 trigger=%s echo=%s lgpio edge timing",
                ULTRA_TRIGGER,
                ULTRA_ECHO,
            )
        except Exception as exc:
            logger.debug("ultrasonic unavailable: %s", exc)

    def _setup_battery(self) -> None:
        try:
            import board
            import adafruit_ads7830.ads7830 as ADC
            from adafruit_ads7830.analog_in import AnalogIn

            i2c = board.I2C()
            adc = ADC.ADS7830(i2c, BATTERY_I2C_ADDR)
            self._battery_chan = AnalogIn(adc, 0)
        except Exception as exc:
            logger.debug("battery ADC unavailable: %s", exc)

    def _setup_line(self) -> None:
        try:
            from gpiozero import InputDevice

            self._line_left = InputDevice(pin=LINE_LEFT_GPIO)
            self._line_middle = InputDevice(pin=LINE_MIDDLE_GPIO)
            self._line_right = InputDevice(pin=LINE_RIGHT_GPIO)
        except Exception as exc:
            logger.debug("line sensors unavailable: %s", exc)

    def ultrasonic_mm(self) -> Optional[float]:
        if self._ultrasonic_mm_fn is not None:
            return self._ultrasonic_mm_fn()
        self.setup()
        if self._ultra is None:
            return None
        try:
            return self._ultra.distance_mm()
        except Exception:
            return None

    def battery(self) -> tuple[Optional[float], Optional[float]]:
        if self._battery_fn is not None:
            return self._battery_fn()
        self.setup()
        if self._battery_chan is None:
            return None, None
        try:
            raw = float(self._battery_chan.value)
            volts = raw / 65535.0 * BATTERY_FULL_V
            span = BATTERY_FULL_V - BATTERY_EMPTY_V
            pct = max(0.0, min(100.0, (volts - BATTERY_EMPTY_V) / span * 100.0))
            return volts, pct
        except Exception:
            return None, None

    def line(self) -> tuple[Optional[int], Optional[int], Optional[int]]:
        if self._line_fn is not None:
            return self._line_fn()
        self.setup()
        try:
            left = int(self._line_left.value) if self._line_left is not None else None
            mid = int(self._line_middle.value) if self._line_middle is not None else None
            right = int(self._line_right.value) if self._line_right is not None else None
            return left, mid, right
        except Exception:
            return None, None, None

    def sample(self) -> dict[str, Any]:
        volts, pct = self.battery()
        ultra = self.ultrasonic_mm()
        left, mid, right = self.line()
        return {
            "battery_voltage_v": volts,
            "battery_percent": pct,
            "ultrasonic_mm": ultra,
            "distance_mm": ultra,
            "ultrasonic_distance_cm": (ultra / 10.0) if ultra is not None else None,
            "line_left": left,
            "line_middle": mid,
            "line_right": right,
        }
