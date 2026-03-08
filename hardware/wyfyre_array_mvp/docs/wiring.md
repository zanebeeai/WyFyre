# Wiring Guide (5x LD2450, 2x ESP32, ESP-NOW uplink)

This revision avoids runtime UART0/USB conflict on Node A.

## Node split

- ESP32_A (Node A, host-connected): `S0`, `S1`
- ESP32_B (Node B, relay node): `S2`, `S3`, `S4`
- B sends radar telemetry to A over ESP-NOW.
- A outputs combined JSON stream to the PC over USB serial.

Facing outward from the bar, sensor ordering is left-to-right: `S0, S1, S2, S3, S4`.

## UART mapping (recommended)

### ESP32_A (2 sensors + USB)

- `S0` on UART2: ESP32 GPIO16 (RX) <- LD2450 TX, ESP32 GPIO17 (TX) -> LD2450 RX
- `S1` on UART1: ESP32 GPIO26 (RX) <- LD2450 TX, ESP32 GPIO25 (TX) -> LD2450 RX
- Keep UART0 for USB flashing + host serial data.

### ESP32_B (3 sensors, no host serial required)

- `S2` on UART1: ESP32 GPIO26 (RX) <- LD2450 TX, ESP32 GPIO25 (TX) -> LD2450 RX
- `S3` on UART2: ESP32 GPIO16 (RX) <- LD2450 TX, ESP32 GPIO17 (TX) -> LD2450 RX
- `S4` on UART0 default pins: ESP32 GPIO3 (RX) <- LD2450 TX, ESP32 GPIO1 (TX) -> LD2450 RX

## Power distribution

- Power LD2450 modules from a stable 5V rail sized for startup/current spikes.
- Power each ESP32 from a stable USB supply or dedicated regulator.
- Tie all grounds together:
  - ESP32_A GND
  - ESP32_B GND
  - all LD2450 GND
  - main power supply GND
- Keep radar power wiring short and use thicker conductors for shared rails.
- Add local decoupling near each LD2450 if you see noise/resets.

## Reliability notes

- LD2450 UART in this project is `256000` baud.
- Hardware UART only; do not use software UART at this baud.
- Keep TX/RX runs short and avoid routing parallel to noisy power lines.
- If packet loss appears, first check ground quality and power ripple.

## ESP-NOW pairing

- In `esp32_array_node_a.ino`, set `PEER_B_MAC` to Node B WiFi MAC.
- In `esp32_array_node_b.ino`, set `PEER_A_MAC` to Node A WiFi MAC.
- Keep `ESPNOW_CHANNEL` identical on both nodes (default `1`).
- Capture each MAC from serial boot logs or by printing `WiFi.macAddress()` once.

## ESP-NOW reliability notes

- Node B runs without USB serial logging in normal mode (UART0 is assigned to `S4`).
- Node A telemetry includes `remote_link_ms`, `remote_rx_count`, and `remote_drop_count` for quick link diagnostics in host UI.

## Mechanical placement

- Default spacing: 120 mm center-to-center.
- Global sensor offsets from bar center (mm): `[-240, -120, 0, 120, 240]`.
- Keep all modules parallel and rigidly fixed to the same horizontal bar.
