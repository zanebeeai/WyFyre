# WyFyre Array MVP (5x LD2450, 2x ESP32, ESP-NOW relay)

This stage uses a 5-sensor horizontal bar and solves the UART0/USB conflict by making Node A host-facing and Node B relay-facing.

- Node A (USB to PC): reads `S0,S1` on UART1/UART2
- Node B (wireless relay): reads `S2,S3,S4` on UART0/UART1/UART2
- `B -> A`: ESP-NOW packets
- `A -> PC`: JSON lines over USB serial
- PC app: fuses all 5 sensors, shows raw + fused + heatmap

## 1) Folder Map

- `firmware/esp32_array_node_a/esp32_array_node_a.ino`
- `firmware/esp32_array_node_b/esp32_array_node_b.ino`
- `python/main.py` (desktop app)
- `python/calibrate.py` (bias calibration helper)
- `config/geometry.json` (sensor offsets + enabled flags)
- `config/runtime.json` (transport/fusion/app defaults)
- `config/calibration.json` (per-sensor bias corrections)
- `docs/wiring.md`
- `docs/calibration_and_testing.md`
- `docs/design_notes.md`

## 2) Physical Topology

Default bar geometry (mm):

- `S0=-300`, `S1=-150`, `S2=0`, `S3=+150`, `S4=+300`

Node split:

- Node A: `S0,S1`
- Node B: `S2,S3,S4`

Why this split:

- Node A keeps UART0 free for USB host comms.
- Node B can use all 3 UARTs because it is not host-USB critical during runtime.

## 3) Wiring (Quick Reference)

Full details: `docs/wiring.md`

Node A:

- `S0`: GPIO16 RX, GPIO17 TX
- `S1`: GPIO18 RX, GPIO19 TX

Node B:

- `S2`: GPIO4 RX, GPIO5 TX
- `S3`: GPIO16 RX, GPIO17 TX
- `S4`: GPIO18 RX, GPIO19 TX

Always:

- Cross TX/RX per UART pair
- Common ground across both ESP32s and all LD2450 modules
- Stable power rail with enough current margin

## 4) One-Time ESP-NOW Pairing Setup

Node A and B firmware contain fixed peer MAC placeholders.

1. Flash each board once with temporary MAC-print sketch (or use current boot logs).
2. Read WiFi MAC for each board.
3. Set constants:
   - in `firmware/esp32_array_node_a/esp32_array_node_a.ino`, set `PEER_B_MAC`
   - in `firmware/esp32_array_node_b/esp32_array_node_b.ino`, set `PEER_A_MAC`
4. Reflash both boards.

If MACs are wrong, `S2-S4` will not appear at host.

## 5) Flashing Sequence (Recommended)

1. Wire Node A sensors (`S0,S1`) and USB to PC.
2. Flash `esp32_array_node_a.ino`.
3. Wire Node B sensors (`S2,S3,S4`) and flash `esp32_array_node_b.ino`.
4. Power both boards.
5. Leave only Node A connected to PC for runtime host app.

## 6) Host App Setup

From `hardware/wyfyre_array_mvp/python`:

```bash
pip install -r requirements.txt
```

Edit `config/runtime.json`:

- `transport.mode` should be `"serial"`
- `transport.serial_fallback.ports.A` should match Node A COM port (example `COM7`)

Run:

```bash
python main.py
```

## 7) Normal Operation (Start to Finish)

1. Mount bar rigidly and ensure all sensors are parallel.
2. Power Node A + Node B.
3. Start `python main.py`.
4. Confirm Node A status becomes online in UI.
5. Set fusion mode to `multi` for normal occupancy tracking.
6. Verify raw panel shows detections from `S0...S4`.
7. Verify fused panel and heatmap are stable.
8. For single-person use case, switch to `single` mode.

Command path in this architecture:

- Host sends mode command to Node A.
- Node A applies mode locally and forwards mode to Node B via ESP-NOW.

## 8) Calibration Workflow

Use one known reference point in front of the bar (example x=0 mm, y=2000 mm):

```bash
python calibrate.py --duration 12 --x 0 --y 2000
```

This updates `config/calibration.json` with per-sensor x/y biases.

## 9) Test Modes

From app controls:

- `normal`: regular fusion + heatmap
- `sensor_test`: isolate one sensor ID (`S0..S4`)
- `fused_sanity`: emphasizes target consistency checks

## 10) Dataset Logging and CV Stub

The framework is ready for aligned CV/radar capture:

- `python/dataset_logger.py` writes `radar_fusion.jsonl`
- optional heatmaps are saved as `.npy`
- `python/webcam_stub.py` can save camera frames
- `python/cv_feedback.py` is where future CV confidence correction plugs in

## 11) Troubleshooting

- Only S0/S1 appear: ESP-NOW MAC pairing or Node B power issue.
- No detections at all: check UART cross wiring and common ground.
- Flicker/noise: improve power rail stability and cable routing.
- Mode command inconsistent: resend `Set on nodes` from app; A forwards to B.

## 12) Known Limits (Current MVP)

- Command protocol is intentionally minimal (`ping`, `query_mode`, `set_mode`).
- Fusion is target-level heuristic scoring, not raw radar beamforming.
- ESP-NOW reliability depends on RF environment; keep nodes reasonably close.
