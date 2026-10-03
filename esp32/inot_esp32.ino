/*
 * INOT Mark1 - ESP32 Firmware
 * Controls LEDs via MQTT with state acknowledgements and LWT status.
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include "config.h"

// Pin mappings
const int PIN_LIGHT1 = 18;
const int PIN_LIGHT2 = 19;
const int PIN_LIGHT3 = 21; // Optional
const int PIN_LIGHT4 = 22; // Optional

// MQTT Topics
const char* TOPIC_LIGHT1_SET = "inot/devices/light1/set";
const char* TOPIC_LIGHT1_STATE = "inot/devices/light1/state";
const char* TOPIC_LIGHT2_SET = "inot/devices/light2/set";
const char* TOPIC_LIGHT2_STATE = "inot/devices/light2/state";
const char* TOPIC_LIGHT3_SET = "inot/devices/light3/set";
const char* TOPIC_LIGHT3_STATE = "inot/devices/light3/state";
const char* TOPIC_LIGHT4_SET = "inot/devices/light4/set";
const char* TOPIC_LIGHT4_STATE = "inot/devices/light4/state";

const char* TOPIC_STATUS = "inot/esp32/status";

WiFiClient espClient;
PubSubClient client(espClient);
String clientId;
unsigned long lastReconnectAttempt = 0;

void setupWifi() {
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int retries = 0;
  while (WiFi.status() != WL_CONNECTED && retries < 40) {
    delay(500);
    Serial.print(".");
    retries++;
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Wi-Fi connected. IP address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("Wi-Fi connection failed. Will retry in loop.");
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  message.trim();
  message.toUpperCase();

  Serial.print("[MQTT] Received on ");
  Serial.print(topic);
  Serial.print(": '");
  Serial.print(message);
  Serial.println("'");

  int targetPin = -1;
  const char* stateTopic = nullptr;

  if (strcmp(topic, TOPIC_LIGHT1_SET) == 0) {
    targetPin = PIN_LIGHT1;
    stateTopic = TOPIC_LIGHT1_STATE;
  } else if (strcmp(topic, TOPIC_LIGHT2_SET) == 0) {
    targetPin = PIN_LIGHT2;
    stateTopic = TOPIC_LIGHT2_STATE;
  } else if (strcmp(topic, TOPIC_LIGHT3_SET) == 0) {
    targetPin = PIN_LIGHT3;
    stateTopic = TOPIC_LIGHT3_STATE;
  } else if (strcmp(topic, TOPIC_LIGHT4_SET) == 0) {
    targetPin = PIN_LIGHT4;
    stateTopic = TOPIC_LIGHT4_STATE;
  } else {
    Serial.print("[WARN] Unknown topic: ");
    Serial.println(topic);
    return;
  }

  if (message == "ON") {
    digitalWrite(targetPin, HIGH);
    Serial.print("[GPIO] Set pin ");
    Serial.print(targetPin);
    Serial.println(" to HIGH");
    if (stateTopic) {
      client.publish(stateTopic, "ON", false);
    }
  } else if (message == "OFF") {
    digitalWrite(targetPin, LOW);
    Serial.print("[GPIO] Set pin ");
    Serial.print(targetPin);
    Serial.println(" to LOW");
    if (stateTopic) {
      client.publish(stateTopic, "OFF", false);
    }
  } else {
    Serial.print("[WARN] Unsupported command payload: '");
    Serial.print(message);
    Serial.println("'");
  }
}

boolean reconnectMqtt() {
  Serial.print("Attempting MQTT connection to ");
  Serial.print(MQTT_BROKER_IP);
  Serial.print(":");
  Serial.println(MQTT_PORT);

  // Connect with Last Will and Testament (LWT)
  // boolean connect(const char* id, const char* willTopic, uint8_t willQos, boolean willRetain, const char* willMessage)
  if (client.connect(clientId.c_str(), TOPIC_STATUS, 1, true, "offline")) {
    Serial.println("MQTT connected.");
    client.publish(TOPIC_STATUS, "online", true);

    // Subscribe to device set topics
    client.subscribe(TOPIC_LIGHT1_SET);
    client.subscribe(TOPIC_LIGHT2_SET);
    client.subscribe(TOPIC_LIGHT3_SET);
    client.subscribe(TOPIC_LIGHT4_SET);
    Serial.println("Subscribed to device set topics.");
    return true;
  } else {
    Serial.print("MQTT connection failed, state code: ");
    Serial.println(client.state());
    return false;
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== INOT Mark1 ESP32 Initializing ===");

  pinMode(PIN_LIGHT1, OUTPUT);
  pinMode(PIN_LIGHT2, OUTPUT);
  pinMode(PIN_LIGHT3, OUTPUT);
  pinMode(PIN_LIGHT4, OUTPUT);

  digitalWrite(PIN_LIGHT1, LOW);
  digitalWrite(PIN_LIGHT2, LOW);
  digitalWrite(PIN_LIGHT3, LOW);
  digitalWrite(PIN_LIGHT4, LOW);

  String mac = WiFi.macAddress();
  mac.replace(":", "");
  clientId = "inot-esp32-" + mac;
  Serial.print("Device Client ID: ");
  Serial.println(clientId);

  setupWifi();

  client.setServer(MQTT_BROKER_IP, MQTT_PORT);
  client.setCallback(mqttCallback);

  lastReconnectAttempt = 0;
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    setupWifi();
  }

  if (WiFi.status() == WL_CONNECTED) {
    if (!client.connected()) {
      unsigned long now = millis();
      if (now - lastReconnectAttempt > 5000) {
        lastReconnectAttempt = now;
        if (reconnectMqtt()) {
          lastReconnectAttempt = 0;
        }
      }
    } else {
      client.loop();
    }
  }
}
