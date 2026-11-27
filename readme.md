# 🧠 INOT Mark1 — VLM-Based Smart Home Assistant

An intelligent, vision-language-action (VLA) smart home system that enables real-time spatial voice control of physical appliances using webcam perception, spatial reasoning, and an open MQTT + ESP32 hardware architecture.

Based on the research architecture in **INOT (arXiv:2504.13942)**, this implementation eliminates proprietary cloud dependencies (replacing Tuya with ESP32 + local Mosquitto MQTT) and unifies the AI pipeline under **Groq** for high-speed, low-latency reasoning.

---

## ⚡ System Architecture

```text
Phone/Laptop Mic ──> src/audio/stt_backend.py (Vosk/Whisper/Groq STT)
                            │
                      src/ai/fast_path.py (rule-based intent matching)
                            │  (fallback)
                      src/ai/llm_backend.py (Ollama local / Groq cloud LLM)
                            │
                      src/core/safe_parse.py (Secure parsing, no eval)
                            │
                      src/hardware/mqtt_executor.py (paho-mqtt 2.x)
                            │
                      Mosquitto Broker (TCP 1883) ──Wi-Fi──> ESP32 Dev Board
                                                                  │
                                                        GPIO 18 / 19 ──> LED1 / LED2
                                                                  │
                      State Ack (inot/devices/+/state) <──────────┘
```

---

## 📁 Project Structure

```text
.
├── main.py                          # CLI onboarding entry point (thin wrapper)
├── pipeline.py                      # Runtime pipeline entry point (thin wrapper)
├── server.py                        # Web server entry point (thin wrapper)
├── requirements.txt                 # Python dependencies
├── .env                             # Environment config (gitignored)
│
├── src/                             # ════ Main source package ════
│   ├── __init__.py
│   ├── main.py                      # CLI onboarding (camera → annotate → spatial → commands)
│   ├── pipeline.py                  # Runtime pipeline (STT → fast-path/LLM → MQTT)
│   │
│   ├── core/                        # Shared utilities & config
│   │   ├── config.py                # Central Settings dataclass from .env
│   │   ├── metrics.py               # Latency instrumentation to logs/metrics.csv
│   │   ├── safe_parse.py            # AST-based safe parser (no eval)
│   │   └── room_context.py          # room_context.json loader, validator, queries
│   │
│   ├── ai/                          # AI & decision modules
│   │   ├── llm_backend.py           # Ollama / Groq LLM abstraction
│   │   ├── fast_path.py             # Deterministic rule-based intent matcher
│   │   ├── command_processor.py     # Conversational command loop (legacy PC)
│   │   ├── nl_processor.py          # NL device extraction ("two lights" → {light:2})
│   │   └── spatial_inferencer.py    # Spatial relationship reasoning via VLM
│   │
│   ├── audio/                       # Speech-to-text modules
│   │   ├── stt_backend.py           # Vosk / faster-whisper / Groq Whisper abstraction
│   │   └── transcriptor.py          # Legacy Groq-only STT + mic recording
│   │
│   ├── onboarding/                  # PC-side onboarding pipeline
│   │   ├── onboarder.py             # Orchestrates: mic → NL → annotate → spatial
│   │   ├── annotator.py             # OWLv2 detection + interactive bbox GUI
│   │   └── camera_capture.py        # Webcam capture with auto-exposure warmup
│   │
│   ├── hardware/                    # Device execution layer
│   │   └── mqtt_executor.py         # MQTT publisher, ACK monitor, device registry
│   │
│   └── web/                         # Web dashboard & API server
│       ├── server.py                # FastAPI backend (REST + WebSocket + twin)
│       ├── static/                  # Dashboard HTML/CSS/JS
│       └── twin/                    # Digital twin single-page app
│
├── config/                          # ════ Configuration templates ════
│   ├── .env.example                 # Environment variable template
│   ├── mosquitto.conf               # Mosquitto broker config
│   ├── docker-compose.yml           # Docker services (Mosquitto + Ollama)
│   └── room_context.example.json    # Example room context schema
│
├── firmware/                        # ════ ESP32 firmware ════
│   └── inot_esp32/
│       ├── inot_esp32.ino           # Arduino C++ firmware with LWT & ACK
│       ├── config.h                 # Wi-Fi & MQTT config (gitignored)
│       └── config.example.h         # Config template
│
├── data/                            # ════ Data files ════
│   └── room_context_room.json       # Example room context for benchmarks
│
├── scripts/                         # ════ Utility scripts ════
│   ├── preflight.py                 # PC preflight diagnostics
│   ├── preflight_pi.py              # Raspberry Pi preflight diagnostics
│   ├── run_benchmark.py             # 120+ test case evaluation suite
│   ├── metrics_summary.py           # Metrics CSV analysis
│   ├── mic_helper.py                # USB mic detection & test
│   ├── setup_pi.sh                  # Raspberry Pi setup script
│   └── start_broker.bat             # Windows Mosquitto launcher
│
├── deploy/                          # ════ Deployment configs ════
│   └── systemd/
│       ├── inot-mark1@.service      # Pipeline systemd unit
│       └── inot-twin@.service       # Server/twin systemd unit
│
├── docs/                            # ════ Documentation ════
│   ├── readme.md                    # Original detailed README
│   └── README_PI.md                 # Raspberry Pi setup guide
│
├── tests/                           # ════ Test suite ════
│   ├── test_config.py               # Config loading tests
│   ├── test_safe_parse.py           # Parsing security tests
│   ├── test_fast_path.py            # Intent matcher tests
│   ├── test_room_context.py         # Room context validation tests
│   ├── test_executor.py             # MQTT executor tests
│   ├── test_pipeline.py             # Pipeline integration tests
│   ├── test_e2e.py                  # End-to-end pipeline tests
│   ├── test_server.py               # FastAPI server tests
│   └── fixtures/
│       └── test_command.wav         # Test audio fixture
│
├── images/                          # Captured scene images
└── logs/                            # Runtime metrics CSV logs
```

---

## 🛠️ Quick Start

### 1. Install Dependencies
```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
copy config\.env.example .env
# Edit .env with your GROQ_API_KEY
```

### 3. Run Pipeline (Pi / Headless)
```bash
python pipeline.py --text "turn on light1"       # direct text
python pipeline.py --wav tests/fixtures/test_command.wav  # WAV file
python pipeline.py --loop                         # live mic loop
```

### 4. Run Web Dashboard
```bash
python server.py
# Open http://localhost:8000
```

### 5. Run Tests
```bash
pytest tests/ -v
```

---

## 📊 Current Status

| Component | Status | Notes |
|:---|:---|:---|
| **Edge AI Pipeline** | ✅ Tested on RPi | Complex commands via Ollama (qwen2.5) with high accuracy |
| **Fast-Path Rules** | ✅ Working | Deterministic matching for simple on/off commands |
| **Rule Parser** | ✅ Done (on RPi) | Updated directly on RPi — needs to be pushed to git |
| **Onboarding Engine** | 🚧 In Progress | Photo → cloud VLM → room_context.json (friend working on this) |
| **Phone Mic Input** | ⏳ Next | Stream phone mic audio to RPi for voice commands |
| **Web Dashboard** | ✅ Working | Real-time device control, WebSocket state sync |

---

## 📜 References
- **INOT Paper**: *Intelligence of Things: An Edge-Cloud VLA Architecture for Smart Environments* (arXiv:2504.13942)
- Built with [Groq](https://groq.com/), [Ollama](https://ollama.ai/), [Eclipse Mosquitto](https://mosquitto.org/), and [ESP32 Arduino](https://github.com/espressif/arduino-esp32)

