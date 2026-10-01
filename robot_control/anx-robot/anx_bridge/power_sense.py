"""Estimate load power from battery **voltage sag** (ADS7830 only — no shunt).

Robot HAT has voltage, not amperes. Approximates:

  I_est ≈ (V_rest - V_now) / R_esr
  P_est ≈ V_now * I_est

``R_esr`` is battery+wiring effective resistance (``ANX_BATTERY_ESR_OHM``,
default 0.15 Ω). Absolute watts are approximate; relative spikes when a servo
stalls are the useful signal for the Hard Ware badge.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_adc_chan0: Any = None
_adc_tried = False
_v_rest_ema: Optional[float] = None
_last_sample: Optional["PowerSample"] = None
_sampler_started = False


@dataclass(frozen=True)
class PowerSample:
    volts: float
    volts_rest: float
    sag_v: float
    current_a_est: float
    power_w_est: float
    level: str  # ok | warn | crit | unknown
    ts: float


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r invalid; using %s", name, raw, default)
        return default


def battery_esr_ohm() -> float:
    return max(0.02, _env_float("ANX_BATTERY_ESR_OHM", 0.15))


def power_warn_w() -> float:
    return max(0.5, _env_float("ANX_POWER_WARN_W", 6.0))


def power_crit_w() -> float:
    return max(power_warn_w() + 0.5, _env_float("ANX_POWER_CRIT_W", 12.0))


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
        logger.info(
            "ADS7830 battery ADC ready — estimating watts from voltage sag "
            "(ESR=%.3fΩ warn=%.1fW crit=%.1fW)",
            battery_esr_ohm(),
            power_warn_w(),
            power_crit_w(),
        )
    except Exception as exc:
        _adc_chan0 = None
        logger.info("battery ADC unavailable (%s) — power badge disabled", exc)


def _read_volts_unlocked() -> Optional[float]:
    _try_init_ads7830()
    if _adc_chan0 is None:
        return None
    try:
        # Vendor example: value/65535*8.4 for 2S pack full-scale.
        return float(_adc_chan0.value) / 65535.0 * 8.4
    except Exception:
        logger.debug("battery read failed", exc_info=True)
        return None


def _classify(power_w: float) -> str:
    if power_w >= power_crit_w():
        return "crit"
    if power_w >= power_warn_w():
        return "warn"
    return "ok"


def _update_rest_ema(volts: float) -> float:
    """Track near-unloaded pack voltage: rise quickly, fall slowly."""
    global _v_rest_ema
    if _v_rest_ema is None:
        _v_rest_ema = volts
        return volts
    if volts >= _v_rest_ema:
        _v_rest_ema = 0.35 * _v_rest_ema + 0.65 * volts
    else:
        # Slow decay so a stall sag stays visible against recent rest.
        _v_rest_ema = 0.995 * _v_rest_ema + 0.005 * volts
    return _v_rest_ema


def sample_power() -> Optional[PowerSample]:
    """Read voltage and estimate load watts from sag. None if no ADC."""
    global _last_sample
    with _lock:
        volts = _read_volts_unlocked()
        if volts is None:
            return None
        rest = _update_rest_ema(volts)
        sag = max(0.0, rest - volts)
        r = battery_esr_ohm()
        current = sag / r
        power = volts * current
        sample = PowerSample(
            volts=round(volts, 3),
            volts_rest=round(rest, 3),
            sag_v=round(sag, 4),
            current_a_est=round(current, 3),
            power_w_est=round(power, 2),
            level=_classify(power),
            ts=time.time(),
        )
        _last_sample = sample
        return sample


def estimate_from_volts(
    volts: float,
    *,
    volts_rest: float,
    esr_ohm: float | None = None,
) -> PowerSample:
    """Pure helper for tests / callers that already have a voltage reading."""
    r = battery_esr_ohm() if esr_ohm is None else max(0.02, float(esr_ohm))
    sag = max(0.0, float(volts_rest) - float(volts))
    current = sag / r
    power = float(volts) * current
    return PowerSample(
        volts=round(float(volts), 3),
        volts_rest=round(float(volts_rest), 3),
        sag_v=round(sag, 4),
        current_a_est=round(current, 3),
        power_w_est=round(power, 2),
        level=_classify(power),
        ts=time.time(),
    )


def battery_volts() -> Optional[float]:
    s = sample_power()
    return None if s is None else s.volts


def last_sample() -> Optional[PowerSample]:
    with _lock:
        return _last_sample


def power_payload() -> dict:
    s = sample_power()
    if s is None:
        return {
            "ok": False,
            "available": False,
            "error": "no battery ADC (ADS7830) — cannot estimate watts",
        }
    return {
        "ok": True,
        "available": True,
        "method": "voltage_sag",
        "volts": s.volts,
        "volts_rest": s.volts_rest,
        "sag_v": s.sag_v,
        "current_a_est": s.current_a_est,
        "power_w_est": s.power_w_est,
        "level": s.level,
        "warn_w": power_warn_w(),
        "crit_w": power_crit_w(),
        "esr_ohm": battery_esr_ohm(),
        "ts": s.ts,
    }


def start_power_sampler(*, interval_s: float = 0.4) -> None:
    """Background sampler so UI/get_info see fresh stall spikes."""
    global _sampler_started
    with _lock:
        if _sampler_started:
            return
        _sampler_started = True

    def _loop() -> None:
        while True:
            try:
                sample_power()
            except Exception:
                logger.debug("power sampler tick failed", exc_info=True)
            time.sleep(max(0.15, interval_s))

    threading.Thread(target=_loop, name="anx-power-sense", daemon=True).start()
    logger.info("power sag sampler started (interval=%.2fs)", interval_s)
