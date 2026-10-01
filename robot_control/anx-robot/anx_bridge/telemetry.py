"""Telemetry payload shape published on `<prefix>/telemetry`."""

from __future__ import annotations

from typing import Any


def build_telemetry(
    *,
    control_source: str,
    deadman_trips: int,
    host: dict[str, Any] | None = None,
    sensors: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    host = host or {}
    sensors = sensors or {}
    state = state or {}
    ultra_mm = sensors.get("ultrasonic_mm")
    if ultra_mm is None:
        ultra_mm = sensors.get("distance_mm")
    ultra_cm = sensors.get("ultrasonic_distance_cm")
    if ultra_cm is None and ultra_mm is not None:
        ultra_cm = float(ultra_mm) / 10.0
    return {
        "cpu_temp_c": host.get("cpu_temp_c"),
        "gpu_temp_c": host.get("gpu_temp_c"),
        "cpu_percent": host.get("cpu_percent"),
        "ram_percent": host.get("ram_percent"),
        "swap_percent": host.get("swap_percent"),
        "battery_voltage_v": sensors.get("battery_voltage_v"),
        "battery_percent": sensors.get("battery_percent"),
        "ultrasonic_distance_cm": ultra_cm,
        "ultrasonic_mm": ultra_mm,
        "distance_mm": sensors.get("distance_mm", ultra_mm),
        "line_left": sensors.get("line_left"),
        "line_middle": sensors.get("line_middle"),
        "line_right": sensors.get("line_right"),
        "speed_mps": state.get("speed_mps", 0.0),
        "distance_m": state.get("distance_m", 0.0),
        "odometry_source": state.get("odometry_source", "open_loop_pwm"),
        "motor_left_speed": state.get("motor_left_speed", 0),
        "motor_right_speed": state.get("motor_right_speed", 0),
        "motor_left_speed_mps": state.get("motor_left_speed_mps", 0.0),
        "motor_right_speed_mps": state.get("motor_right_speed_mps", 0.0),
        "drive_direction": state.get("drive_direction", "stop"),
        "speed_setting": state.get("speed_setting", 0),
        "servo_arm_deg": state.get("servo_arm_deg"),
        "servo_hand_deg": state.get("servo_hand_deg"),
        "servo_look_deg": state.get("servo_look_deg"),
        "servo_grab_deg": state.get("servo_grab_deg"),
        "servo_camera_deg": state.get("servo_camera_deg"),
        "mode_automatic": bool(state.get("mode_automatic", False)),
        "mode_track_line": bool(state.get("mode_track_line", False)),
        "mode_steady_camera": bool(state.get("mode_steady_camera", False)),
        "mode_cvfl": bool(state.get("mode_cvfl", False)),
        "switch_1": bool(state.get("switch_1", False)),
        "switch_2": bool(state.get("switch_2", False)),
        "switch_3": bool(state.get("switch_3", False)),
        "lights_police": bool(state.get("lights_police", False)),
        "control_source": control_source,
        "deadman_trips_total": deadman_trips,
    }
