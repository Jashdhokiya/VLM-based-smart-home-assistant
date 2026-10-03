#ifndef CONFIG_H
#define CONFIG_H

// Wi-Fi Credentials
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// MQTT Broker Settings (Must be Laptop LAN IP, NOT localhost)
const char* MQTT_BROKER_IP = "192.168.1.100";
const int MQTT_PORT = 1883;

#endif // CONFIG_H
