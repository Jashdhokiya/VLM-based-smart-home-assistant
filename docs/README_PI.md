# INOT Mark1 — Raspberry Pi 4B Setup Guide

> **UNTESTED ON PHYSICAL PI** — Written from firmware, scripts, and architecture review.  
> Follow the [verification checklist](#verification-checklist) on first boot.

---

## Overview

The Pi runs the full runtime pipeline locally:
- **Mosquitto** MQTT broker (Docker, TCP 1883 + WebSocket 9001)  
- **Ollama** local LLM (Docker, `qwen2.5:1.5b`)  
- **Python app** in a venv (Vosk offline STT → fast-path / LLM → MQTT → ESP32)
- **FastAPI server** serving the digital twin and REST API  

Onboarding (photo → YOLO → cloud VLM → `room_context.json`) runs on your **PC**, then the context is pushed to the Pi.

---

## 1. Flash Raspberry Pi OS Lite 64-bit

1. Download **Raspberry Pi Imager** from https://www.raspberrypi.com/software/
2. Choose **Raspberry Pi OS Lite (64-bit)** (Bookworm).
3. Click the **⚙ gear / Advanced options** button before writing:
   - **Hostname**: `mark1pi` (or your choice)
   - **Username**: `pi` (or your choice — replace `pi` throughout this guide)
   - **Password**: set a strong password
   - **Wi-Fi**: enter SSID and password
   - **Enable SSH**: ✓ (use password authentication)
   - **Locale / timezone**: set your region
4. Write to SD card, insert into Pi, power on.

---

## 2. Find the Pi's IP

```bash
# From your router admin page, or:
ping mark1pi.local        # mDNS (may take 60 s on first boot)

# Or scan your network:
# Windows:
arp -a | findstr /i "b8-27\|dc-a6\|e4-5f"   # Raspberry Pi MAC prefixes
# Linux/macOS:
nmap -sn 192.168.1.0/24 | grep -i raspberry
```

---

## 3. First SSH

```bash
ssh pi@mark1pi.local
# Or by IP:
ssh pi@192.168.1.XXX
```

Accept the host key fingerprint on first connection.

---

## 4. VS Code Remote-SSH (Optional but recommended)

1. Install the **Remote - SSH** extension in VS Code.
2. Press `Ctrl+Shift+P` → **Remote-SSH: Connect to Host** → `pi@mark1pi.local`.
3. You can now edit files directly on the Pi with full IntelliSense.

---

## 5. Clone the Repository

```bash
# On the Pi:
mkdir -p ~/Mark1
cd ~/Mark1
git clone https://github.com/Jashdhokiya/VLM-based-smart-home-assistant.git .
```

---

## 6. Run setup_pi.sh

```bash
chmod +x scripts/setup_pi.sh
./scripts/setup_pi.sh
```

This script (idempotent — safe to re-run) does:
1. `apt` install of system packages (git, python3-venv, portaudio, libasound2, etc.)
2. Installs Docker + Compose plugin
3. Creates `.venv` and `pip install -r requirements.txt`
4. Copies `.env.example` → `.env` with Pi defaults (PLATFORM=rpi, STT=vosk, LLM=ollama)
5. `docker compose up -d` (Mosquitto + Ollama)
6. Pulls `qwen2.5:1.5b` into Ollama (may take 5–15 min on first run)
7. Downloads Vosk small English model

> **After setup_pi.sh completes**, you may need to log out and back in  
> for the `docker` group to take effect: `newgrp docker` or re-SSH.

---

## 7. Copy .env and room_context.json from PC

```bash
# From your PC (PowerShell or bash):
scp .env               pi@mark1pi.local:~/Mark1/
scp room_context.json  pi@mark1pi.local:~/Mark1/

# Or push room_context.json via the HTTP endpoint after starting the server:
curl -X POST http://mark1pi.local:8000/context \
     -H "Content-Type: application/json" \
     -d @room_context.json
```

---

## 8. Run Preflight Diagnostics

```bash
cd ~/Mark1
source .venv/bin/activate
python scripts/preflight_pi.py
```

All items should show **[PASS]** or **[SKIP]** (mic skip is normal before USB mic).  
Fix any **[FAIL]** items using the printed hints before continuing.

---

## 9. Test with WAV Files (No Mic Required)

```bash
# Single WAV file — fast path test (no LLM needed):
python pipeline.py --wav tests/fixtures/test_command.wav

# Direct text test (no STT needed):
python pipeline.py --text "turn on light1"

# Validate room context:
python room_context.py room_context.json
```

---

## 10. Add a USB Microphone

1. Plug in USB mic, then:
```bash
python scripts/mic_helper.py        # list devices
python scripts/mic_helper.py --test # record 3-second test clip
```
2. Note the device index, e.g. `[2] USB Audio Device`.
3. Edit `.env`:
```bash
AUDIO_DEVICE=2        # or the device name string
```
4. Test live:
```bash
python pipeline.py --loop
```

---

## 11. Enable systemd Services

Install and start the pipeline service and the server/twin service:

```bash
# Copy unit files
sudo cp systemd/inot-mark1@.service  /etc/systemd/system/
sudo cp systemd/inot-twin@.service   /etc/systemd/system/

# Reload and enable (replace 'pi' with your username)
sudo systemctl daemon-reload
sudo systemctl enable --now inot-mark1@pi.service
sudo systemctl enable --now inot-twin@pi.service

# Check status
sudo systemctl status inot-mark1@pi.service
sudo systemctl status inot-twin@pi.service

# View logs
journalctl -u inot-mark1@pi -f
journalctl -u inot-twin@pi  -f
```

---

## 12. ESP32 Reflash with Pi's IP

1. Open `inot_esp32/config.example.h`, copy to `inot_esp32/config.h`.
2. Edit `config.h`:
```cpp
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_BROKER_IP = "192.168.1.XXX"; // Pi's LAN IP
// Or use mDNS hostname if your ESP32 supports it:
// const char* MQTT_BROKER_IP = "mark1pi.local";
const int   MQTT_PORT      = 1883;
```
3. Open **Arduino IDE**, select your ESP32 board, click **Upload**.
4. Open Serial Monitor (115200 baud). You should see:
   ```
   Wi-Fi connected. IP address: 192.168.1.YYY
   MQTT connected.
   Subscribed to device set topics.
   ```

---

## 13. Open the Digital Twin in a Browser

```
http://mark1pi.local:8000/twin
```

The twin:
- Loads device positions from `/context` and state from `/state`
- Subscribes to `inot/devices/+/state` via MQTT WebSocket on port **9001**
- Shows retained state immediately on load
- Clicking a device card publishes to `inot/devices/<id>/set`
- The floor plan renders positions from `room_context.json`

---

## Troubleshooting

### No audio device

```bash
# Check ALSA sees the device:
aplay -l ; arecord -l

# If USB mic isn't listed, unplug and replug:
dmesg | tail -20
```

Set `STT_BACKEND=vosk` or `STT_BACKEND=faster_whisper` and use `--wav` mode until mic is confirmed.

---

### Ollama OOM → switch to smaller model

```bash
# Edit .env:
OLLAMA_MODEL=qwen2.5:0.5b
OLLAMA_FALLBACK_MODEL=qwen2.5:0.5b

# Pull the smaller model:
docker compose exec ollama ollama pull qwen2.5:0.5b

# Check RAM:
free -h
# Check which process is using RAM:
ps aux --sort=-%mem | head -10
```

Alternatively, add swap:
```bash
sudo dphys-swapfile swapoff
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
sudo dphys-swapfile setup
sudo dphys-swapfile swapon
```

---

### CPU throttling

```bash
vcgencmd get_throttled
# 0x0 = no throttle. Anything else = problem.

# Check temperature:
vcgencmd measure_temp

# Fix: use a heatsink + fan, or check PSU (should be 5V 3A USB-C)
```

---

### MQTT connection refused

```bash
# Check Docker containers are running:
docker compose ps

# Check Mosquitto logs:
docker compose logs mosquitto

# Test TCP connection manually:
nc -zv localhost 1883
nc -zv localhost 9001

# Restart:
docker compose restart mosquitto
```

---

### mDNS (mark1pi.local) not resolving

```bash
# Install avahi on Pi:
sudo apt install -y avahi-daemon
sudo systemctl enable --now avahi-daemon

# From Windows PC, install Bonjour Print Services:
# https://support.apple.com/kb/DL999

# Or just use the IP address directly.
```

---

### Digital twin not connecting to MQTT WebSocket

The twin connects to `ws://<hostname>:9001`. Checklist:
1. `mosquitto.conf` has `listener 9001` with `protocol websockets` ✓ (included)
2. Port 9001 is not firewalled: `sudo ufw allow 9001`
3. Browser console shows error — check CORS or ws:// vs wss://
4. mqtt.js CDN unreachable (air-gapped Pi):
   ```bash
   # On PC, download mqtt.min.js:
   curl -o mqtt.min.js https://unpkg.com/mqtt@5/dist/mqtt.min.js
   scp mqtt.min.js pi@mark1pi.local:~/Mark1/twin/
   ```
   Then edit `twin/index.html` and change the script src to `mqtt.min.js`.

---

## Benchmark Procedure (Local vs Cloud)

Run both backends on the same WAV file and compare `logs/metrics.csv`:

```bash
# 1. Groq (cloud baseline)
STT_BACKEND=groq LLM_BACKEND=groq \
  python pipeline.py --wav tests/fixtures/test_command.wav

# 2. Local (Vosk + Ollama)
STT_BACKEND=vosk LLM_BACKEND=ollama \
  python pipeline.py --wav tests/fixtures/test_command.wav

# 3. Faster-whisper + Ollama
STT_BACKEND=faster_whisper LLM_BACKEND=ollama \
  python pipeline.py --wav tests/fixtures/test_command.wav

# 4. Fast-path only (no LLM)
python pipeline.py --text "turn on light1"

# View summary:
python scripts/metrics_summary.py --last 50
```

Expected Pi 4B benchmarks (approximate, varies by SD card speed):

| Stage         | Groq (cloud) | Vosk local | FW-Whisper |
|---------------|-------------|-----------|------------|
| STT           | 1–3 s       | 0.5–1 s   | 2–5 s      |
| Fast path     | —           | <5 ms     | <5 ms      |
| Ollama LLM    | —           | 5–20 s    | 5–20 s     |
| MQTT publish  | <10 ms      | <10 ms    | <10 ms     |
| **Total (FP)**| 1–3 s       | <1 s      | <6 s       |

> **Tip**: The fast path handles most common commands without hitting the LLM.  
> Benchmark separately to see the split.

---

## Verification Checklist

Run these on the Pi **in order** after setup:

```bash
# 1. Docker services running
docker compose ps

# 2. Mosquitto TCP reachable
nc -zv localhost 1883 && echo PASS || echo FAIL

# 3. Mosquitto WebSocket reachable
nc -zv localhost 9001 && echo PASS || echo FAIL

# 4. Ollama responding
curl -sf http://localhost:11434/api/tags | python3 -m json.tool

# 5. Ollama model available
curl -sf http://localhost:11434/api/tags | grep qwen2.5

# 6. Room context valid
python room_context.py room_context.json

# 7. Preflight all checks
python scripts/preflight_pi.py

# 8. WAV pipeline (fast path)
python pipeline.py --wav tests/fixtures/test_command.wav

# 9. WAV pipeline (LLM path — complex command)
python pipeline.py --text "I am going to sleep, turn everything off"

# 10. Server starts and /api/status responds
python server.py &
sleep 3
curl http://localhost:8000/api/status | python3 -m json.tool
kill %1

# 11. Digital twin page loads
curl -sf http://localhost:8000/twin/ | grep SIMULATION && echo PASS

# 12. Run unit tests (no Pi hardware, no internet needed)
pytest -o pythonpath=. tests/ -v
```

All should output **PASS** or succeed. File any failures in the GitHub issues with the full output.

