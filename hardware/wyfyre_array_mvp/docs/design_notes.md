# Design Notes

## Transport decision

MVP transport is split into two hops:

- `ESP32_B -> ESP32_A` over ESP-NOW (low-latency local wireless, no router needed)
- `ESP32_A -> host PC` over USB serial JSON lines

Why this is the clean default after UART0 conflict review:

- Node A keeps UART0 dedicated to USB host link.
- Node A uses only UART1/UART2 for radar, avoiding USB contention.
- Node B can still run 3 sensors with UART0/1/2 and forward everything upstream.
- Host app only needs one port (`Node A`).

UDP receiver is still available in host code as an optional path for later networking experiments.

## Fusion scope

This is black-box target fusion of LD2450 track outputs, not beamforming or IQ processing.

Pipeline:

1. Parse per-sensor targets on ESP32 nodes.
2. Transform local coordinates into one global bar frame.
3. Cluster global detections by configurable radius.
4. Maintain short-lived tracks for persistence.
5. Score fused targets by weighted confidence factors.
6. Build temporally smoothed confidence heatmap.

## Confidence factors

- Sensor agreement (how many sensors support one fused point)
- Track persistence over recent frames
- Speed term (de-emphasize unstable/high-jitter outliers)
- Angle consistency inside each cluster
- Sensor-specific weights
- LD2450 distance-resolution term

All weights and grid parameters are in `config/runtime.json`.

## CV alignment framework

- `python/webcam_stub.py` provides optional frame capture hook.
- `python/dataset_logger.py` writes synchronized JSONL records and optional heatmap NPY files.
- `python/cv_feedback.py` is the extension point where CV detections can adjust confidence in future iterations.

Expected future workflow:

1. Mount webcam rigidly to the same bar frame.
2. Collect synchronized radar + webcam sessions.
3. Estimate camera<->bar extrinsics.
4. Train calibration/occupancy model and feed predictions back through `CvFeedbackAdapter`.
