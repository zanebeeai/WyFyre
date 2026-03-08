# WyFyre AR over Tailscale

This folder is a full AR bridge that:

- reads UART detections from the 5-sensor array the same way as `hardware/wyfyre_array_mvp/python/main.py`
- runs the same fusion pipeline and confidence heatmap generation
- serves a Quest-friendly Babylon XR client
- replaces ngrok with Tailscale access (`tailscale serve` or `tailscale funnel`)

## What is implemented

- `server.py`: FastAPI + WebSocket service that streams `fusion_frame` packets
- `transport_serial.py`: serial JSON-line reader matching current host flow
- `fusion.py`: copied fusion logic (clusters, tracks, confidence, heatmap)
- `index.html`: XR scene with
  - 3D target pings at fused target positions
  - top-right confidence radar/minimap heatmap
  - yaw-only world transform (pitch ignored), per your requirement

## Setup

1) Install Python dependencies

```bash
cd AR_tailscale
pip install -r requirements.txt
```

2) Confirm serial config (default is AUTO)

- Edit `AR_tailscale/config/runtime.json` if needed.
- Most setups can keep:
  - `transport.mode = "serial"`
  - `transport.serial_fallback.ports.A = "AUTO"`

3) Start server

```bash
python server.py
```

4) Expose with Tailscale

Tailnet-only URL (recommended for private testing):

```bash
tailscale serve --bg 8000
```

Public HTTPS URL (if Quest/browser cannot join tailnet directly):

```bash
tailscale funnel --bg 8000
```

5) Open on Quest browser

- Tailnet: open your machine tailnet hostname URL from `tailscale serve status`
- Funnel: open the `https://<name>.ts.net` URL from `tailscale funnel status`

## Notes on pose model

- Sensor origin is assumed co-located with headset position.
- Sensor heading follows headset yaw.
- Headset pitch/roll are ignored for radar orientation transform.

This exactly enforces: look up/down does not tilt the sensor frame.

## Stream format (server -> client)

`fusion_frame` includes:

- `fused_targets[]`: `track_id`, `x_mm`, `y_mm`, `confidence`, `speed_cms`, etc.
- `heatmap`: `{width,height,extent,u8_b64}` where `u8_b64` is a `uint8` heatmap blob
- `status`: node/ESP-NOW and active-mask telemetry

## Troubleshooting

- If no targets appear: verify Node A COM port is free and Arduino Serial Monitor is closed.
- If only 1-2 sensors seem active: verify Node B relay and ESP-NOW pairing in firmware.
- If Quest cannot connect to private tailnet URL: use `tailscale funnel --bg 8000`.
