"""
INOT Mark1 — Web Dashboard + Pi API Server

FastAPI backend providing:
  - REST API for device control, status, voice commands
  - WebSocket for real-time MQTT state sync (PC dashboard)
  - POST /context   receive room_context.json from onboarding PC
  - GET  /state     snapshot of all device states (for digital twin init)
  - GET  /context   retrieve current room context
"""

from __future__ import annotations

import base64
import csv
import json
import os
import sys
import time
import asyncio
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dotenv import load_dotenv
from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File,
    Form, Body, HTTPException, Request,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from src.hardware import mqtt_executor
from src.onboarding import onboarding_engine
from src.core import safe_parse
from src.core import metrics

# PC-only legacy modules are imported lazily inside endpoints if needed
_command_processor = None
_transcriptor = None

def _get_command_processor():
    global _command_processor
    if _command_processor is None:
        try:
            from src.ai import command_processor
            _command_processor = command_processor
        except Exception as e:
            print(f"[SERVER] [DEBUG] command_processor unavailable: {e}")
    return _command_processor

def _get_transcriptor():
    global _transcriptor
    if _transcriptor is None:
        try:
            from src.audio import transcriptor
            _transcriptor = transcriptor
        except Exception as e:
            print(f"[SERVER] [DEBUG] transcriptor unavailable: {e}")
    return _transcriptor

# Project paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # project root
SRC_WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = SRC_WEB_DIR / "static"
TWIN_DIR = SRC_WEB_DIR / "twin"
STATIC_DIR.mkdir(exist_ok=True)
SPATIAL_FILE = BASE_DIR / "spatial_information.txt"
METRICS_FILE = BASE_DIR / "logs" / "metrics.csv"
ANNOTATED_IMG = BASE_DIR / "annotated_image.jpg"
SCENE_IMG = BASE_DIR / "images" / "current_scene.jpg"


# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

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
        dead = set()
        for conn in list(self.active_connections):
            try:
                await conn.send_json(message)
            except Exception:
                dead.add(conn)
        for c in dead:
            self.active_connections.discard(c)

    def threadsafe_broadcast(self, message: dict):
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# MQTT listener → WebSocket broadcast
# ---------------------------------------------------------------------------

def on_mqtt_event(event_type: str, data: dict):
    manager.threadsafe_broadcast({
        "type": event_type,
        "timestamp": time.time(),
        "data": data,
    })


mqtt_executor.add_state_listener(on_mqtt_event)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    manager.loop = asyncio.get_running_loop()

    try:
        mqtt_executor._get_client()
    except Exception as e:
        print(f"[SERVER] MQTT init note: {e}")

    yield


app = FastAPI(
    title="INOT Mark1 Smart Home Dashboard",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Device metadata (static; room_context drives the live registry)
# ---------------------------------------------------------------------------

DEVICE_METADATA: Dict[str, dict] = {
    "Light1": {
        "label": "Light 1 (Left Desk)",
        "icon": "lightbulb",
        "pin": "GPIO 18",
        "room": "Study Area / Desk",
        "spatial_position": "Left",
        "description": "Primary task illumination for left desk workspace",
    },
    "Light2": {
        "label": "Light 2 (Right Window)",
        "icon": "lightbulb",
        "pin": "GPIO 19",
        "room": "Window Area",
        "spatial_position": "Right",
        "description": "Ambient perimeter illumination near the right window",
    },
    "Light3": {
        "label": "Light 3 (Ceiling / Aux)",
        "icon": "lightbulb",
        "pin": "GPIO 21",
        "room": "Central Room",
        "spatial_position": "Center",
        "description": "Central overhead lighting fixture",
    },
    "Light4": {
        "label": "Light 4 (Night / Accent)",
        "icon": "lightbulb",
        "pin": "GPIO 22",
        "room": "Nightstand / Corner",
        "spatial_position": "Corner",
        "description": "Warm low-intensity evening accent light",
    },
}


# ---------------------------------------------------------------------------
# Room context helpers
# ---------------------------------------------------------------------------

def _ctx_path() -> Path:
    p = Path(os.getenv("ROOM_CONTEXT_PATH", "room_context.json"))
    if not p.is_absolute():
        p = BASE_DIR / p
    return p


def _load_ctx_raw() -> Optional[dict]:
    p = _ctx_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _apply_room_context(data: dict) -> dict:
    """Validate, persist to ROOM_CONTEXT_PATH, reload registries, and notify WS clients."""
    try:
        from src.core import room_context as rc
        ok, errors = rc.validate(data)
        if not ok:
            raise HTTPException(
                status_code=422,
                detail={"message": "room_context schema validation failed", "errors": errors},
            )
    except ImportError:
        pass

    p = _ctx_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[SERVER] room_context.json saved to {p}")

    mqtt_executor.reload_devices()

    try:
        import src.pipeline as pipeline  # type: ignore[import]
        if hasattr(pipeline, "reload_context"):
            pipeline.reload_context()
    except ImportError:
        pass

    devices = data.get("devices", [])
    device_ids = [d.get("id") for d in devices]
    manager.threadsafe_broadcast({
        "type": "context_updated",
        "timestamp": time.time(),
        "data": {
            "room_name": data.get("room_name"),
            "device_count": len(devices),
            "device_ids": device_ids,
        },
    })

    return {
        "status": "ok",
        "saved_to": str(p),
        "room_name": data.get("room_name"),
        "device_count": len(devices),
        "device_ids": device_ids,
    }


# ---------------------------------------------------------------------------
# API: system status
# ---------------------------------------------------------------------------

@app.get("/api/status")
def get_status():
    from src.core.config import settings
    return {
        "mqtt": {
            "connected": mqtt_executor.is_connected(),
            "dry_run": settings.mqtt_dry_run,
            "broker_host": settings.mqtt_broker_host,
            "broker_port": settings.mqtt_broker_port,
        },
        "esp32": {
            "status": mqtt_executor.get_esp32_status(),
        },
        "ai": {
            "llm_backend": settings.llm_backend,
            "stt_backend": settings.stt_backend,
            "llm_model": (
                settings.ollama_model
                if settings.llm_backend == "ollama"
                else settings.groq_command_model
            ),
        },
        "features": {
            "camera_enabled": settings.camera_enabled,
            "tts_enabled": settings.tts_enabled,
            "wake_word_enabled": settings.wake_word_enabled,
        },
        "platform": settings.platform,
        "enabled_devices": mqtt_executor.enabled_devices(),
    }


# ---------------------------------------------------------------------------
# API: room context (Pi onboarding endpoint)
# ---------------------------------------------------------------------------

@app.post("/context")
async def post_context(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be valid JSON")

    return _apply_room_context(data)


@app.get("/context")
def get_context():
    data = _load_ctx_raw()
    if data is None:
        raise HTTPException(status_code=404, detail="No room_context.json found")
    return data


# ---------------------------------------------------------------------------
# API: Pi-safe Cloud Onboarding Engine (open endpoints)
# ---------------------------------------------------------------------------

async def _run_onboarding_pipeline(
    *,
    image_bytes: bytes,
    room_name: Optional[str] = None,
    device_hints: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    save: bool = False,
    push_to_rpi: bool = False,
    rpi_url: Optional[str] = None,
    include_raw: bool = False,
) -> Dict[str, Any]:
    """Shared handler for multipart upload and base64 JSON onboarding requests."""
    try:
        onboarding_engine.detect_image_mime(image_bytes)
        await run_in_threadpool(onboarding_engine.save_scene_image, image_bytes)
        result = await run_in_threadpool(
            onboarding_engine.generate_context,
            image_bytes,
            room_name=room_name,
            device_hints=device_hints,
            provider=provider,
            model=model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloud onboarding failed: {exc}")

    draft_record = {
        "created_at": time.time(),
        "provider": result["provider"],
        "model": result["model"],
        "mime_type": result["mime_type"],
        "image_bytes": result["image_bytes"],
        "duration_ms": result["duration_ms"],
        "validation": result["validation"],
        "context": result["context"],
    }
    await run_in_threadpool(onboarding_engine.save_draft, draft_record)

    apply_result = None
    if save:
        if not result["validation"]["valid"]:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Generated context failed room_context validation; not saved.",
                    "errors": result["validation"]["errors"],
                    "context": result["context"],
                },
            )
        apply_result = _apply_room_context(result["context"])

    rpi_push_result = None
    if push_to_rpi:
        try:
            rpi_push_result = await run_in_threadpool(
                onboarding_engine.push_context_to_rpi,
                result["context"],
                rpi_url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to push context to RPi: {exc}")

    response: Dict[str, Any] = {
        "status": "ok",
        "provider": result["provider"],
        "model": result["model"],
        "mime_type": result["mime_type"],
        "image_bytes": result["image_bytes"],
        "duration_ms": result["duration_ms"],
        "image_url": "/api/scene-image",
        "validation": result["validation"],
        "context": result["context"],
        "saved": bool(apply_result),
        "apply_result": apply_result,
        "pushed_to_rpi": bool(rpi_push_result),
        "rpi_push": rpi_push_result,
    }
    if include_raw:
        response["raw"] = result.get("raw", "")
    return response


@app.get("/api/onboard/status")
def onboard_status():
    """Readiness and configuration status of the Pi-safe cloud onboarding engine."""
    return onboarding_engine.get_status()


@app.post("/api/onboard/upload")
async def onboard_upload(
    image: UploadFile = File(...),
    room_name: Optional[str] = Form(None),
    device_hints: Optional[str] = Form(None),
    provider: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    save: bool = Form(False),
    push_to_rpi: bool = Form(False),
    rpi_url: Optional[str] = Form(None),
    include_raw: bool = Form(False),
):
    """
    Upload a room image (multipart/form-data), send it to the configured cloud
    VLM/endpoint, and receive validated ``room_context`` JSON.
    """
    image_bytes = await image.read()
    return await _run_onboarding_pipeline(
        image_bytes=image_bytes,
        room_name=room_name,
        device_hints=device_hints,
        provider=provider,
        model=model,
        save=save,
        push_to_rpi=push_to_rpi,
        rpi_url=rpi_url,
        include_raw=include_raw,
    )


@app.post("/api/onboard/analyze")
async def onboard_analyze_base64(payload: dict):
    """
    JSON base64 alternative to ``/api/onboard/upload``.
    Body: ``{"image_base64": "...", "room_name": "...", "device_hints": "...", ...}``.
    """
    raw_b64 = str(payload.get("image_base64") or "").strip()
    if not raw_b64:
        raise HTTPException(status_code=400, detail="'image_base64' is required.")
    if raw_b64.startswith("data:") and "," in raw_b64:
        raw_b64 = raw_b64.split(",", 1)[1]
    try:
        image_bytes = base64.b64decode(raw_b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data.")

    return await _run_onboarding_pipeline(
        image_bytes=image_bytes,
        room_name=payload.get("room_name"),
        device_hints=payload.get("device_hints"),
        provider=payload.get("provider"),
        model=payload.get("model"),
        save=bool(payload.get("save", False)),
        push_to_rpi=bool(payload.get("push_to_rpi", False)),
        rpi_url=payload.get("rpi_url"),
        include_raw=bool(payload.get("include_raw", False)),
    )


@app.get("/api/onboard/draft")
def onboard_get_draft():
    """Return the most recently generated onboarding context draft."""
    draft = onboarding_engine.load_draft()
    if draft is None:
        raise HTTPException(status_code=404, detail="No onboarding draft generated yet.")
    return draft


@app.post("/api/onboard/validate")
def onboard_validate_context(payload: dict, normalize: bool = True):
    """Validate (and optionally normalize) a candidate ``room_context`` JSON payload."""
    candidate = payload.get("context") if isinstance(payload.get("context"), dict) else payload
    try:
        from src.core import room_context as rc
        data = onboarding_engine.normalize_context(candidate) if normalize else candidate
        ok, errors = rc.validate(data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "valid": ok,
        "errors": errors,
        "context": data,
    }


@app.post("/api/onboard/apply")
async def onboard_apply(
    payload: Optional[dict] = Body(default=None),
    rpi_url: Optional[str] = None,
):
    """
    Commit a generated/edited ``room_context`` JSON locally (saving to
    ``ROOM_CONTEXT_PATH`` and reloading MQTT + pipeline), and optionally forward
    it to a target Raspberry Pi.
    """
    target_rpi = rpi_url
    if payload is None or payload == {}:
        draft = onboarding_engine.load_draft()
        if not draft or "context" not in draft:
            raise HTTPException(
                status_code=404,
                detail="No request body provided and no onboarding draft available.",
            )
        ctx = draft["context"]
    else:
        if not target_rpi and isinstance(payload.get("rpi_url"), str):
            target_rpi = payload.get("rpi_url")
        ctx = payload.get("context") if isinstance(payload.get("context"), dict) else payload

    result = _apply_room_context(ctx)

    if target_rpi:
        try:
            result["rpi_push"] = await run_in_threadpool(
                onboarding_engine.push_context_to_rpi, ctx, target_rpi
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Saved locally, but RPi push failed: {exc}")

    return result


@app.post("/api/onboard/push-to-rpi")
async def onboard_push_to_rpi(payload: Optional[dict] = Body(default=None)):
    """
    Push a ``room_context`` JSON (from request body, active ``room_context.json``,
    or latest onboarding draft) to a Raspberry Pi's ``POST /context`` endpoint.
    """
    data = payload or {}
    rpi_url = data.get("rpi_url")
    ctx = data.get("context")
    if ctx is None and "devices" in data:
        ctx = data
    if ctx is None:
        ctx = _load_ctx_raw()
    if ctx is None:
        draft = onboarding_engine.load_draft()
        if draft and isinstance(draft.get("context"), dict):
            ctx = draft["context"]
    if ctx is None:
        raise HTTPException(
            status_code=404,
            detail="No room_context available to push (provide 'context' in body or onboard first).",
        )

    try:
        push_res = await run_in_threadpool(
            onboarding_engine.push_context_to_rpi, ctx, rpi_url
        )
        return {"status": "ok", **push_res}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to push context to RPi: {exc}")


# ---------------------------------------------------------------------------
# API: device state snapshot
# ---------------------------------------------------------------------------

@app.get("/state")
def get_state():
    ctx = _load_ctx_raw()
    enabled = {d.lower() for d in mqtt_executor.enabled_devices()}
    devices_out = {}
    for name, state_val in mqtt_executor.get_device_states().items():
        meta = ALL_DEVICES_META(name)
        dev_id = mqtt_executor.ALL_DEVICES.get(name, {}).get("id", name.lower())
        devices_out[dev_id] = {
            "name": name,
            "state": state_val,
            "enabled": name.lower() in enabled,
            "set_topic": f"inot/devices/{dev_id}/set",
            "state_topic": f"inot/devices/{dev_id}/state",
            **meta,
        }
    return {
        "devices": devices_out,
        "mqtt_connected": mqtt_executor.is_connected(),
        "esp32_status": mqtt_executor.get_esp32_status(),
        "timestamp": time.time(),
        "room_context_loaded": ctx is not None,
    }


def ALL_DEVICES_META(name: str) -> dict:
    base = DEVICE_METADATA.get(name, {})
    ctx = _load_ctx_raw()
    if ctx:
        name_lower = name.lower()
        for dev in ctx.get("devices", []):
            dev_name = str(dev.get("name", ""))
            dev_id = str(dev.get("id", ""))
            if dev_name == name or dev_name.lower() == name_lower or dev_id.lower() == name_lower:
                dev_type = dev.get("type", "light")
                zone = dev.get("zone", "room")
                x = dev.get("x", 0.5)
                y = dev.get("y", 0.5)
                return {
                    "type": dev_type,
                    "zone": zone,
                    "x": x,
                    "y": y,
                    "near": dev.get("near", []),
                    "label": base.get("label", dev_name or name),
                    "icon": base.get("icon", dev_type),
                }
    return {
        "type": "light",
        "zone": "room",
        "x": 0.5,
        "y": 0.5,
        "near": [],
        "label": base.get("label", name),
        "icon": base.get("icon", "lightbulb"),
    }


# ---------------------------------------------------------------------------
# API: devices list
# ---------------------------------------------------------------------------

@app.get("/api/devices")
def get_devices():
    enabled = [d.lower() for d in mqtt_executor.enabled_devices()]
    states = mqtt_executor.get_device_states()
    device_list = []
    for dev_name, info in mqtt_executor.ALL_DEVICES.items():
        is_enabled = dev_name.lower() in enabled
        curr_state = states.get(dev_name, "OFF").upper()
        dyn_meta = ALL_DEVICES_META(dev_name)
        static_meta = DEVICE_METADATA.get(dev_name, {})
        x = dyn_meta.get("x", 0.5)
        y = dyn_meta.get("y", 0.5)
        zone = dyn_meta.get("zone", "room")
        dev_type = dyn_meta.get("type", "light")
        near = dyn_meta.get("near", [])
        device_list.append({
            "name": dev_name,
            "id": info.get("id", dev_name.lower()),
            "label": static_meta.get("label") or dyn_meta.get("label") or dev_name,
            "type": dev_type,
            "state": curr_state,
            "enabled": is_enabled,
            "topic_set": info["topic"],
            "topic_state": info["state"],
            "pin": static_meta.get("pin", "MQTT"),
            "room": static_meta.get("room") or zone,
            "zone": zone,
            "x": x,
            "y": y,
            "near": near,
            "spatial_position": static_meta.get("spatial_position") or f"({float(x):.2f}, {float(y):.2f})",
            "icon": static_meta.get("icon") or dev_type,
            "description": static_meta.get("description") or f"Smart {dev_type} in {zone}",
        })
    return {"devices": device_list}


# ---------------------------------------------------------------------------
# API: device control
# ---------------------------------------------------------------------------

@app.post("/api/device/{device_name}/toggle")
def toggle_device(device_name: str):
    allowed = mqtt_executor.enabled_devices()
    canonical = None
    for d in allowed:
        if d.lower() == device_name.lower():
            canonical = d
            break
    if not canonical:
        for d in mqtt_executor.ALL_DEVICES:
            if d.lower() == device_name.lower():
                canonical = d
                break
    if not canonical:
        raise HTTPException(status_code=404, detail=f"Device '{device_name}' not found.")

    current = mqtt_executor.get_device_state(canonical)
    next_cmd = "Off" if current == "ON" else "On"
    success = mqtt_executor.execute(canonical, next_cmd)
    return {
        "success": success,
        "device": canonical,
        "previous_state": current,
        "new_state": mqtt_executor.get_device_state(canonical),
        "command": next_cmd,
    }


@app.post("/api/device/{device_name}/set")
def set_device(device_name: str, payload: dict):
    cmd = payload.get("state", "On").capitalize()
    if cmd not in ("On", "Off"):
        raise HTTPException(status_code=400, detail="State must be 'On' or 'Off'.")
    canonical = device_name
    for d, info in mqtt_executor.ALL_DEVICES.items():
        if d.lower() == device_name.lower() or info.get("id", "").lower() == device_name.lower():
            canonical = d
            break
    success = mqtt_executor.execute(canonical, cmd)
    return {
        "success": success,
        "device": canonical,
        "state": mqtt_executor.get_device_state(canonical),
        "command": cmd,
    }


@app.post("/api/devices/batch")
def batch_action(payload: dict):
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
        for dev in enabled:
            zone = ALL_DEVICES_META(dev).get("zone", "").lower()
            cmd = "On" if ("1" in dev or zone in ("desk", "study")) else "Off"
            results[dev] = mqtt_executor.execute(dev, cmd)
    elif action == "night_mode":
        for dev in enabled:
            results[dev] = mqtt_executor.execute(dev, "Off")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown batch action '{action}'.")
    return {"action": action, "results": results, "states": mqtt_executor.get_device_states()}


# ---------------------------------------------------------------------------
# API: command processing
# ---------------------------------------------------------------------------

@app.post("/api/command")
def process_command_endpoint(payload: dict):
    query = payload.get("query", "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query cannot be empty.")

    try:
        from src import pipeline
        result = pipeline.process_command(query)
        result["device_states"] = mqtt_executor.get_device_states()
        return result
    except ImportError:
        pass

    cp = _get_command_processor()
    if cp:
        spatial_text = None
        if SPATIAL_FILE.exists():
            try:
                spatial_text = SPATIAL_FILE.read_text(encoding="utf-8")
            except Exception:
                pass
        try:
            result = cp.process_single_command(
                query, spatial_information=spatial_text, speak=False
            )
            result["device_states"] = mqtt_executor.get_device_states()
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Command processing error: {e}")

    raise HTTPException(status_code=503, detail="No command processor available.")


# ---------------------------------------------------------------------------
# API: voice (audio upload → STT → command)
# ---------------------------------------------------------------------------

@app.post("/api/voice/process-audio")
async def process_voice_audio(audio: UploadFile = File(...)):
    suffix = Path(audio.filename or "clip.wav").suffix or ".webm"
    temp_audio = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await audio.read())
            temp_audio = tmp.name

        try:
            from src.audio import stt_backend
            transcript_text = stt_backend.transcribe_file(temp_audio)
        except ImportError:
            tr = _get_transcriptor()
            if tr:
                transcript_text = tr.transcribe_file(temp_audio)
            else:
                raise HTTPException(status_code=503, detail="No STT backend available.")

        if not transcript_text or not transcript_text.strip():
            return {"transcript": "", "message": "No speech detected in audio."}

        try:
            from src import pipeline
            result = pipeline.process_command(transcript_text)
        except ImportError:
            cp = _get_command_processor()
            if cp:
                spatial_text = SPATIAL_FILE.read_text() if SPATIAL_FILE.exists() else None
                result = cp.process_single_command(transcript_text, spatial_information=spatial_text, speak=False)
            else:
                raise HTTPException(status_code=503, detail="No command processor.")

        result["device_states"] = mqtt_executor.get_device_states()
        result["transcript"] = transcript_text
        return result
    except HTTPException:
        raise
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
    try:
        from src.audio import stt_backend
        wav_path = stt_backend.record_audio()
        transcript_text = stt_backend.transcribe_file(wav_path)
    except ImportError:
        tr = _get_transcriptor()
        if tr:
            transcript_text = tr.transcribe()
        else:
            raise HTTPException(status_code=503, detail="No audio backend available.")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    if not transcript_text or not transcript_text.strip():
        return {"transcript": "", "message": "No speech detected."}

    try:
        from src import pipeline
        result = pipeline.process_command(transcript_text)
    except ImportError:
        cp = _get_command_processor()
        if cp:
            spatial_text = SPATIAL_FILE.read_text() if SPATIAL_FILE.exists() else None
            result = cp.process_single_command(transcript_text, spatial_information=spatial_text, speak=False)
        else:
            raise HTTPException(status_code=503, detail="No command processor.")

    result["device_states"] = mqtt_executor.get_device_states()
    result["transcript"] = transcript_text
    return result


# ---------------------------------------------------------------------------
# API: spatial / scene image
# ---------------------------------------------------------------------------

@app.get("/api/spatial")
def get_spatial_info():
    scene_img = onboarding_engine._scene_image_path()
    content = ""
    if SPATIAL_FILE.exists():
        content = SPATIAL_FILE.read_text(encoding="utf-8")
    else:
        ctx = _load_ctx_raw()
        if ctx:
            try:
                from src.core import room_context as rc
                content = rc.build_prompt_context(ctx)
            except Exception:
                content = json.dumps(ctx, indent=2)
        else:
            content = "No spatial_information.txt found. Using room_context.json if available."
    return {
        "spatial_text": content,
        "has_annotated_image": ANNOTATED_IMG.exists(),
        "has_scene_image": scene_img.exists() or SCENE_IMG.exists(),
        "image_url": "/api/scene-image" if (ANNOTATED_IMG.exists() or scene_img.exists() or SCENE_IMG.exists()) else None,
    }


@app.get("/api/scene-image")
def get_scene_image():
    scene_img = onboarding_engine._scene_image_path()
    candidates = []
    if ANNOTATED_IMG.exists():
        candidates.append(ANNOTATED_IMG)
    if scene_img.exists():
        candidates.append(scene_img)
    elif SCENE_IMG.exists():
        candidates.append(SCENE_IMG)
    if not candidates:
        raise HTTPException(status_code=404, detail="No scene image available.")

    # Serve whichever scene image was updated most recently (e.g. newly uploaded room photo)
    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        mime_type = onboarding_engine.detect_image_mime(chosen.read_bytes()[:16])
    except Exception:
        mime_type = "image/jpeg"
    return FileResponse(chosen, media_type=mime_type)


# ---------------------------------------------------------------------------
# API: metrics
# ---------------------------------------------------------------------------

@app.get("/api/metrics")
def get_metrics(limit: int = 25):
    if not METRICS_FILE.exists():
        return {"metrics": []}
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            records = list(csv.DictReader(f))
        return {"metrics": records[-limit:][::-1]}
    except Exception as e:
        return {"metrics": [], "error": str(e)}


# ---------------------------------------------------------------------------
# WebSocket: real-time MQTT state sync
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await websocket.send_json({
            "type": "initial_state",
            "timestamp": time.time(),
            "data": {
                "states": mqtt_executor.get_device_states(),
                "esp32_status": mqtt_executor.get_esp32_status(),
                "mqtt_connected": mqtt_executor.is_connected(),
                "enabled_devices": mqtt_executor.enabled_devices(),
            },
        })
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
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


# ---------------------------------------------------------------------------
# Static file serving
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if TWIN_DIR.exists():
    app.mount("/twin", StaticFiles(directory=TWIN_DIR, html=True), name="twin")


@app.get("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"message": "INOT Mark1 running. Add static/index.html for the dashboard."})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    print(f"\n{'='*56}")
    print(f"  INOT Mark1 Smart Home Server")
    print(f"  Dashboard:    http://localhost:{port}")
    print(f"  Digital Twin: http://localhost:{port}/twin")
    print(f"  API:          http://localhost:{port}/api/status")
    print(f"{'='*56}\n")
    uvicorn.run("src.web.server:app", host=host, port=port, reload=False)
