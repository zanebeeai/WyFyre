HardwareSerial radar(2);

static const uint32_t HOST_BAUD = 115200;
static const uint32_t RADAR_BAUD = 256000;
static const int RADAR_RX_PIN = 16;
static const int RADAR_TX_PIN = 17;

void bridge_serial(Stream &from, Stream &to) {
  while (from.available()) {
    int b = from.read();
    if (b < 0) {
      return;
    }
    to.write((uint8_t)b);
  }
}

void setup() {
  Serial.begin(HOST_BAUD);
  radar.begin(RADAR_BAUD, SERIAL_8N1, RADAR_RX_PIN, RADAR_TX_PIN);
}

void loop() {
  bridge_serial(radar, Serial);  // LD2450 -> USB host
  bridge_serial(Serial, radar);  // USB host -> LD2450
}
