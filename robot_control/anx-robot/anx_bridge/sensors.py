"""GPIO / I2C sensor sampling for the overlay (imports vendor-style libs; no vendor edits).

Ultrasonic: GPIO 23 trigger / 24 echo, max 2 m → millimetres.
Battery: ADS7830 I2C 0x48 ch0, volts = raw/65535*8.4.
Line IR: GPIO 17 (right) / 27 (middle) / 22 (left).
"""

from __future__ import annotations

import logging
import os
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

LINE_LEFT_GPIO = 22
LINE_MIDDLE_GPIO = 27
LINE_RIGHT_GPIO = 17

BATTERY_I2C_ADDR = 0x48
BATTERY_FULL_V = 8.4
BATTERY_EMPTY_V = 6.0


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
            from gpiozero import DistanceSensor

            self._ultra = DistanceSensor(
                echo=ULTRA_ECHO, trigger=ULTRA_TRIGGER, max_distance=ULTRA_MAX_M
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
            # gpiozero DistanceSensor.distance is metres (0 .. max_distance).
            metres = float(self._ultra.distance)
            return metres * 1000.0
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
