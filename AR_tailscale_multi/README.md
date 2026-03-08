# WyFyre AR Tailscale Multi

This variant adds multi-headset role support on top of the AR fusion bridge:

- One headset can join as `master` (board-holder).
- Any number of other headsets join as `slave`.
- Slaves render the same fused pings relative to the master using pose relaying and calibration.

Core behavior remains the same as MVP fusion:

- same serial packet intake and 5-sensor fusion math
- same confidence heatmap generation
- near-origin detection rejection (`fusion.min_valid_range_mm`)

## Files

- `server.py`: Fusion backend + role-aware websocket hub (`master` / `slave`)
- `index.html`: Multi-user XR client with role buttons and slave calibration
- `fusion.py`: MVP-equivalent fusion plus near-range rejection output for debugging
- `config/runtime.json`: includes `fusion.min_valid_range_mm`
- `config/geometry.json`: includes radar range/FOV

## Run

```bash
cd AR_tailscale_multi
pip install -r requirements.txt
python server.py
```

Expose over Tailscale (recommended for Quest):

```bash
tailscale funnel --bg 8000
tailscale funnel status
```

Open the shown `https://...ts.net` URL from each headset/browser.

## Role flow

1. On one headset, click `Join as Master`.
2. On all others, click `Join as Slave`.
3. If a second client requests master while occupied, server denies and assigns slave.

You can also preselect with query params:

- `?role=master`
- `?role=slave`

## Slave calibration (same model as original WiFyre-VR)

- Uses a fixed forced offset exactly like the original multi-user prototype flow.
- Implemented in `index.html` as:

```js
const INITIAL_RELATIVE_OFFSET = {
  position: { x: 2.0, y: 0.0, z: 0.0 },
  rotation: 0,
};
```

- Slave detections are transformed with the same broadcaster->global->receiver pipeline and this fixed offset.
- To force a different slave position relative to master, edit `INITIAL_RELATIVE_OFFSET.position`.

## Debug radar

- Dashed gray arc = near-range cutoff (`min_valid_range_mm`)
- Gray dots = rejected near detections (excluded from fusion and heatmap)
- Heatmap = inferno-style intensity over radar wedge from geometry config
