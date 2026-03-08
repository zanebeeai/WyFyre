#include <WiFi.h>
#include <esp_now.h>
#include <esp_wifi.h>

static const char* NODE_ID = "B";
static const uint32_t RADAR_BAUD = 256000;

static const uint8_t PEER_A_MAC[6] = {0xB0, 0xB2, 0x1C, 0xC1, 0xA3, 0x6C};
static const uint8_t ESPNOW_CHANNEL = 1;
static const uint16_t ESPNOW_MAGIC = 0x5759;
static const uint8_t ESPNOW_VERSION = 1;

static const uint16_t REPORT_FRAME_SIZE = 30;
static const uint8_t REPORT_HEADER[4] = {0xAA, 0xFF, 0x03, 0x00};
static const uint8_t REPORT_TAIL[2] = {0x55, 0xCC};
static const uint8_t CMD_HEADER[4] = {0xFD, 0xFC, 0xFB, 0xFA};
static const uint8_t CMD_TAIL[4] = {0x04, 0x03, 0x02, 0x01};

enum TrackMode : uint8_t {
  TRACK_SINGLE = 1,
  TRACK_MULTI = 2,
};

enum PacketType : uint8_t {
  PKT_TELEMETRY = 1,
  PKT_COMMAND = 2,
};

enum CommandType : uint8_t {
  CMD_SET_MODE = 1,
};

struct Detection {
  int16_t x_mm;
  int16_t y_mm;
  int16_t speed_cms;
  uint16_t dist_res_mm;
  bool active;
};

struct SensorState {
  uint8_t frame[REPORT_FRAME_SIZE];
  uint16_t fill = 0;
  bool have_last = false;
  Detection targets[3];
};

struct __attribute__((packed)) EspNowHeader {
  uint16_t magic;
  uint8_t version;
  uint8_t packet_type;
};

struct __attribute__((packed)) EspNowDetection {
  uint8_t sensor_global_index;
  uint8_t target_id;
  int16_t x_mm;
  int16_t y_mm;
  int16_t speed_cms;
  uint16_t distance_resolution_mm;
  uint8_t active;
};

struct __attribute__((packed)) EspNowTelemetry {
  uint16_t magic;
  uint8_t version;
  uint8_t packet_type;
  uint8_t mode;
  uint8_t reserved;
  uint32_t timestamp_ms;
  uint32_t seq;
  uint32_t tx_ok;
  uint32_t tx_fail;
  uint8_t sensor_frame_mask;
  uint8_t sensor_active_mask;
  uint8_t count;
  EspNowDetection detections[9];
};

struct __attribute__((packed)) EspNowCommand {
  uint16_t magic;
  uint8_t version;
  uint8_t packet_type;
  uint8_t command;
  uint8_t value;
  uint32_t seq;
};

HardwareSerial sensorUart0(0);
HardwareSerial sensorUart1(1);
HardwareSerial sensorUart2(2);
HardwareSerial* SENSOR_UARTS[3] = {&sensorUart1, &sensorUart2, &sensorUart0};
static const uint8_t SENSOR_GLOBAL_INDEX[3] = {2, 3, 4};
static const int RX_PINS[3] = {26, 16, 3};
static const int TX_PINS[3] = {25, 17, 1};

SensorState sensorState[3];
TrackMode desiredMode = TRACK_MULTI;
uint32_t telemetrySeq = 1;
uint32_t txOk = 0;
uint32_t txFail = 0;

int16_t decodeSigned15(uint16_t raw) {
  int16_t mag = raw & 0x7FFF;
  return (raw & 0x8000) ? -mag : mag;
}

void onEspNowSend(const uint8_t* mac, esp_now_send_status_t status) {
  (void)mac;
  if (status == ESP_NOW_SEND_SUCCESS) {
    txOk++;
  } else {
    txFail++;
  }
}

void sendCommandToSensor(HardwareSerial& uart, uint8_t cmdLo, uint8_t cmdHi) {
  uint8_t frame[12];
  frame[0] = CMD_HEADER[0];
  frame[1] = CMD_HEADER[1];
  frame[2] = CMD_HEADER[2];
  frame[3] = CMD_HEADER[3];
  frame[4] = 0x02;
  frame[5] = 0x00;
  frame[6] = cmdLo;
  frame[7] = cmdHi;
  frame[8] = CMD_TAIL[0];
  frame[9] = CMD_TAIL[1];
  frame[10] = CMD_TAIL[2];
  frame[11] = CMD_TAIL[3];
  uart.write(frame, sizeof(frame));
}

void applyLocalMode(TrackMode mode) {
  desiredMode = mode;
  for (uint8_t i = 0; i < 3; ++i) {
    sendCommandToSensor(*SENSOR_UARTS[i], 0xFF, 0x00);
    delay(10);
    if (mode == TRACK_SINGLE) {
      sendCommandToSensor(*SENSOR_UARTS[i], 0x80, 0x00);
    } else {
      sendCommandToSensor(*SENSOR_UARTS[i], 0x90, 0x00);
    }
    delay(10);
    sendCommandToSensor(*SENSOR_UARTS[i], 0xFE, 0x00);
    delay(10);
  }
}

bool parseOneReportFrame(SensorState& state, HardwareSerial& uart) {
  while (uart.available()) {
    int ch = uart.read();
    if (ch < 0) {
      break;
    }
    uint8_t b = static_cast<uint8_t>(ch);

    if (state.fill == 0 && b != REPORT_HEADER[0]) {
      continue;
    }
    state.frame[state.fill++] = b;

    if (state.fill == 1 && state.frame[0] != REPORT_HEADER[0]) state.fill = 0;
    if (state.fill == 2 && state.frame[1] != REPORT_HEADER[1]) state.fill = 0;
    if (state.fill == 3 && state.frame[2] != REPORT_HEADER[2]) state.fill = 0;
    if (state.fill == 4 && state.frame[3] != REPORT_HEADER[3]) state.fill = 0;

    if (state.fill == REPORT_FRAME_SIZE) {
      if (state.frame[28] == REPORT_TAIL[0] && state.frame[29] == REPORT_TAIL[1]) {
        for (uint8_t t = 0; t < 3; ++t) {
          uint8_t o = 4 + t * 8;
          uint16_t rawX = static_cast<uint16_t>(state.frame[o]) | (static_cast<uint16_t>(state.frame[o + 1]) << 8);
          uint16_t rawY = static_cast<uint16_t>(state.frame[o + 2]) | (static_cast<uint16_t>(state.frame[o + 3]) << 8);
          uint16_t rawS = static_cast<uint16_t>(state.frame[o + 4]) | (static_cast<uint16_t>(state.frame[o + 5]) << 8);
          uint16_t dr = static_cast<uint16_t>(state.frame[o + 6]) | (static_cast<uint16_t>(state.frame[o + 7]) << 8);
          state.targets[t].x_mm = decodeSigned15(rawX);
          state.targets[t].y_mm = decodeSigned15(rawY);
          state.targets[t].speed_cms = decodeSigned15(rawS);
          state.targets[t].dist_res_mm = dr;
          state.targets[t].active = dr != 0;
        }
        state.have_last = true;
        state.fill = 0;
        return true;
      }
      state.fill = 0;
    }
  }
  return false;
}

void onEspNowRecv(const uint8_t* mac, const uint8_t* data, int len) {
  (void)mac;
  if (len != static_cast<int>(sizeof(EspNowCommand)) || data == nullptr) {
    return;
  }

  EspNowCommand cmd;
  memcpy(&cmd, data, sizeof(cmd));
  if (cmd.magic != ESPNOW_MAGIC || cmd.version != ESPNOW_VERSION || cmd.packet_type != PKT_COMMAND) {
    return;
  }

  if (cmd.command == CMD_SET_MODE) {
    if (cmd.value == 1) {
      applyLocalMode(TRACK_SINGLE);
    } else {
      applyLocalMode(TRACK_MULTI);
    }
  }
}

void sendTelemetryToA() {
  EspNowTelemetry pkt = {};
  pkt.magic = ESPNOW_MAGIC;
  pkt.version = ESPNOW_VERSION;
  pkt.packet_type = PKT_TELEMETRY;
  pkt.mode = (desiredMode == TRACK_SINGLE) ? 1 : 2;
  pkt.timestamp_ms = millis();
  pkt.seq = telemetrySeq++;
  pkt.tx_ok = txOk;
  pkt.tx_fail = txFail;

  uint8_t frameMask = 0;
  uint8_t activeMask = 0;

  uint8_t outCount = 0;
  for (uint8_t s = 0; s < 3; ++s) {
    if (sensorState[s].have_last) {
      frameMask |= static_cast<uint8_t>(1U << s);
    }
    bool sensorHasActive = false;
    for (uint8_t t = 0; t < 3 && outCount < 9; ++t) {
      const Detection* d = nullptr;
      Detection zero = {0, 0, 0, 0, false};
      if (sensorState[s].have_last) {
        d = &sensorState[s].targets[t];
      } else {
        d = &zero;
      }
      if (d->active) {
        sensorHasActive = true;
      }

      EspNowDetection& out = pkt.detections[outCount++];
      out.sensor_global_index = SENSOR_GLOBAL_INDEX[s];
      out.target_id = t;
      out.x_mm = d->x_mm;
      out.y_mm = d->y_mm;
      out.speed_cms = d->speed_cms;
      out.distance_resolution_mm = d->dist_res_mm;
      out.active = d->active ? 1 : 0;
    }
    if (sensorHasActive) {
      activeMask |= static_cast<uint8_t>(1U << s);
    }
  }

  pkt.sensor_frame_mask = frameMask;
  pkt.sensor_active_mask = activeMask;
  pkt.count = outCount;
  esp_now_send(PEER_A_MAC, reinterpret_cast<const uint8_t*>(&pkt), sizeof(pkt));
}

bool setupEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  esp_wifi_set_promiscuous(false);

  if (esp_now_init() != ESP_OK) {
    return false;
  }

  esp_now_register_recv_cb(onEspNowRecv);
  esp_now_register_send_cb(onEspNowSend);

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, PEER_A_MAC, 6);
  peerInfo.channel = ESPNOW_CHANNEL;
  peerInfo.encrypt = false;
  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    return false;
  }
  return true;
}

void setup() {
  SENSOR_UARTS[0]->begin(RADAR_BAUD, SERIAL_8N1, RX_PINS[0], TX_PINS[0]);
  SENSOR_UARTS[1]->begin(RADAR_BAUD, SERIAL_8N1, RX_PINS[1], TX_PINS[1]);
  SENSOR_UARTS[2]->begin(RADAR_BAUD, SERIAL_8N1, RX_PINS[2], TX_PINS[2]);

  setupEspNow();
  delay(300);
  applyLocalMode(TRACK_MULTI);
}

void loop() {
  for (uint8_t i = 0; i < 3; ++i) {
    parseOneReportFrame(sensorState[i], *SENSOR_UARTS[i]);
  }

  static uint32_t lastEmitMs = 0;
  uint32_t now = millis();
  if (now - lastEmitMs >= 80) {
    sendTelemetryToA();
    lastEmitMs = now;
  }
}
