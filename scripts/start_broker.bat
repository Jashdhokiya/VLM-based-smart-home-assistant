@echo off
echo Starting Mosquitto MQTT Broker...
"C:\Program Files\mosquitto\mosquitto.exe" -c "%~dp0..\mosquitto.conf" -v
pause
