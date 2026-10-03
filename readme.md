# 🧠 VLM-Based Smart Home Assistant (INOT Mark1)

An intelligent, vision-language-action (VLA) smart home system that enables real-time spatial voice control of physical appliances using webcam perception, spatial reasoning, and an open MQTT + ESP32 hardware architecture.

Based on the research architecture in **INOT (arXiv:2504.13942)**, this implementation eliminates proprietary cloud dependencies (replacing Tuya with ESP32 + local Mosquitto MQTT) and unifies the AI pipeline under **Groq** for high-speed, low-latency reasoning.

---

## ⚡ System Architecture

```text
Laptop Microphone ──> transcriptor.py (PyAudio/SoundDevice + Groq Whisper)
                            │
                      nl_processor.py ('two lights' ──> {'light': 2} via Groq Qwen)
                            │
Laptop Webcam     ──> camera_capture.py ──> images/current_scene.jpg
                            │
                      annotator.py (Visual candidate labeling & GUI editor) ──> annotated_image.jpg
                            │
                      spatial_inferencer.py (Groq Qwen Spatial Reasoning) ──> spatial_information.txt
                            │
Voice Command     ──> command_processor.py (Groq Qwen Contextual Decision)
                            │
                      safe_parse.py (Secure parsing, strict typing, no eval)
                            │
                      mqtt_executor.py (paho-mqtt 2.x) ──> metrics.py (logs latency)
                            │
                      Mosquitto Broker (TCP 1883) ──Wi-Fi──> ESP32 Dev Board
                                                                  │
                                                        GPIO 18 / 19 ──> LED1 / LED2
                                                                  │
                      State Ack (inot/devices/+/state) <──────────┘
```

---

## ✨ Key Features

- **Unified Single-Key AI Pipeline**: The entire AI stack (Speech-to-Text, Natural Language Parsing, Spatial Scene Reasoning, and Intent Decisions) runs via **Groq** (`qwen/qwen3.8-27b` and `whisper-large-v3`), delivering sub-200ms cloud inference.
- **Open Hardware Execution Layer**: Replaced proprietary Tuya cloud APIs with a local **Mosquitto MQTT broker** and **ESP32 microcontroller**, ensuring LAN operation without external cloud vendor lock-in.
- **Zero `eval()` Security**: All parsing across LLM responses and natural language inputs is hardened via [safe_parse.py](safe_parse.py) using abstract syntax trees (`ast.literal_eval`) and JSON schema validation, rejecting hostile inputs.
- **Hardware-Free Dry-Run Mode**: Set `MQTT_DRY_RUN=true` to test the full voice and vision pipeline on a laptop without physical hardware connected.
- **End-to-End Latency Instrumentation**: [metrics.py](metrics.py) automatically records microsecond-precision stage timings (`record`, `stt_api`, `llm_decision`, `parse`, `mqtt_publish`, `esp32_ack`) into `logs/metrics.csv`.
- **Preflight Diagnostics**: Built-in verification tool ([scripts/preflight.py](scripts/preflight.py)) checks API connectivity, models, and broker reachability before runtime.

---

## 📁 Repository Structure

```text
.
├── main.py                     # Main application entrypoint
├── camera_capture.py           # Webcam capture with auto-exposure warmup
├── transcriptor.py             # Audio recording & Groq Whisper STT
├── nl_processor.py             # Natural language device extraction (Groq Qwen)
├── annotator.py                # Visual bounding-box layout & interactive GUI
├── spatial_inferencer.py       # Spatial relationship reasoning (Groq Qwen)
├── command_processor.py        # Conversational command loop (Groq Qwen)
├── safe_parse.py               # AST-based safe parser (eliminates eval)
├── mqtt_executor.py            # Paho-MQTT 2.x publisher & ACK monitor
├── metrics.py                  # Latency instrumentation helper
├── mosquitto.conf              # Mosquitto broker config (0.0.0.0:1883)
├── requirements.txt            # Python dependencies
├── .env.example                # Environment variable configuration template
├── esp32/
│   ├── inot_esp32.ino          # Arduino C++ firmware with LWT and ACK publishing
│   └── config.example.h        # Wi-Fi and MQTT IP template
├── scripts/
│   └── preflight.py            # Self-test diagnostic utility
└── tests/
    ├── test_safe_parse.py      # Unit tests for secure parsing & hostile inputs
    └── test_pipeline.py        # Integration tests for MQTT, camera, and metrics
```

---

## 🛠️ Setup Instructions

### 1. Prerequisites
- **Python**: Version 3.10 to 3.14
- **Mosquitto MQTT Broker**: [Download Mosquitto](https://mosquitto.org/download/) or `winget install EclipseFoundation.Mosquitto`
- **Arduino IDE**: with ESP32 board support and the `PubSubClient` library installed.

### 2. Clone the Repository
```bash
git clone https://github.com/Jashdhokiya/VLM-based-smart-home-assistant.git
cd VLM-based-smart-home-assistant
```

### 3. Create Virtual Environment & Install Dependencies
```bash
python -m venv .venv

# On Windows:
.\.venv\Scripts\activate
pip install -r requirements.txt

# On Linux / macOS:
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy the `.env.example` file to `.env`:
```bash
cp .env.example .env
```
Open `.env` and add your **Groq API Key**:
```dotenv
GROQ_API_KEY=gsk_your_groq_api_key_here

AI_PROVIDER=groq
GROQ_LLM_MODEL=qwen/qwen3.8-27b
GROQ_COMMAND_MODEL=qwen/qwen3.8-27b
GROQ_WHISPER_MODEL=whisper-large-v3

MQTT_BROKER_HOST=localhost
MQTT_BROKER_PORT=1883
MQTT_DRY_RUN=true
ENABLED_DEVICES=Light1,Light2
```

---

## 🔌 Hardware Setup (ESP32)

### Pin Mapping
| Device Name | GPIO Pin | MQTT Set Topic | MQTT State (ACK) Topic |
| :--- | :--- | :--- | :--- |
| **Light1** | **GPIO 18** | `inot/devices/light1/set` | `inot/devices/light1/state` |
| **Light2** | **GPIO 19** | `inot/devices/light2/set` | `inot/devices/light2/state` |

*Wiring: GPIO ──> 220Ω Resistor ──> LED (Long leg / Anode) ──> LED (Short leg / Cathode) ──> GND*

> **Critical Placement Rule**: The camera numbers devices sequentially from **left to right** in the frame. Position the LED wired to GPIO 18 (`Light1`) on the left side, and GPIO 19 (`Light2`) to its right.

### Flash Firmware
1. Copy `esp32/config.example.h` to `esp32/config.h`:
   ```bash
   cp esp32/config.example.h esp32/config.h
   ```
2. Edit `esp32/config.h` with your Wi-Fi SSID, Password, and your laptop's LAN IP address (found via `ipconfig` or `ip a`).
3. Open `esp32/inot_esp32.ino` in Arduino IDE, select your ESP32 board, and click **Upload**.

---

## 🚦 Verification & Running

### Step 1: Run Preflight Diagnostics
Verify that your API keys, models, and network reachability pass:
```bash
python scripts/preflight.py
```

### Step 2: Run Unit Tests
```bash
pytest -o pythonpath=. tests -v
```

### Step 3: Start Mosquitto Broker (When using physical ESP32)
```bash
mosquitto -c mosquitto.conf -v
```

### Step 4: Run the Assistant
```bash
python main.py
```

1. **Scene Capture**: The webcam takes a snapshot of your room.
2. **Voice Onboarding**: Speak into your microphone:
   > *"two lights"*
3. **Annotation GUI**: An interactive window shows bounding boxes. Drag boxes to align with your LEDs, or press **`a`** to accept.
4. **Command Execution**: Give natural language commands:
   - *"Turn on Light 1"*
   - *"Turn off Light 1"*
   - *"Turn on the light near the window"* (resolved through spatial reasoning)
   - *"Exit"* (cleanly stops the assistant)

---

## 📊 Latency Metrics

All stage durations are logged to `logs/metrics.csv` with the following schema:
- `timestamp_iso`: UTC timestamp of the command execution
- `run_id`: Unique run identifier
- `platform`: Host device (`laptop` or `rpi`)
- `command_id`: Correlating identifier for multi-stage command runs
- `stage`: Measured pipeline stage (`nl_extract`, `spatial_gpt`, `stt_api`, `llm_decision`, `parse`, `mqtt_publish`, `esp32_ack`)
- `duration_ms`: Duration in milliseconds
- `note`: Context details or payload

---

## 📜 References & Acknowledgements
- **INOT Paper**: *Intelligence of Things: An Edge-Cloud Vision-Language-Action Architecture for Smart Environments* (arXiv:2504.13942).
- Built with [Groq](https://groq.com/), [Eclipse Mosquitto](https://mosquitto.org/), and [ESP32 Arduino](https://github.com/espressif/arduino-esp32).
