"""
INOT Mark1 - Web Dashboard Server
FastAPI backend providing REST and WebSocket APIs for device monitoring,
interactive toggles, voice input processing (Groq Whisper + Web Speech),
and real-time MQTT state synchronization.
"""

import os
import sys
import subprocess
from pathlib import Path

# Auto-switch to project virtual environment if executed with global Python
_base_dir = Path(__file__).resolve().parent
_venv_python_win = _base_dir / ".venv" / "Scripts" / "python.exe"
_venv_python_unix = _base_dir / ".venv" / "bin" / "python"
_target_venv = _venv_python_win if _venv_python_win.exists() else (_venv_python_unix if _venv_python_unix.exists() else None)

if _target_venv and Path(sys.executable).resolve() != _target_venv.resolve():
    sys.exit(subprocess.call([str(_target_venv)] + sys.argv))

import json
import time
import asyncio
import tempfile
import csv
from typing import Dict, List, Set, Optional
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

import mqtt_executor
import command_processor
import transcriptor
import safe_parse
import metrics

# Project paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
SPATIAL_FILE = BASE_DIR / "spatial_information.txt"
METRICS_FILE = BASE_DIR / "logs" / "metrics.csv"
ANNOTATED_IMG = BASE_DIR / "annotated_image.jpg"
SCENE_IMG = BASE_DIR / "images" / "current_scene.jpg"

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.add(connection)
        for dead in dead_connections:
            self.active_connections.discard(dead)

    def threadsafe_broadcast(self, message: dict):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)

manager = ConnectionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.loop = asyncio.get_running_loop()
    try:
        spatial_text = ""
        if SPATIAL_FILE.exists():
            with open(SPATIAL_FILE, "r", encoding="utf-8") as f:
                spatial_text = f.read()
        command_processor.init_conversation(spatial_text)
        print("[SERVER] Conversation processor initialized.")
    except Exception as e:
        print(f"[SERVER] [WARN] Conversation init error: {e}")

    try:
        mqtt_executor._get_client()
    except Exception as e:
        print(f"[SERVER] MQTT init note: {e}")

    yield

# Initialize FastAPI
app = FastAPI(title="INOT Mark1 Smart Home Dashboard", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Project paths
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)
SPATIAL_FILE = BASE_DIR / "spatial_information.txt"
METRICS_FILE = BASE_DIR / "logs" / "metrics.csv"
ANNOTATED_IMG = BASE_DIR / "annotated_image.jpg"
SCENE_IMG = BASE_DIR / "images" / "current_scene.jpg"

# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        dead_connections = set()
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead_connections.add(connection)
        for dead in dead_connections:
            self.active_connections.discard(dead)

    def threadsafe_broadcast(self, message: dict):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)

manager = ConnectionManager()

# Hook into MQTT executor state listener
def on_mqtt_event(event_type: str, data: dict):
    payload = {
        "type": event_type,
        "timestamp": time.time(),
        "data": data
    }
    manager.threadsafe_broadcast(payload)

mqtt_executor.add_state_listener(on_mqtt_event)

# Device spatial mapping metadata
DEVICE_METADATA = {
    "Light1": {
        "label": "Light 1 (Left Desk)",
        "icon": "lightbulb",
        "pin": "GPIO 18",
        "room": "Study Area / Desk",
        "spatial_position": "Left",
        "description": "Primary task illumination for left desk workspace"
    },
    "Light2": {
        "label": "Light 2 (Right Window)",
        "icon": "lightbulb",
        "pin": "GPIO 19",
        "room": "Window Area",
        "spatial_position": "Right",
        "description": "Ambient perimeter illumination near the right window"
    },
    "Light3": {
        "label": "Light 3 (Ceiling Fan / Aux)",
        "icon": "lightbulb",
        "pin": "GPIO 21",
        "room": "Central Room",
        "spatial_position": "Center",
        "description": "Central overhead lighting fixture"
    },
    "Light4": {
        "label": "Light 4 (Night / Accent)",
        "icon": "lightbulb",
        "pin": "GPIO 22",
        "room": "Nightstand / Corner",
        "spatial_position": "Corner",
        "description": "Warm low-intensity evening accent light"
    },
    "fan1": {
        "label": "Smart Fan (Right)",
        "icon": "fan",
        "pin": "Aux Relay",
        "room": "Right Ceiling",
        "spatial_position": "Right",
        "description": "Detected ceiling circulation fan"
    }
}

# API Endpoints
@app.get("/api/status")
def get_status():
    """Returns general system status, broker connectivity, and AI provider info."""
    broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
    broker_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    dry_run = os.getenv("MQTT_DRY_RUN", "false").lower() == "true"
    ai_provider = os.getenv("AI_PROVIDER", "groq").lower()
    llm_model = os.getenv("GROQ_COMMAND_MODEL", os.getenv("GROQ_LLM_MODEL", "qwen/qwen3.8-27b"))
    whisper_model = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
    camera_enabled = os.getenv("CAMERA_ENABLED", "true").lower() == "true"
    tts_enabled = os.getenv("TTS_ENABLED", "true").lower() == "true"

    return {
        "mqtt": {
            "connected": mqtt_executor.is_connected(),
            "dry_run": dry_run,
            "broker_host": broker_host,
            "broker_port": broker_port,
        },
        "esp32": {
            "status": mqtt_executor.get_esp32_status(),
        },
        "ai": {
            "provider": ai_provider,
            "llm_model": llm_model,
            "whisper_model": whisper_model,
        },
        "features": {
            "camera_enabled": camera_enabled,
            "tts_enabled": tts_enabled,
        },
        "enabled_devices": mqtt_executor.enabled_devices()
    }

@app.get("/api/devices")
def get_devices():
    """Returns all devices, their enabled status, state, and metadata."""
    enabled = [d.lower() for d in mqtt_executor.enabled_devices()]
    states = mqtt_executor.get_device_states()
    
    device_list = []
    for dev_name, info in mqtt_executor.ALL_DEVICES.items():
        is_enabled = dev_name.lower() in enabled
        curr_state = states.get(dev_name, "OFF").upper()
        meta = DEVICE_METADATA.get(dev_name, {
            "label": dev_name,
            "icon": "lightbulb",
            "pin": "N/A",
            "room": "General",
            "spatial_position": "Unknown",
            "description": f"Smart appliance {dev_name}"
        })
        device_list.append({
            "name": dev_name,
            "label": meta["label"],
            "state": curr_state,
            "enabled": is_enabled,
            "topic_set": info["topic"],
            "topic_state": info["state"],
            "pin": meta["pin"],
            "room": meta["room"],
            "spatial_position": meta["spatial_position"],
            "icon": meta["icon"],
            "description": meta["description"]
        })
    return {"devices": device_list}

@app.post("/api/device/{device_name}/toggle")
def toggle_device(device_name: str):
    """Toggles a device state between ON and OFF."""
    allowed = mqtt_executor.enabled_devices()
    canonical = None
    for d in allowed:
        if d.lower() == device_name.lower():
            canonical = d
            break
            
    if not canonical:
        # Fallback check across all devices if user wants to toggle even if not explicitly in ENABLED_DEVICES
        for d in mqtt_executor.ALL_DEVICES:
            if d.lower() == device_name.lower():
                canonical = d
                break

    if not canonical:
        raise HTTPException(status_code=404, detail=f"Device '{device_name}' not found.")

    current = mqtt_executor.get_device_state(canonical)
    next_cmd = "Off" if current == "ON" else "On"

    success = mqtt_executor.execute(canonical, next_cmd)
    new_state = mqtt_executor.get_device_state(canonical)

    return {
        "success": success,
        "device": canonical,
        "previous_state": current,
        "new_state": new_state,
        "command": next_cmd
    }

@app.post("/api/device/{device_name}/set")
def set_device(device_name: str, payload: dict):
    """Sets a device state explicitly to 'On' or 'Off'."""
    cmd = payload.get("state", "On").capitalize()
    if cmd not in ("On", "Off"):
        raise HTTPException(status_code=400, detail="State must be 'On' or 'Off'.")

    success = mqtt_executor.execute(device_name, cmd)
    return {
        "success": success,
        "device": device_name,
        "state": mqtt_executor.get_device_state(device_name),
        "command": cmd
    }

@app.post("/api/devices/batch")
def batch_action(payload: dict):
    """Performs batch actions like 'all_on', 'all_off', 'study_mode', 'night_mode'."""
    action = payload.get("action", "").lower()
    enabled = mqtt_executor.enabled_devices()
    results = {}

    if action == "all_on":
        for dev in enabled:
            results[dev] = mqtt_executor.execute(dev, "On")
    elif action == "all_off":
        for dev in enabled:
            results[dev] = mqtt_executor.execute(dev, "Off")
    elif action == "study_mode":
        # Turn on left/desk light, turn off other lights
        for dev in enabled:
            cmd = "On" if "1" in dev else "Off"
            results[dev] = mqtt_executor.execute(dev, cmd)
    elif action == "night_mode":
        # All off or low accent
        for dev in enabled:
            results[dev] = mqtt_executor.execute(dev, "Off")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown batch action '{action}'.")

    return {
        "action": action,
        "results": results,
        "states": mqtt_executor.get_device_states()
    }

@app.post("/api/command")
def process_command(payload: dict):
    """
    Processes natural language command using Groq Qwen with full spatial reasoning,
    then executes device actions via safe_parse and MQTT.
    """
    query = payload.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    spatial_text = None
    if SPATIAL_FILE.exists():
        try:
            with open(SPATIAL_FILE, "r", encoding="utf-8") as f:
                spatial_text = f.read()
        except Exception:
            pass

    try:
        result = command_processor.process_single_command(query, spatial_information=spatial_text, speak=False)
        result["device_states"] = mqtt_executor.get_device_states()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Command processing error: {e}")

@app.post("/api/voice/process-audio")
async def process_voice_audio(audio: UploadFile = File(...)):
    """
    Accepts an audio blob from the browser MediaRecorder, transcribes it via Groq Whisper,
    then evaluates spatial intent and executes device control.
    """
    suffix = Path(audio.filename).suffix or ".webm"
    temp_audio = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            contents = await audio.read()
            tmp.write(contents)
            temp_audio = tmp.name

        # Transcribe with Groq Whisper
        transcript_text = transcriptor.transcribe_file(temp_audio)

        if not transcript_text or not transcript_text.strip():
            return {
                "transcript": "",
                "message": "No speech detected in audio."
            }

        # Process the transcribed command
        spatial_text = None
        if SPATIAL_FILE.exists():
            try:
                with open(SPATIAL_FILE, "r", encoding="utf-8") as f:
                    spatial_text = f.read()
            except Exception:
                pass

        result = command_processor.process_single_command(transcript_text, spatial_information=spatial_text, speak=False)
        result["device_states"] = mqtt_executor.get_device_states()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio processing failed: {e}")
    finally:
        if temp_audio and os.path.exists(temp_audio):
            try:
                os.remove(temp_audio)
            except Exception:
                pass

@app.post("/api/voice/server-mic")
def trigger_server_mic():
    """
    Triggers the host machine's microphone directly using PyAudio / SoundDevice,
    records 5 seconds, transcribes via Groq Whisper, and executes the command.
    """
    try:
        transcript_text = transcriptor.transcribe()
        if not transcript_text or not transcript_text.strip():
            return {
                "transcript": "",
                "message": "No speech detected."
            }

        spatial_text = None
        if SPATIAL_FILE.exists():
            try:
                with open(SPATIAL_FILE, "r", encoding="utf-8") as f:
                    spatial_text = f.read()
            except Exception:
                pass

        result = command_processor.process_single_command(transcript_text, spatial_information=spatial_text, speak=False)
        result["device_states"] = mqtt_executor.get_device_states()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Server microphone capture failed: {e}")

@app.get("/api/spatial")
def get_spatial_info():
    """Returns spatial information text and available image artifacts."""
    content = ""
    if SPATIAL_FILE.exists():
        with open(SPATIAL_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "No spatial_information.txt found. Using default left-to-right device placement."

    has_annotated = ANNOTATED_IMG.exists()
    has_scene = SCENE_IMG.exists()

    return {
        "spatial_text": content,
        "has_annotated_image": has_annotated,
        "has_scene_image": has_scene,
        "image_url": "/api/scene-image" if (has_annotated or has_scene) else None
    }

@app.get("/api/scene-image")
def get_scene_image():
    """Serves the latest scene or annotated image."""
    if ANNOTATED_IMG.exists():
        return FileResponse(ANNOTATED_IMG, media_type="image/jpeg")
    if SCENE_IMG.exists():
        return FileResponse(SCENE_IMG, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="No scene image available.")

@app.get("/api/metrics")
def get_metrics(limit: int = 25):
    """Returns the most recent latency metrics from logs/metrics.csv."""
    if not METRICS_FILE.exists():
        return {"metrics": []}
    
    records = []
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            records = list(reader)
        # Return latest first
        latest = records[-limit:][::-1]
        return {"metrics": latest}
    except Exception as e:
        return {"metrics": [], "error": str(e)}

# WebSocket for real-time updates
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    # Send initial state snapshot
    try:
        await websocket.send_json({
            "type": "initial_state",
            "timestamp": time.time(),
            "data": {
                "states": mqtt_executor.get_device_states(),
                "esp32_status": mqtt_executor.get_esp32_status(),
                "mqtt_connected": mqtt_executor.is_connected(),
                "enabled_devices": mqtt_executor.enabled_devices(),
            }
        })
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                # Handle client-sent actions
                if msg.get("action") == "toggle":
                    dev = msg.get("device")
                    if dev:
                        curr = mqtt_executor.get_device_state(dev)
                        nxt = "Off" if curr == "ON" else "On"
                        mqtt_executor.execute(dev, nxt)
                elif msg.get("action") == "ping":
                    await websocket.send_json({"type": "pong", "time": time.time()})
            except Exception:
                pass
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)

# Static file serving
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"message": "Dashboard frontend loading... Please check static/index.html"})

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    print(f"\n========================================================")
    print(f"  [INOT Mark1] Smart Home Assistant Web Dashboard")
    print(f"  Access local URL:  http://localhost:{port}")
    print(f"  Access network:    http://127.0.0.1:{port}")
    print(f"========================================================\n")
    uvicorn.run("server:app", host=host, port=port, reload=False)
