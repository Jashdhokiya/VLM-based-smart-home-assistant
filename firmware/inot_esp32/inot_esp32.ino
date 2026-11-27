#include <WiFi.h>

void setup() {
  Serial.begin(115200);
  delay(1500);
  WiFi.mode(WIFI_STA);
  Serial.println("\n--- ESP32 Serial Ready ---");
}

void loop() {
  String mac = WiFi.macAddress();
  String cleanMac = mac;
  cleanMac.replace(":", "");

  Serial.println("---------------------------------");
  Serial.print("MAC Address: ");
  Serial.println(mac);
  Serial.print("Device UID : ");
  Serial.println(cleanMac);
  Serial.println("---------------------------------");
  
  delay(2000);
}
