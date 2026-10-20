/*
 AgriFlow ESP32 starter firmware.
 IMPORTANT:
 - Set Wi-Fi, Render URL and token before uploading.
 - Confirm your relay module input polarity and 5V/3.3V compatibility.
 - Flow sensor signal must be safe for ESP32's 3.3V GPIO.
 - The ESP32 local timer is the safety backstop; cloud polling is not a safety system.
 - This starter sketch is a reference and must be bench-tested before connecting the valve.
*/
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* WIFI_SSID = "YOUR_2_4_GHZ_WIFI";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* BASE_URL = "https://YOUR-RENDER-SERVICE.onrender.com";
const char* DEVICE_TOKEN = "YOUR_RENDER_DEVICE_TOKEN";

constexpr int FLOW_PIN = 27;
constexpr int RELAY_PIN = 23;
constexpr bool RELAY_ACTIVE_LOW = true; // verify your module
constexpr float PULSES_PER_LITRE = 450.0; // calibrate against measured water
constexpr unsigned long POLL_MS = 3000;
constexpr unsigned long TELEMETRY_MS = 5000;

volatile uint32_t flowPulses = 0;
unsigned long lastPoll = 0, lastTelemetry = 0, lastFlowCalc = 0;
unsigned long valveDeadline = 0;
bool valveOpen = false;
float flowLpm = 0, totalLitres = 0;
String lastCommand = "STOP";

void IRAM_ATTR onFlowPulse() { flowPulses++; }

void setValve(bool on) {
  valveOpen = on;
  digitalWrite(RELAY_PIN, (on ^ RELAY_ACTIVE_LOW) ? HIGH : LOW);
  if (!on) {
    valveDeadline = 0;
    lastCommand = "STOP";
  }
}

String apiUrl(const char* path) {
  String base(BASE_URL);
  while (base.endsWith("/")) base.remove(base.length() - 1);
  return base + path;
}

bool postTelemetry() {
  if (WiFi.status() != WL_CONNECTED) return false;
  HTTPClient http;
  http.begin(apiUrl("/api/device/telemetry"));
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  StaticJsonDocument<256> doc;
  doc["flow_lpm"] = flowLpm;
  doc["total_litres"] = totalLitres;
  doc["remaining_seconds"] = valveOpen && valveDeadline > millis() ? (valveDeadline - millis()) / 1000 : 0;
  doc["valve_open"] = valveOpen;
  String body; serializeJson(doc, body);
  int code = http.POST(body);
  http.end();
  return code >= 200 && code < 300;
}

void pollCommand() {
  if (WiFi.status() != WL_CONNECTED) return;
  HTTPClient http;
  http.begin(apiUrl("/api/device/command"));
  http.addHeader("X-Device-Token", DEVICE_TOKEN);
  int code = http.GET();
  if (code == 200) {
    StaticJsonDocument<384> doc;
    if (deserializeJson(doc, http.getString()) == DeserializationError::Ok) {
      String command = doc["command"] | "STOP";
      unsigned long duration = doc["duration_seconds"] | 0UL;
      if (command == "START" && !valveOpen && duration > 0) {
        setValve(true);
        valveDeadline = millis() + duration * 1000UL;
        lastCommand = "START";
      } else if (command == "STOP") {
        setValve(false);
      }
    }
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  setValve(false); // boot-safe OFF; confirm relay polarity before powering valve
  pinMode(FLOW_PIN, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(FLOW_PIN), onFlowPulse, FALLING);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  unsigned long start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(500); Serial.print(".");
  }
  Serial.println(WiFi.status() == WL_CONNECTED ? "\nWi-Fi connected" : "\nWi-Fi not connected; valve stays OFF");
}

void loop() {
  unsigned long now = millis();
  // Local shutoff works even if Wi-Fi or Render goes offline.
  if (valveOpen && valveDeadline && (long)(now - valveDeadline) >= 0) setValve(false);

  if (now - lastFlowCalc >= 1000) {
    noInterrupts(); uint32_t pulses = flowPulses; flowPulses = 0; interrupts();
    float litresThisInterval = pulses / PULSES_PER_LITRE;
    totalLitres += litresThisInterval;
    flowLpm = litresThisInterval * 60.0;
    lastFlowCalc = now;
  }
  if (now - lastPoll >= POLL_MS) { pollCommand(); lastPoll = now; }
  if (now - lastTelemetry >= TELEMETRY_MS) { postTelemetry(); lastTelemetry = now; }
  delay(10);
}
