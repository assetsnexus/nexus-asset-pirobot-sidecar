"""Optional battery / power samples for the stock status chips.

Adeept Robot HAT exposes ADS7830 battery **voltage** (example
``08_Battrey_level.py``). There is no onboard current shunt / ``power_w`` /
``current_a`` on this tank — ANX port-io ``power_w`` metrics belong to other
assets. We surface battery volts when the ADC is present; otherwise omit.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_adc_chan0: Any = None
_adc_tried = False


def _try_init_ads7830() -> None:
    global _adc_chan0, _adc_tried
    if _adc_tried:
        return
    _adc_tried = True
    try:
        import board
        import adafruit_ads7830.ads7830 as ADC
        from adafruit_ads7830.analog_in import AnalogIn

        i2c = board.I2C()
        adc = ADC.ADS7830(i2c, 0x48)
        _adc_chan0 = AnalogIn(adc, 0)
        logger.info("ADS7830 battery ADC ready (ch0 → pack voltage)")
    except Exception as exc:
        _adc_chan0 = None
        logger.info("battery ADC unavailable (%s) — no Batt chip metric", exc)


def battery_volts() -> Optional[float]:
    """Return pack voltage estimate or None if no ADC."""
    with _lock:
        _try_init_ads7830()
        if _adc_chan0 is None:
            return None
        try:
            # Vendor example: value/65535*8.4 for 2S pack full-scale.
            volts = float(_adc_chan0.value) / 65535.0 * 8.4
            return round(volts, 2)
        except Exception:
            logger.debug("battery read failed", exc_info=True)
            return None


def sysinfo_extra() -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """(battery_v, current_a, power_w) — only battery_v may be populated on HAT."""
    return battery_volts(), None, None
