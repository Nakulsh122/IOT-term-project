#include <WiFi.h>
#include <ArduinoJson.h>
#include <ArduinoWebsockets.h>
#include <Wire.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_ADXL345_U.h>

using namespace websockets;

// ---------------- Wi-Fi ----------------
const char* ssid = "Nstation 2";
const char* password = "alkalium";
const char* websocketServer = "ws://10.77.175.73:8000/ws/esp"; // Replace with your server IP

WebsocketsClient client;

// ---------------- Buttons / LED ----------------
#define CONFIG_BUTTON 12
#define STOP_BUTTON 14
#define LED_PIN 2

bool baselineCollected = false;
bool monitoringActive = false;

// ---------------- Sampling ----------------
#define BASELINE_SAMPLES 500
#define CHUNK_SIZE 100   // Send 100 samples at a time
#define WINDOW_SIZE 100

float baselineX[BASELINE_SAMPLES];
float baselineY[BASELINE_SAMPLES];
float baselineZ[BASELINE_SAMPLES];

float xBuffer[WINDOW_SIZE];
float yBuffer[WINDOW_SIZE];
float zBuffer[WINDOW_SIZE];
int bufferIndex = 0;

// ---------------- ADXL345 ----------------
Adafruit_ADXL345_Unified accel = Adafruit_ADXL345_Unified(12345);

void setupADXL() {
  if(!accel.begin()) {
    Serial.println("Could not find ADXL345, check wiring!");
    while(1) delay(10);
  }
  accel.setRange(ADXL345_RANGE_16_G); // Use 16G range for monitoring
  Serial.println("ADXL345 initialized");
}

// ---------------- Read Accelerometer ----------------
void readAccelerometer(float &x, float &y, float &z) {
  sensors_event_t event;
  accel.getEvent(&event);
  x = event.acceleration.x;
  y = event.acceleration.y;
  z = event.acceleration.z;
}

// ---------------- LED ----------------
void blinkLED(int duration = 200) {
  digitalWrite(LED_PIN, HIGH);
  delay(duration);
  digitalWrite(LED_PIN, LOW);
}

void solidLED(bool on = true) {
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}

// ---------------- Send JSON ----------------
void sendJSON(const char* type, int samples, float* x, float* y, float* z) {
  DynamicJsonDocument doc(8192);  // Large enough for chunk
  doc["machine_id"] = "MACHINE_1";
  doc["type"] = type;
  JsonArray xArray = doc.createNestedArray("x");
  JsonArray yArray = doc.createNestedArray("y");
  JsonArray zArray = doc.createNestedArray("z");

  for(int i=0; i<samples; i++){
    xArray.add(x[i]);
    yArray.add(y[i]);
    zArray.add(z[i]);
  }

  String payload;
  serializeJson(doc, payload);
  client.send(payload);
}

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);
  pinMode(CONFIG_BUTTON, INPUT_PULLUP);
  pinMode(STOP_BUTTON, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);

  // Initialize ADXL345
  setupADXL();

  // Connect to Wi-Fi
  WiFi.begin(ssid, password);
  Serial.print("Connecting to Wi-Fi...");
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(500);
  }
  Serial.println("\nWi-Fi connected!");

  // Connect WebSocket
  client.onMessage([](WebsocketsMessage msg){
    Serial.println("Server says: " + msg.data());
  });

  Serial.println("Connecting to WebSocket server...");
  while(!client.connect(websocketServer)){
    Serial.println("Connection failed, retrying in 2 sec...");
    delay(2000);
  }
  Serial.println("WebSocket connected!");
}

// ---------------- Loop ----------------
void loop() {
  client.poll();

  // Blink LED while waiting for baseline
  if(!baselineCollected) blinkLED(300);

  // Configure button
  if(digitalRead(CONFIG_BUTTON) == LOW && !baselineCollected){
    Serial.println("Configure button pressed");
    solidLED(true);

    // Collect baseline samples
    for(int i=0;i<BASELINE_SAMPLES;i++){
      readAccelerometer(baselineX[i], baselineY[i], baselineZ[i]);
      delay(10); // ~100 Hz
    }

    // Send baseline in chunks
    for(int i=0;i<BASELINE_SAMPLES;i+=CHUNK_SIZE){
      int chunk = (i+CHUNK_SIZE <= BASELINE_SAMPLES) ? CHUNK_SIZE : (BASELINE_SAMPLES - i);
      sendJSON("configure", chunk, &baselineX[i], &baselineY[i], &baselineZ[i]);
      delay(50);
    }

    baselineCollected = true;
    monitoringActive = true;
    solidLED(false);
    Serial.println("Baseline sent, monitoring active");
    delay(1000);
  }

  // Stop button
  if(digitalRead(STOP_BUTTON) == LOW && baselineCollected){
    monitoringActive = !monitoringActive;
    Serial.print("Monitoring Active: ");
    Serial.println(monitoringActive);
    delay(500);
  }

  // Normal monitoring
  if(monitoringActive && baselineCollected){
    float x, y, z;
    readAccelerometer(x, y, z);

    xBuffer[bufferIndex] = x;
    yBuffer[bufferIndex] = y;
    zBuffer[bufferIndex] = z;
    bufferIndex++;

    if(bufferIndex >= WINDOW_SIZE){
      sendJSON("data", bufferIndex, xBuffer, yBuffer, zBuffer);
      bufferIndex = 0;
      blinkLED(100);
    }
    delay(10); // ~100 Hz
  }
}
