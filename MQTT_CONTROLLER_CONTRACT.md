# RaspTank / robot MQTT controller topic contract

Virtual controllers with `transport: mqtt` publish state and accept actions over MQTT.

## Topics

Prefix comes from the MQTT bus / virtual-controller `topicPrefix` (default often the asset id).

| Direction | Topic | Payload |
|-----------|--------|---------|
| Asset → clients | `{prefix}/controller/{vcId}` | `ControllerStateMessage` JSON (axes, buttons, timestamp). Deduplicated / rate-limited ~30 Hz. |
| Clients → asset (optional) | mapped action `mqtt.topic` | action `mqtt.payload` (string or JSON) on button press |

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
