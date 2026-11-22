"""
INOT Mark1 — Central configuration loaded from .env

Every tunable lives here. Modules import `from config import settings`.
Values are read from environment / .env at import time, cached as a
frozen dataclass-like object.  No secrets in this file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

# Load .env from project root (idempotent)
_BASE_DIR = Path(__file__).resolve().parent
load_dotenv(_BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")


def _int(name: str, default: int = 0) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float = 0.0) -> float:
    return float(os.getenv(name, str(default)))


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _list(name: str, default: str = "") -> List[str]:
    raw = os.getenv(name, default)
    return [s.strip() for s in raw.split(",") if s.strip()]


@dataclass
class Settings:
    """Immutable-ish project settings.  Reload with ``Settings.load()``."""

    # ── Platform ──────────────────────────────────────────────────────
    platform: str = "pc"                # pc | rpi
    platform_label: str = "laptop"
    base_dir: Path = _BASE_DIR

    # ── STT ───────────────────────────────────────────────────────────
    stt_backend: str = "groq"           # vosk | faster_whisper | groq
    groq_whisper_model: str = "whisper-large-v3"
    vosk_model_path: str = ""           # auto-detected if empty
    faster_whisper_model: str = "tiny.en"
    faster_whisper_compute: str = "int8"
    audio_device: Optional[str] = None  # sounddevice device name/index
    audio_rate: int = 16000
    audio_channels: int = 1
    audio_record_seconds: int = 5

    # ── LLM ───────────────────────────────────────────────────────────
    llm_backend: str = "groq"           # ollama | groq
    groq_api_key: str = ""
    groq_llm_model: str = "qwen/qwen3.8-27b"
    groq_command_model: str = "qwen/qwen3.8-27b"
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:1.5b"
    ollama_fallback_model: str = "qwen2.5:0.5b"
    ollama_keep_alive: str = "-1"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 256
    llm_timeout_s: float = 30.0

    # ── MQTT ──────────────────────────────────────────────────────────
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_ws_port: int = 9001
    mqtt_keepalive: int = 60
    mqtt_dry_run: bool = True
    mqtt_client_id: str = "inot-mark1"
    ack_required: bool = False
    ack_timeout_s: float = 2.0
    mqtt_retry_count: int = 2
    mqtt_retry_delay_s: float = 0.5

    # ── Devices ───────────────────────────────────────────────────────
    enabled_devices: List[str] = field(default_factory=lambda: ["Light1", "Light2"])
    room_context_path: str = "room_context.json"

    # ── Camera / Vision (PC onboarding only) ──────────────────────────
    camera_enabled: bool = True
    camera_index: int = 0
    camera_warmup_frames: int = 20
    image_path: str = "images/current_scene.jpg"
    force_onboarding: bool = True
    annotation_auto_accept: bool = False

    # ── Audio / TTS ───────────────────────────────────────────────────
    tts_enabled: bool = True

    # ── Wake word / VAD (optional) ────────────────────────────────────
    wake_word_enabled: bool = False

    # ── Server ────────────────────────────────────────────────────────
    dashboard_host: str = "0.0.0.0"
    dashboard_port: int = 8000

    # ── Groq fallback for LLM ─────────────────────────────────────────
    llm_groq_fallback: bool = False     # Try Groq if local LLM fails validation

    # ── AI Provider (legacy compat) ───────────────────────────────────
    ai_provider: str = "groq"

    @classmethod
    def load(cls) -> "Settings":
        """Build a Settings object from current environment."""
        load_dotenv(_BASE_DIR / ".env")
        return cls(
            platform=_str("PLATFORM", "pc").lower(),
            platform_label=_str("PLATFORM_LABEL", "laptop"),
            # STT
            stt_backend=_str("STT_BACKEND", "groq").lower(),
            groq_whisper_model=_str("GROQ_WHISPER_MODEL", "whisper-large-v3"),
            vosk_model_path=_str("VOSK_MODEL_PATH", ""),
            faster_whisper_model=_str("FASTER_WHISPER_MODEL", "tiny.en"),
            faster_whisper_compute=_str("FASTER_WHISPER_COMPUTE", "int8"),
            audio_device=_str("AUDIO_DEVICE", "") or None,
            audio_rate=_int("AUDIO_RATE", 16000),
            audio_channels=_int("AUDIO_CHANNELS", 1),
            audio_record_seconds=_int("AUDIO_RECORD_SECONDS", 5),
            # LLM
            llm_backend=_str("LLM_BACKEND", "groq").lower(),
            groq_api_key=_str("GROQ_API_KEY", ""),
            groq_llm_model=_str("GROQ_LLM_MODEL", "qwen/qwen3.8-27b"),
            groq_command_model=_str("GROQ_COMMAND_MODEL", "qwen/qwen3.8-27b"),
            ollama_base_url=_str("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            ollama_model=_str("OLLAMA_MODEL", "qwen2.5:1.5b"),
            ollama_fallback_model=_str("OLLAMA_FALLBACK_MODEL", "qwen2.5:0.5b"),
            ollama_keep_alive=_str("OLLAMA_KEEP_ALIVE", "-1"),
            llm_temperature=_float("LLM_TEMPERATURE", 0.1),
            llm_max_tokens=_int("LLM_MAX_TOKENS", 256),
            llm_timeout_s=_float("LLM_TIMEOUT_S", 30.0),
            # MQTT
            mqtt_broker_host=_str("MQTT_BROKER_HOST", "localhost"),
            mqtt_broker_port=_int("MQTT_BROKER_PORT", 1883),
            mqtt_ws_port=_int("MQTT_WS_PORT", 9001),
            mqtt_keepalive=_int("MQTT_KEEPALIVE", 60),
            mqtt_dry_run=_bool("MQTT_DRY_RUN", True),
            mqtt_client_id=_str("MQTT_CLIENT_ID", "inot-mark1"),
            ack_required=_bool("ACK_REQUIRED", False),
            ack_timeout_s=_float("ACK_TIMEOUT_S", 2.0),
            mqtt_retry_count=_int("MQTT_RETRY_COUNT", 2),
            mqtt_retry_delay_s=_float("MQTT_RETRY_DELAY_S", 0.5),
            # Devices
            enabled_devices=_list("ENABLED_DEVICES", "Light1,Light2"),
            room_context_path=_str("ROOM_CONTEXT_PATH", "room_context.json"),
            # Camera
            camera_enabled=_bool("CAMERA_ENABLED", True),
            camera_index=_int("CAMERA_INDEX", 0),
            camera_warmup_frames=_int("CAMERA_WARMUP_FRAMES", 20),
            image_path=_str("IMAGE_PATH", "images/current_scene.jpg"),
            force_onboarding=_bool("FORCE_ONBOARDING", True),
            annotation_auto_accept=_bool("ANNOTATION_AUTO_ACCEPT", False),
            # TTS / Wake
            tts_enabled=_bool("TTS_ENABLED", True),
            wake_word_enabled=_bool("WAKE_WORD_ENABLED", False),
            # Server
            dashboard_host=_str("DASHBOARD_HOST", "0.0.0.0"),
            dashboard_port=_int("DASHBOARD_PORT", 8000),
            # Fallback
            llm_groq_fallback=_bool("LLM_GROQ_FALLBACK", False),
            ai_provider=_str("AI_PROVIDER", "groq").lower(),
        )


# Singleton — importable everywhere as `from config import settings`
settings = Settings.load()

