"""Battery voltage → SoC %, estimated current (A) and load watts (W).

HAT has ADS7830 pack voltage only (no shunt). Current is estimated from sag:

    I ≈ max(0, (V_rest - V) / ESR)
    P ≈ V * I

``V_rest`` tracks the recent unloaded pack voltage (rises with V, decays slowly).
History for Hard Ware sparkline is **amperes** over the last 7 s.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, List, Optional, Tuple

logger = logging.getLogger(__name__)

_HISTORY_SEC = 7.0
_SAMPLE_HZ = 5.0  # ~35 points in 7s

_lock = threading.Lock()
_adc_chan0: Any = None
_adc_tried = False
_last_sample: Optional["PowerSample"] = None
_history: Deque[Tuple[float, float]] = deque()  # (ts, amps)
_sampler_started = False
_v_rest: Optional[float] = None


@dataclass(frozen=True)
class PowerSample:
    volts: float
    percent: float
    current_a_est: float
    power_w_est: float
    volts_rest: float
    level: str  # ok | warn | crit | unknown
    ts: float


# Back-compat alias
BattSample = PowerSample


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("%s=%r invalid; using %s", name, raw, default)
        return default


def batt_v_empty() -> float:
    return _env_float("ANX_BATT_V_EMPTY", 6.0)


def batt_v_full() -> float:
    full = _env_float("ANX_BATT_V_FULL", 8.4)
    empty = batt_v_empty()
    return full if full > empty + 0.1 else empty + 0.1


def battery_esr_ohm() -> float:
    return max(0.01, _env_float("ANX_BATTERY_ESR_OHM", 0.15))


def power_warn_w() -> float:
    return max(0.1, _env_float("ANX_POWER_WARN_W", 6.0))


def power_crit_w() -> float:
    return max(power_warn_w() + 0.1, _env_float("ANX_POWER_CRIT_W", 12.0))


def current_warn_a() -> float:
    return max(0.05, _env_float("ANX_CURRENT_WARN_A", 1.5))


def current_crit_a() -> float:
    return max(current_warn_a() + 0.05, _env_float("ANX_CURRENT_CRIT_A", 3.0))


def volts_to_percent(volts: float) -> float:
    empty = batt_v_empty()
    full = batt_v_full()
    pct = (float(volts) - empty) / (full - empty) * 100.0
    return round(max(0.0, min(100.0, pct)), 1)


def _classify_power(watts: float) -> str:
    if watts >= power_crit_w():
        return "crit"
    if watts >= power_warn_w():
        return "warn"
    return "ok"


def _classify_current(amps: float) -> str:
    if amps >= current_crit_a():
        return "crit"
    if amps >= current_warn_a():
        return "warn"
    return "ok"


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
            "ADS7830 battery ADC ready — I≈(Vrest-V)/ESR "
            "(esr=%.3fΩ warn=%.1fW/%.1fA crit=%.1fW/%.1fA)",
            battery_esr_ohm(),
            power_warn_w(),
            current_warn_a(),
            power_crit_w(),
            current_crit_a(),
        )
    except Exception as exc:
        _adc_chan0 = None
        logger.info("battery ADC unavailable (%s) — power sense disabled", exc)


def _read_volts_unlocked() -> Optional[float]:
    _try_init_ads7830()
    if _adc_chan0 is None:
        return None
    try:
        return float(_adc_chan0.value) / 65535.0 * 8.4
    except Exception:
        logger.debug("battery read failed", exc_info=True)
        return None


def _update_v_rest_unlocked(volts: float) -> float:
    """Track unloaded pack voltage: climb immediately, decay slowly on sag."""
    global _v_rest
    if _v_rest is None:
        _v_rest = float(volts)
        return _v_rest
    if volts >= _v_rest:
        _v_rest = float(volts)
    else:
        # Slow recovery of resting estimate while under load (~2%/sample at 5 Hz → ~10s).
        _v_rest = 0.98 * _v_rest + 0.02 * float(volts)
    return _v_rest


def _estimate(volts: float, volts_rest: float, esr_ohm: float) -> Tuple[float, float]:
    sag = max(0.0, float(volts_rest) - float(volts))
    amps = sag / max(0.01, float(esr_ohm))
    watts = float(volts) * amps
    return round(amps, 3), round(watts, 2)


def _trim_history_unlocked(now: float) -> None:
    cutoff = now - _HISTORY_SEC
    while _history and _history[0][0] < cutoff:
        _history.popleft()


def sample_power() -> Optional[PowerSample]:
    """Read pack voltage → SoC, estimated A/W; append amps to 7s history."""
    global _last_sample
    with _lock:
        volts = _read_volts_unlocked()
        if volts is None:
            return None
        now = time.time()
        v_rest = _update_v_rest_unlocked(volts)
        amps, watts = _estimate(volts, v_rest, battery_esr_ohm())
        pct = volts_to_percent(volts)
        # Chip uses power level; sparkline uses current level — expose power as primary.
        sample = PowerSample(
            volts=round(volts, 3),
            percent=pct,
            current_a_est=amps,
            power_w_est=watts,
            volts_rest=round(v_rest, 3),
            level=_classify_power(watts),
            ts=now,
        )
        _last_sample = sample
        _history.append((now, amps))
        _trim_history_unlocked(now)
        return sample


def sample_battery() -> Optional[PowerSample]:
    return sample_power()


def estimate_from_volts(
    volts: float,
    *,
    volts_rest: float | None = None,
    esr_ohm: float | None = None,
    **_kwargs: Any,
) -> PowerSample:
    """Pure helper for tests: volts (+ optional rest/ESR) → PowerSample."""
    v = float(volts)
    rest = float(volts_rest) if volts_rest is not None else v
    esr = float(esr_ohm) if esr_ohm is not None else battery_esr_ohm()
    amps, watts = _estimate(v, rest, esr)
    return PowerSample(
        volts=round(v, 3),
        percent=volts_to_percent(v),
        current_a_est=amps,
        power_w_est=watts,
        volts_rest=round(rest, 3),
        level=_classify_power(watts),
        ts=time.time(),
    )


def battery_volts() -> Optional[float]:
    s = sample_power()
    return None if s is None else s.volts


def last_sample() -> Optional[PowerSample]:
    with _lock:
        return _last_sample


def history_series(*, window_s: float = _HISTORY_SEC) -> List[dict]:
    """Points for sparkline: ``[{t, a}, ...]`` (amperes) within the window.

    ``t`` is **absolute epoch milliseconds** (same shape as the latency chart)
    so the canvas can map the last 7 s without relative/absolute mix-ups.
    """
    with _lock:
        now = time.time()
        _trim_history_unlocked(now)
        cutoff = now - max(1.0, float(window_s))
        return [
            {"t": int(round(ts * 1000.0)), "a": float(amps)}
            for ts, amps in _history
            if ts >= cutoff
        ]


def power_payload() -> dict:
    """JSON for ``GET /anx/power`` — load W + current A series (last 7s)."""
    s = sample_power()
    if s is None:
        return {
            "ok": False,
            "available": False,
            "error": "no battery ADC (ADS7830)",
            "series": [],
            "window_s": _HISTORY_SEC,
        }
    series = history_series()
    return {
        "ok": True,
        "available": True,
        "method": "voltage_sag",
        "volts": s.volts,
        "volts_rest": s.volts_rest,
        "percent": s.percent,
        "current_a_est": s.current_a_est,
        "power_w_est": s.power_w_est,
        "level": s.level,
        "level_current": _classify_current(s.current_a_est),
        "esr_ohm": battery_esr_ohm(),
        "warn_w": power_warn_w(),
        "crit_w": power_crit_w(),
        "warn_a": current_warn_a(),
        "crit_a": current_crit_a(),
        "series": series,
        "window_s": _HISTORY_SEC,
        "ts": s.ts,
    }


def start_power_sampler(*, interval_s: float | None = None) -> None:
    """Background sampler (~5 Hz) for the 7s current sparkline."""
    global _sampler_started
    period = 1.0 / _SAMPLE_HZ if interval_s is None else max(0.1, float(interval_s))
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
            time.sleep(period)

    threading.Thread(target=_loop, name="anx-power-sense", daemon=True).start()
    logger.info("power sampler started (interval=%.2fs, history=%.0fs, series=A)", period, _HISTORY_SEC)
