#include <WiFi.h>
#include <esp_now.h>

static const char* NODE_ID = "A";
static const uint32_t SERIAL_BAUD = 115200;
static const uint32_t RADAR_BAUD = 256000;

static const uint8_t PEER_B_MAC[6] = {0xE4, 0x65, 0xB8, 0x4A, 0x19, 0x7C};

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
  uint8_t packet_type;
  uint8_t mode;
  uint32_t timestamp_ms;
  uint32_t seq;
  uint8_t count;
  EspNowDetection detections[9];
};

struct __attribute__((packed)) EspNowCommand {
  uint8_t packet_type;
  uint8_t command;
  uint8_t value;
  uint32_t seq;
};

HardwareSerial sensorUart1(1);
HardwareSerial sensorUart2(2);
HardwareSerial* SENSOR_UARTS[2] = {&sensorUart2, &sensorUart1};
static const char* SENSOR_IDS[2] = {"S0", "S1"};
static const uint8_t SENSOR_GLOBAL_INDEX[2] = {0, 1};
static const int RX_PINS[2] = {18, 16};
static const int TX_PINS[2] = {19, 17};

SensorState sensorState[2];
EspNowTelemetry latestRemoteTelemetry;
bool haveRemoteTelemetry = false;
uint32_t remoteLastSeenMs = 0;
TrackMode desiredMode = TRACK_MULTI;
uint32_t cmdSeq = 1;

int16_t decodeSigned15(uint16_t raw) {
  int16_t mag = raw & 0x7FFF;
  return (raw & 0x8000) ? -mag : mag;
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
  for (uint8_t i = 0; i < 2; ++i) {
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

void sendModeToPeerB(TrackMode mode) {
  EspNowCommand cmd;
  cmd.packet_type = PKT_COMMAND;
  cmd.command = CMD_SET_MODE;
  cmd.value = (mode == TRACK_SINGLE) ? 1 : 2;
  cmd.seq = cmdSeq++;
  esp_now_send(PEER_B_MAC, reinterpret_cast<const uint8_t*>(&cmd), sizeof(cmd));
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

    if (state.fill == 1 && state.frame[0] != REPORT_HEADER[0]) {
      state.fill = 0;
    }
    if (state.fill == 2 && state.frame[1] != REPORT_HEADER[1]) {
      state.fill = 0;
    }
    if (state.fill == 3 && state.frame[2] != REPORT_HEADER[2]) {
      state.fill = 0;
    }
    if (state.fill == 4 && state.frame[3] != REPORT_HEADER[3]) {
      state.fill = 0;
    }

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
  if (len <= 0 || data == nullptr) {
    return;
  }
  if (data[0] == PKT_TELEMETRY && len == static_cast<int>(sizeof(EspNowTelemetry))) {
    memcpy(&latestRemoteTelemetry, data, sizeof(EspNowTelemetry));
    haveRemoteTelemetry = true;
    remoteLastSeenMs = millis();
  }
}

void emitCombinedJson() {
  String payload;
  payload.reserve(2600);
  payload += "{\"msg\":\"detections\",\"node_id\":\"";
  payload += NODE_ID;
  payload += "\",\"timestamp_ms\":";
  payload += String(millis());
  payload += ",\"mode\":";
  payload += (desiredMode == TRACK_SINGLE) ? "\"single\"" : "\"multi\"";
  payload += ",\"remote_link_ms\":";
  payload += String(haveRemoteTelemetry ? (millis() - remoteLastSeenMs) : 999999);
  payload += ",\"detections\":[";

  bool first = true;
  for (uint8_t s = 0; s < 2; ++s) {
    if (!sensorState[s].have_last) {
      continue;
    }
    for (uint8_t t = 0; t < 3; ++t) {
      const Detection& d = sensorState[s].targets[t];
      if (!first) {
        payload += ",";
      }
      first = false;
      payload += "{\"sensor_id\":\"";
      payload += SENSOR_IDS[s];
      payload += "\",\"sensor_index\":";
      payload += String(SENSOR_GLOBAL_INDEX[s]);
      payload += ",\"target_id\":";
      payload += String(t);
      payload += ",\"x_mm\":";
      payload += String(d.x_mm);
      payload += ",\"y_mm\":";
      payload += String(d.y_mm);
      payload += ",\"speed_cms\":";
      payload += String(d.speed_cms);
      payload += ",\"distance_resolution_mm\":";
      payload += String(d.dist_res_mm);
      payload += ",\"active\":";
      payload += d.active ? "true" : "false";
      payload += "}";
    }
  }

  if (haveRemoteTelemetry && (millis() - remoteLastSeenMs) < 1500) {
    for (uint8_t i = 0; i < latestRemoteTelemetry.count && i < 9; ++i) {
      const EspNowDetection& d = latestRemoteTelemetry.detections[i];
      if (!first) {
        payload += ",";
      }
      first = false;
      payload += "{\"sensor_id\":\"S";
      payload += String(d.sensor_global_index);
      payload += "\",\"sensor_index\":";
      payload += String(d.sensor_global_index);
      payload += ",\"target_id\":";
      payload += String(d.target_id);
      payload += ",\"x_mm\":";
      payload += String(d.x_mm);
      payload += ",\"y_mm\":";
      payload += String(d.y_mm);
      payload += ",\"speed_cms\":";
      payload += String(d.speed_cms);
      payload += ",\"distance_resolution_mm\":";
      payload += String(d.distance_resolution_mm);
      payload += ",\"active\":";
      payload += d.active ? "true" : "false";
      payload += "}";
    }
  }

  payload += "]}";
  Serial.println(payload);
}

void emitStatus(const char* kind, const char* value) {
  String payload;
  payload.reserve(256);
  payload += "{\"msg\":\"";
  payload += kind;
  payload += "\",\"node_id\":\"";
  payload += NODE_ID;
  payload += "\",\"timestamp_ms\":";
  payload += String(millis());
  if (value != nullptr) {
    payload += ",\"value\":\"";
    payload += value;
    payload += "\"";
  }
  payload += "}";
  Serial.println(payload);
}

void handleHostCommand(const String& line) {
  if (line.indexOf("\"msg\":\"ping\"") >= 0) {
    emitStatus("pong", (desiredMode == TRACK_SINGLE) ? "single" : "multi");
    return;
  }
  if (line.indexOf("\"msg\":\"query_mode\"") >= 0) {
    emitStatus("mode", (desiredMode == TRACK_SINGLE) ? "single" : "multi");
    return;
  }
  if (line.indexOf("\"msg\":\"set_mode\"") >= 0) {
    if (line.indexOf("\"mode\":\"single\"") >= 0) {
      applyLocalMode(TRACK_SINGLE);
      sendModeToPeerB(TRACK_SINGLE);
      emitStatus("ack", "single");
      return;
    }
    if (line.indexOf("\"mode\":\"multi\"") >= 0) {
      applyLocalMode(TRACK_MULTI);
      sendModeToPeerB(TRACK_MULTI);
      emitStatus("ack", "multi");
      return;
    }
    emitStatus("nack", "invalid_mode");
    return;
  }
  emitStatus("nack", "unknown_command");
}

void pollSerialCommands() {
  static String line;
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      line.trim();
      if (line.length() > 0) {
        handleHostCommand(line);
      }
      line = "";
    } else if (c != '\r') {
      line += c;
      if (line.length() > 512) {
        line = "";
      }
    }
  }
}

bool setupEspNow() {
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  if (esp_now_init() != ESP_OK) {
    return false;
  }
  esp_now_register_recv_cb(onEspNowRecv);

  esp_now_peer_info_t peerInfo = {};
  memcpy(peerInfo.peer_addr, PEER_B_MAC, 6);
  peerInfo.channel = 0;
  peerInfo.encrypt = false;
  if (esp_now_add_peer(&peerInfo) != ESP_OK) {
    return false;
  }
  return true;
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  SENSOR_UARTS[0]->begin(RADAR_BAUD, SERIAL_8N1, RX_PINS[0], TX_PINS[0]);
  SENSOR_UARTS[1]->begin(RADAR_BAUD, SERIAL_8N1, RX_PINS[1], TX_PINS[1]);

  bool espOk = setupEspNow();
  delay(300);
  applyLocalMode(TRACK_MULTI);
  sendModeToPeerB(TRACK_MULTI);
  Serial.printf("{\"msg\":\"identity\",\"node_id\":\"%s\",\"mac\":\"%s\"}\n", NODE_ID, WiFi.macAddress().c_str());
  emitStatus("boot", espOk ? "espnow_ok" : "espnow_fail");
}

void loop() {
  for (uint8_t i = 0; i < 2; ++i) {
    parseOneReportFrame(sensorState[i], *SENSOR_UARTS[i]);
  }
  pollSerialCommands();

  static uint32_t lastEmitMs = 0;
  uint32_t now = millis();
  if (now - lastEmitMs >= 80) {
    emitCombinedJson();
    lastEmitMs = now;
  }
}
