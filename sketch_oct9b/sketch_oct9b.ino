#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_ADXL345_U.h>
#include <ArduinoJson.h>

// ---------------------- Wi-Fi / MQTT ----------------------
const char* ssid = "Nstation 2";
const char* password = "alkalium";
const char* mqttServer = "10.77.175.73";  // Replace with your broker IP
const int mqttPort = 1883;
const char* mqttTopic = "machine/sensor";

WiFiClient espClient;
PubSubClient client(espClient);

// ---------------------- ADXL345 ----------------------
Adafruit_ADXL345_Unified accel = Adafruit_ADXL345_Unified(12345);

// ---------------------- Buttons / LED ----------------------
#define CONFIG_BUTTON 12
#define STOP_BUTTON 14
#define LED_PIN 2

bool monitoringActive = false;  // Only active after baseline
bool baselineCollected = false;

// ---------------------- Sampling ----------------------
#define BASELINE_SAMPLES 500
#define WINDOW_SIZE 100  // samples per monitoring message

float xBuffer[WINDOW_SIZE];
float yBuffer[WINDOW_SIZE];
float zBuffer[WINDOW_SIZE];
int bufferIndex = 0;

// ---------------------- Wi-Fi / MQTT Functions ----------------------
void connectWiFi() {
  Serial.print("Connecting to Wi-Fi...");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(500);
  }
  Serial.println("\nWi-Fi connected!");
  Serial.print("IP Address: ");
  Serial.println(WiFi.localIP());
}

void connectMQTT() {
  client.setServer(mqttServer, mqttPort);
  while (!client.connected()) {
    Serial.print("Connecting to MQTT...");
    if (client.connect("ESP32Client")) {
      Serial.println("connected");
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 2 sec");
      delay(2000);
    }
  }
}

// ---------------------- ADXL345 Functions ----------------------
void readAccelerometer(float &x, float &y, float &z) {
  sensors_event_t event;
  accel.getEvent(&event);
  x = event.acceleration.x;
  y = event.acceleration.y;
  z = event.acceleration.z;
}

// ---------------------- LED Functions ----------------------
void blinkLED(int duration = 200) {
  digitalWrite(LED_PIN, HIGH);
  delay(duration);
  digitalWrite(LED_PIN, LOW);
}

void solidLED(bool on = true) {
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}

// ---------------------- Baseline Collection ----------------------
void sendBaseline() {
  Serial.println("Collecting baseline samples...");
  solidLED(true);

  float xSamples[BASELINE_SAMPLES];
  float ySamples[BASELINE_SAMPLES];
  float zSamples[BASELINE_SAMPLES];

  for (int i = 0; i < BASELINE_SAMPLES; i++) {
    float x, y, z;
    readAccelerometer(x, y, z);
    xSamples[i] = x;
    ySamples[i] = y;
    zSamples[i] = z;
    delay(10);  // ~100 Hz sampling
  }

  StaticJsonDocument<4096> doc;
  doc["machine_id"] = "MACHINE_1";
  doc["type"] = "configure";
  JsonArray xArray = doc.createNestedArray("x");
  JsonArray yArray = doc.createNestedArray("y");
  JsonArray zArray = doc.createNestedArray("z");

  for (int i = 0; i < BASELINE_SAMPLES; i++) {
    xArray.add(xSamples[i]);
    yArray.add(ySamples[i]);
    zArray.add(zSamples[i]);
  }

  String payload;
  serializeJson(doc, payload);

  if (client.connected()) {
    client.publish(mqttTopic, payload.c_str());
    Serial.println("Baseline sent to server!");
  } else {
    Serial.println("MQTT not connected! Could not send baseline.");
  }

  solidLED(false);
}

// ---------------------- Monitoring Data ----------------------
void sendMonitoringData() {
  StaticJsonDocument<2048> doc;
  doc["machine_id"] = "MACHINE_1";
  doc["type"] = "data";
  JsonArray xArray = doc.createNestedArray("x");
  JsonArray yArray = doc.createNestedArray("y");
  JsonArray zArray = doc.createNestedArray("z");

  for (int i = 0; i < bufferIndex; i++) {
    xArray.add(xBuffer[i]);
    yArray.add(yBuffer[i]);
    zArray.add(zBuffer[i]);
  }

  String payload;
  serializeJson(doc, payload);

  if (client.connected()) {
    client.publish(mqttTopic, payload.c_str());
    Serial.print("Monitoring data sent (");
    Serial.print(bufferIndex);
    Serial.println(" samples)");
  } else {
    Serial.println("MQTT not connected! Could not send monitoring data.");
  }

  bufferIndex = 0;
  blinkLED(100);  // LED blink for data sent
}

// ---------------------- Setup ----------------------
void setup() {
  Serial.begin(115200);

  pinMode(CONFIG_BUTTON, INPUT_PULLUP);
  pinMode(STOP_BUTTON, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);

  Serial.println("Initializing ADXL345...");
  if (!accel.begin()) {
    Serial.println("ADXL345 not found!");
    while (1); // stop execution
  }
  accel.setRange(ADXL345_RANGE_16_G);
  Serial.println("ADXL345 initialized.");

  connectWiFi();
  connectMQTT();
  Serial.println("Setup complete. Waiting for baseline configuration...");
}

// ---------------------- Main Loop ----------------------
void loop() {
  client.loop();

  // Blink LED while waiting for baseline
  if (!baselineCollected) {
    blinkLED(300);
  }

  // Configure button
  if (digitalRead(CONFIG_BUTTON) == LOW && !baselineCollected) {
    Serial.println("Configure button pressed.");
    sendBaseline();
    baselineCollected = true;
    monitoringActive = true;
    Serial.println("Monitoring activated after baseline.");
    delay(1000);  // debounce
  }

  // Stop button
  if (digitalRead(STOP_BUTTON) == LOW && baselineCollected) {
    monitoringActive = !monitoringActive;
    Serial.print("Monitoring Active: ");
    Serial.println(monitoringActive);
    delay(500); // debounce
  }

  // Normal monitoring
  if (monitoringActive && baselineCollected) {
    float x, y, z;
    readAccelerometer(x, y, z);

    xBuffer[bufferIndex] = x;
    yBuffer[bufferIndex] = y;
    zBuffer[bufferIndex] = z;
    bufferIndex++;

    if (bufferIndex >= WINDOW_SIZE) {
      sendMonitoringData();
    }

    delay(10);  // 100 Hz
  }
}
