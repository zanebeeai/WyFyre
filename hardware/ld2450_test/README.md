# LD2450 + ESP32 Bridge Console

This project runs an HLK-LD2450 radar through an ESP32 UART bridge and provides a desktop control/visualization UI.

Hardware path:

`PC (USB serial) <-> ESP32 <-> UART2 <-> LD2450`

## What is included

- `ld2450_test.ino` - ESP32 transparent serial bridge (host <-> LD2450)
- `serial_protocol.py` - LD2450 protocol parser + command API
- `mapping.py` - Desktop UI with live plot and device controls
- `requirements.txt` - Python dependencies

## Features

- Live plotting of up to 3 targets
- Per-target telemetry: `x`, `y`, `speed`, `distance resolution`
- LD2450 commands:
  - Enable/End configuration mode
  - Single/Multi target tracking and query
  - Read firmware version
  - Set radar baud rate
  - Restart module
  - Factory reset
  - Bluetooth on/off
  - Read MAC address
  - Query/Set zone filtering (3 regions)

## LD2450 hardware profile

Based on the vendor description (`https://www.hlktech.net/index.php?id=1157`):

- 24 GHz ISM band operation
- Integrated mmWave radar + onboard intelligent tracking firmware
- Human motion localization/tracking
- Up to 6 m detection range
- Wall-mount deployment model
- Horizontal FOV (azimuth): +/-60 deg
- Vertical FOV (pitch): +/-35 deg

The UI radar scope is now aligned to these specs:

- Fan-shaped field of view in the plot (+/-60 deg)
- 1 m range rings from 1 m to 6 m
- Animated sweep line for radar-like motion cue
- Per-target range/azimuth labels and short motion trails

## UI and command guide

### Connection panel

- `Port`: Select ESP32 COM port.
- `Refresh`: Re-scan available serial ports.
- `Host Baud`: PC<->ESP32 USB baud rate (default `115200`).
- `Connect`: Open serial and start live parser/plot updates.
- `Disconnect`: Close serial cleanly.

### Device Commands panel

- `Enable Config`: Enter LD2450 configuration mode.
- `End Config`: Exit configuration mode.
- `Single Target`: Track one strongest target.
- `Multi Target`: Track up to three targets.
- `Query Tracking`: Read current tracking mode.
- `Read Firmware`: Query firmware version.
- `Get MAC`: Query MAC address.
- `Radar Baud + Set`: Change LD2450 UART baud.
- `Bluetooth On + Apply`: Enable/disable Bluetooth.
- `Restart`: Reboot module.
- `Factory Reset`: Restore factory defaults.

### Zone Filtering panel

- `Mode` values:
  - `0`: no zone filtering
  - `1`: detect only inside configured zones
  - `2`: exclude configured zones
- `R1/R2/R3`: Rectangles as `x1,y1,x2,y2`.
- `Query`: Read current mode and coordinates from module.
- `Apply`: Write current UI values to module.

### Targets / Plot / Status

- `Targets`: Live telemetry for T1/T2/T3 (`x`, `y`, `speed`, `dist res`, `active`).
- `Live Plot`: Radar-style scope (6 m range rings, +/-60 deg sector, sweep line, target trails).
- `Status`: Command results and error log output.

## Wiring

Use your existing setup where ESP32 powers the LD2450 and uses UART2:

- ESP32 `RX2` (GPIO16) <- LD2450 `TX`
- ESP32 `TX2` (GPIO17) -> LD2450 `RX`
- Common `GND`
- Power the LD2450 from ESP32 supply as already validated in your build

Default UART settings in this repo:

- Host <-> ESP32 USB serial: `115200`
- ESP32 UART2 <-> LD2450: `256000`

## 1) Flash the ESP32 sketch

Flash `ld2450_test.ino` to your ESP32 from Arduino IDE.

The sketch is a binary passthrough bridge. This is expected behavior.

## 2) Install Python dependencies

From the project folder:

```bash
pip install -r requirements.txt
```

## 3) Run the desktop app

```bash
python mapping.py
```

In the UI:

1. Select your ESP32 COM port
2. Click `Connect`
3. Use command buttons as needed
4. Watch targets in the live plot and telemetry table

## Example usage procedures

### Procedure A: basic tracking startup

1. Connect to your COM port.
2. Click `Enable Config`.
3. Click `Multi Target`.
4. Click `End Config`.
5. Confirm moving targets appear in both `Targets` table and plot.

### Procedure B: track only in a selected area

1. Click `Enable Config`.
2. In `Zone Filtering`, set `Mode` to `1`.
3. Enter region coordinates for `R1` (`x1,y1,x2,y2`).
4. Click `Apply`.
5. Click `End Config`.
6. Verify detections are limited to that zone.

### Procedure C: exclude a noisy region

1. Click `Enable Config`.
2. Set `Mode` to `2`.
3. Enter the exclusion area in `R1` (and `R2/R3` if needed).
4. Click `Apply`.
5. Click `End Config`.
6. Verify false detections from that area are reduced.

### Procedure D: recover after bad settings

1. Click `Enable Config`.
2. Click `Factory Reset` and confirm.
3. Click `Restart`.
4. Reconnect if needed.
5. Re-apply desired tracking mode and zone settings.

### Procedure E: radar-style placement and validation

1. Wall-mount the LD2450 in its final orientation.
2. Start the app and connect.
3. Walk at known points near 1 m, 3 m, and 5 m in front of the module.
4. Verify detections stay inside the fan sector and near matching ring distances.
5. If needed, adjust mount angle/height and re-check.

### Procedure F: tune out side clutter using FOV + zones

1. Start from `Multi Target` mode.
2. Observe persistent clutter location in the radar scope.
3. Set `Zone Filtering` mode to `2` (exclude) and define a rectangle around that area.
4. Apply settings and validate clutter suppression while keeping main movement detections.

## Important notes

- Do not keep Arduino Serial Monitor open while using `mapping.py` (port conflict).
- If you open Serial Monitor on the bridge firmware, you will see garbled characters after boot logs. That is normal raw LD2450 binary traffic.
- `Set Radar Baud` changes LD2450 UART speed. If you change it away from `256000`, communication will fail until UART speeds are matched again.

## Troubleshooting

- No COM port listed:
  - Check USB cable/data support
  - Install ESP32 USB/UART driver
  - Click `Refresh`
- Connect fails:
  - Close Serial Monitor and any other serial apps
  - Verify selected COM port
- No targets shown:
  - Verify UART TX/RX are crossed correctly
  - Check common ground
  - Ensure LD2450 has stable power
  - Try `Multi Target` command
- App starts but crashes on missing packages:
  - Re-run `pip install -r requirements.txt`

## File reference

- `ld2450_test.ino`
- `serial_protocol.py`
- `mapping.py`
- `requirements.txt`
