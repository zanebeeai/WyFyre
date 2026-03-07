# Calibration and Testing

## 1) Sensor-by-sensor bring-up

1. Power one node and only one connected LD2450.
2. If validating `S0`/`S1`, use Node A. If validating `S2`/`S3`/`S4`, use Node B and ensure A is powered for ESP-NOW relay.
3. Run host app and select test mode `sensor_test`.
4. Set sensor focus to the expected sensor ID.
5. Verify detections move correctly in raw panel.
6. Repeat for each sensor.

## 2) Geometry sanity pass

1. Enable all 5 sensors.
2. Place one person near centerline at ~2 m.
3. In `normal` mode, verify raw detections from multiple sensors overlap around one global region.
4. If one sensor is offset, proceed to calibration.

## 3) Bias calibration flow

Use one fixed reference point (tripod marker/tape on floor):

```bash
python calibrate.py --duration 12 --x 0 --y 2000
```

What it does:

- Captures detections for `duration` seconds.
- Computes median global `(x, y)` per sensor near the expected range.
- Writes per-sensor bias into `config/calibration.json`.

## 4) Fused-system sanity mode

1. Set test mode to `fused_sanity`.
2. Walk left-to-right across the bar at 2-3 m.
3. Verify fused targets stay smooth and heatmap ridge follows trajectory.
4. Toggle single/multi mode and verify behavior.

## 5) Failure checks

- If a sensor appears dead, verify UART cross wiring and common ground first.
- If detections flicker badly, inspect power rail and reduce cable length.
- If one node is missing, check node heartbeat in host logs and WiFi association.
- If `S2-S4` disappear while `S0-S1` are fine, check ESP-NOW MAC pairing and B power.
