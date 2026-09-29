# RaspTank / robot MQTT controller topic contract

Virtual controllers with `transport: mqtt` publish state and accept actions over MQTT.
The ANX sidecar overlay (`robot_control/anx-robot/`) also accepts drive commands on `{prefix}/cmd`.

Default topic prefix: `rasptank` (`ANX_TOPIC_PREFIX`).

## Topics

| Direction | Topic | Payload |
|-----------|--------|---------|
| Clients → sidecar | `{prefix}/cmd` | JSON `{ "action": "<name>", "value"?: any, "steps"?: [...] }` |
| Clients → sidecar | `{prefix}/controller/{vcId}` | `ControllerStateMessage` JSON (axes, buttons, timestamp). Deduplicated / rate-limited ~30 Hz. |
| Asset-node → sidecar | `{prefix}/node_heartbeat` | Any payload; refreshes node-liveness for the overlay deadman. |
| Sidecar → bus | `{prefix}/telemetry` | Full robot telemetry ~1 Hz (battery, range mm, speed, distance, lines, host). |
| Sidecar → bus | `{prefix}/telemetry_fast` | Range-only JSON `{"ultrasonic_mm","distance_mm"}` ~20 Hz (for node guards). |
| Sidecar → bus | `{prefix}/availability` | `online` / `offline` (retained). |
| Clients → asset (optional) | mapped action `mqtt.topic` | action `mqtt.payload` (string or JSON) on button press |

## Command actions (`{prefix}/cmd`)

| action | value / steps | Notes |
|--------|----------------|-------|
| `forward` / `backward` / `left` / `right` | — | Continuous RC drive (vendor `move`). |
| `DS` / `TS` | — | Stop drive / stop turn. |
| `wsB` | PWM 0–100 | Speed setting for subsequent drive. |
| `turn_left_90` / `turn_right_90` | — | Timed counter-rotate; cancels any active timed motion. |
| `drive_cm` | centimetres (number) | Timed open-loop straight drive; negative = backward. |
| `drive_sequence` | `steps`: ordered `{action,value?}[]` | Default: 100 cm → right 90° → 200 cm → left 90°. |
| `stop` / `allStop` | — | Idempotent full stop (cancels timed motion). |
| `police` / `police_off` | — | Vendor WS2812 police / breath via overlay (no vendor server copy). |
| Arm / camera / switch | as before | `armUp`, `grab`, `Switch_1_on`, … |

While a timed motion runs, the overlay polls ultrasonic ~20 Hz. If range `<` obstacle threshold (default **100 mm**, `ANX_OBSTACLE_STOP_MM`), motors stop and local `speed_mps` is set to 0.

## Telemetry fields (selected)

| Field | Meaning |
|-------|---------|
| `battery_voltage_v` / `battery_percent` | ADS7830 ch0 (`raw/65535*8.4`, 6.0–8.4 V → %). |
| `ultrasonic_mm` / `distance_mm` | Front ultrasonic (GPIO 23/24), millimetres. |
| `ultrasonic_distance_cm` | Same range in cm (legacy). |
| `speed_mps` / `distance_m` | Open-loop PWM odometry. |
| `odometry_source` | Always `open_loop_pwm`. |
| `line_left` / `line_middle` / `line_right` | IR line sensors (GPIO 22 / 27 / 17). |

Overlay odometry defaults (not vendor): `wheelDiameterM=0.045`, `trackWidthM=0.12`, `speedAtFullPwmMps=0.35`. Env: `ANX_WHEEL_DIAMETER_M`, `ANX_TRACK_WIDTH_M`, `ANX_SPEED_AT_FULL_PWM_MPS`.

## Deadman / node heartbeat

- Legacy quiet-MQTT deadman (`ANX_DEADMAN_MS`, default 500) does **not** cut an active timed `drive_cm` / turn / sequence.
- Prefer asset-node heartbeats on `{prefix}/node_heartbeat` (or `anx_bridge.poke_node_heartbeat()`). While heartbeats are fresh (`ANX_NODE_HEARTBEAT_MS`, default 2000), the overlay deadman does not stop motion — the **node owns session timers**.
- If heartbeats were seen and then stop, the overlay failsafe stops motion.
- If the sidecar never sees node heartbeats, only the gated quiet-MQTT deadman applies.

## ControllerStateMessage (shape)

```json
{
  "vcId": "xbox_mqtt",
  "axes": { "lx": 0.0, "ly": 0.0, "rx": 0.0, "ry": 0.0 },
  "buttons": { "a": false, "b": false, "lb": false, "rb": false },
  "ts": 1710000000000
}
```

Axis names and button ids follow the preset profile (e.g. `xbox_mqtt` clones Xbox layout).

## Presets

- **PIR-01:** default `xbox_uinput` (local uinput); optional `xbox_mqtt`.
- **rasptank-mqtt:** default `xbox_mqtt`; keyboard WASD fallback.

Firmware should subscribe to `{prefix}/controller/{vcId}` and map axes/buttons to motors/servos. Do not require region round-trips on the control path.
