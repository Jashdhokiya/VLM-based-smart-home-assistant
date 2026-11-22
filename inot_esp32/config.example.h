#ifndef CONFIG_H
#define CONFIG_H

// Wi-Fi Credentials
const char* WIFI_SSID = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// MQTT Broker Settings
// When using PC:  set to your laptop's LAN IP (ipconfig / ip a)
// When using Pi:  set to the Pi's LAN IP or hostname (e.g. "mark1pi.local")
const char* MQTT_BROKER_IP = "192.168.1.100";
const int MQTT_PORT = 1883;

#endif // CONFIG_H
