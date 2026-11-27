"""
INOT Mark1 — Pi-safe Cloud Onboarding Engine

Uploads a room image to a cloud Vision-Language Model (or external cloud
onboarding endpoint), receives structured JSON describing detected smart
devices and their spatial layout, normalizes and validates it against
``src.core.room_context``, and optionally applies it locally or pushes it
to a Raspberry Pi running ``src.web.server``.

Designed for Raspberry Pi (ARM64):
  - Zero heavy PC-only dependencies (no OpenCV, no pyttsx3, no PyTorch).
  - Uses lightweight ``httpx`` + standard library + optional Pillow resize.
  - Atomic file writes for SD-card safety.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

from src.core import metrics
from src.core import room_context
from src.core import safe_parse

DEFAULT_SCENE_IMAGE = BASE_DIR / "images" / "current_scene.jpg"
DEFAULT_DRAFT_FILE = BASE_DIR / "logs" / "onboarding_draft.json"

SUPPORTED_PROVIDERS = ("groq", "gemini", "openai", "cloud_url")

_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

_TYPE_ALIASES: Dict[str, str] = {
    "light": "light",
    "lights": "light",
    "lamp": "light",
    "desk_lamp": "light",
    "floor_lamp": "light",
    "bulb": "light",
    "led": "light",
    "light_strip": "light",
    "backlight": "light",
    "sconce": "light",
    "chandelier": "light",
    "spotlight": "light",
    "fan": "fan",
    "fans": "fan",
    "ceiling_fan": "fan",
    "ceiling fan": "fan",
    "table_fan": "fan",
    "exhaust": "fan",
    "exhaust_fan": "fan",
    "ac": "ac",
    "air_conditioner": "ac",
    "air conditioner": "ac",
    "aircon": "ac",
    "split_ac": "ac",
    "hvac": "ac",
    "thermostat": "ac",
    "tv": "tv",
    "television": "tv",
    "smart_tv": "tv",
    "smart tv": "tv",
    "monitor": "tv",
    "screen": "tv",
    "display": "tv",
    "curtain": "curtain",
    "curtains": "curtain",
    "blinds": "curtain",
    "shade": "curtain",
    "shades": "curtain",
    "drapes": "curtain",
    "window_curtain": "curtain",
    "lock": "lock",
    "locks": "lock",
    "smart_lock": "lock",
    "door_lock": "lock",
    "deadbolt": "lock",
}

_SENSITIVE_PLACEHOLDERS = {"", "...", "none", "null"}


# ---------------------------------------------------------------------------
# Environment / configuration helpers (read dynamically for testability)
# ---------------------------------------------------------------------------

def _env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _is_configured(val: str) -> bool:
    return bool(val) and val.lower() not in _SENSITIVE_PLACEHOLDERS and not val.startswith("YOUR_")


def _context_file_path() -> Path:
    p = Path(_env_str("ROOM_CONTEXT_PATH", "room_context.json") or "room_context.json")
    return p if p.is_absolute() else BASE_DIR / p


def _scene_image_path() -> Path:
    p = Path(_env_str("IMAGE_PATH", "images/current_scene.jpg") or "images/current_scene.jpg")
    return p if p.is_absolute() else BASE_DIR / p


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, target)
    except Exception:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass
        raise


def _atomic_write_text(target: Path, text: str) -> None:
    _atomic_write_bytes(target, text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Image inspection & lightweight preparation
# ---------------------------------------------------------------------------

def detect_image_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes; raises ValueError if invalid."""
    if not data:
        raise ValueError("Uploaded image is empty.")
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    raise ValueError("Unsupported image format. Upload a JPEG, PNG, WEBP, or GIF image.")


def prepare_image(
    data: bytes,
    max_mb: Optional[float] = None,
    max_dim: Optional[int] = None,
) -> Tuple[bytes, str]:
    """
    Validate image bytes and optionally downscale via Pillow if installed.
    Returns ``(processed_bytes, mime_type)``.
    """
    limit_mb = max_mb if max_mb is not None else _env_float("ONBOARDING_MAX_IMAGE_MB", 10.0)
    limit_bytes = int(limit_mb * 1024 * 1024)
    if len(data) > limit_bytes:
        raise ValueError(
            f"Image size ({len(data) / (1024 * 1024):.2f} MB) exceeds limit of {limit_mb:.1f} MB."
        )

    mime_type = detect_image_mime(data)
    target_dim = max_dim if max_dim is not None else _env_int("ONBOARDING_MAX_IMAGE_DIM", 1280)

    if target_dim > 0:
        try:
            from PIL import Image  # optional; skipped seamlessly on minimal Pi installs
            with Image.open(io.BytesIO(data)) as img:
                if max(img.size) > target_dim:
                    img = img.convert("RGB")
                    img.thumbnail((target_dim, target_dim))
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=88, optimize=True)
                    return buf.getvalue(), "image/jpeg"
        except ImportError:
            pass
        except Exception as exc:
            print(f"[ONBOARDING] Optional Pillow resize skipped: {exc}")

    return data, mime_type


# ---------------------------------------------------------------------------
# Prompting, parsing, and schema normalization
# ---------------------------------------------------------------------------

def build_vision_prompt(
    room_name: Optional[str] = None,
    device_hints: Optional[str] = None,
) -> str:
    """Build the structured Vision-Language prompt that produces room_context JSON."""
    valid_types = ", ".join(sorted(room_context.VALID_TYPES))
    hints_block = ""
    if room_name:
        hints_block += f"\n- Room name hint: {room_name.strip()}"
    if device_hints:
        hints_block += f"\n- User device hints: {device_hints.strip()}"

    return (
        "You are an IoT smart-home spatial onboarding engine.\n"
        "Analyze the room image and detect all controllable smart devices "
        "(lights/lamps, fans, air conditioners, TVs, curtains/blinds, smart locks) "
        "plus nearby furniture or architectural landmarks.\n"
        "Return ONLY a valid JSON object matching this exact schema (no markdown, no prose):\n"
        "{\n"
        '  "room_name": "Bedroom",\n'
        '  "devices": [\n'
        "    {\n"
        '      "id": "light1",\n'
        '      "name": "Desk Lamp",\n'
        '      "type": "light",\n'
        '      "x": 0.25,\n'
        '      "y": 0.40,\n'
        '      "zone": "desk",\n'
        '      "near": ["desk", "monitor"]\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        f'- "type" MUST be one of: [{valid_types}].\n'
        '- "id" MUST be a unique lowercase slug (e.g. light1, light2, fan1, ac1, tv1).\n'
        '- "x" and "y" MUST be normalized floats in [0.0, 1.0] where (0,0) is top-left and (1,1) is bottom-right.\n'
        '- Order same-type devices sequentially from left to right (e.g. light1 on the left, light2 to its right).\n'
        '- "zone" MUST be a short non-empty semantic zone string (e.g. "desk", "window", "ceiling", "bed", "corner").\n'
        '- "near" MUST be a list of 1 to 4 lowercase landmark strings visible near the device.'
        f"{hints_block}"
    )


def _slugify_id(raw_id: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", raw_id.strip().lower()).strip("_")
    return slug or fallback


def _normalize_coord(raw_val: Any, default: float = 0.5) -> float:
    try:
        val = float(raw_val)
    except (TypeError, ValueError):
        return default
    if 1.0 < val <= 100.0:
        val = val / 100.0
    return round(max(0.0, min(1.0, val)), 4)


def _normalize_type(raw_type: Any) -> str:
    key = str(raw_type or "").strip().lower().replace("-", "_")
    if key in room_context.VALID_TYPES:
        return key
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    return key or "light"


def normalize_context(
    raw_data: Any,
    default_room_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Normalize a cloud VLM / endpoint payload into canonical ``room_context`` shape.
    """
    if not isinstance(raw_data, dict):
        raise ValueError("Cloud response must be a JSON object.")

    payload = raw_data
    for wrapper_key in ("context", "room_context", "data"):
        inner = payload.get(wrapper_key)
        if isinstance(inner, dict) and "devices" in inner:
            payload = inner
            break

    room_name = str(
        payload.get("room_name")
        or default_room_name
        or "Room"
    ).strip() or "Room"

    raw_devices = payload.get("devices")
    if not isinstance(raw_devices, list):
        return {"room_name": room_name, "devices": []}

    normalized_devices: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    for idx, item in enumerate(raw_devices):
        if not isinstance(item, dict):
            continue

        dev_type = _normalize_type(item.get("type") or item.get("device_type") or item.get("category"))
        base_id = _slugify_id(
            str(item.get("id") or item.get("device_id") or ""),
            fallback=f"{dev_type}{idx + 1}",
        )
        dev_id = base_id
        suffix = 2
        while dev_id in seen_ids:
            dev_id = f"{base_id}_{suffix}"
            suffix += 1
        seen_ids.add(dev_id)

        name = str(item.get("name") or item.get("label") or "").strip()
        if not name:
            name = dev_id.replace("_", " ").title()

        zone = str(item.get("zone") or item.get("location") or "room").strip() or "room"

        raw_near = item.get("near", [])
        if isinstance(raw_near, str):
            near = [s.strip() for s in raw_near.split(",") if s.strip()]
        elif isinstance(raw_near, list):
            near = [str(n).strip() for n in raw_near if str(n).strip()]
        else:
            near = []

        normalized_devices.append({
            "id": dev_id,
            "name": name,
            "type": dev_type,
            "x": _normalize_coord(item.get("x"), 0.5),
            "y": _normalize_coord(item.get("y"), 0.5),
            "zone": zone,
            "near": near,
        })

    return {
        "room_name": room_name,
        "devices": normalized_devices,
    }


# ---------------------------------------------------------------------------
# Cloud provider backends
# ---------------------------------------------------------------------------

def _extract_openai_text(resp_json: Dict[str, Any]) -> str:
    choices = resp_json.get("choices") or []
    if not choices:
        raise RuntimeError(f"Vision API returned no choices: {resp_json}")
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        content = "\n".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Vision API returned empty content.")
    return content.strip()


def _query_openai_compatible_vision(
    *,
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_b64: str,
    mime_type: str,
    timeout_s: float,
    provider_name: str,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    base_payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                    },
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }

    resp = httpx.post(url, json=base_payload, headers=headers, timeout=timeout_s)
    if resp.status_code == 400 and "response_format" in resp.text.lower():
        fallback_payload = dict(base_payload)
        fallback_payload.pop("response_format", None)
        resp = httpx.post(url, json=fallback_payload, headers=headers, timeout=timeout_s)

    if resp.status_code >= 400:
        raise RuntimeError(
            f"{provider_name} vision request failed ({resp.status_code}): {resp.text[:300]}"
        )
    return _extract_openai_text(resp.json())


def _query_gemini_vision(
    *,
    api_key: str,
    model: str,
    prompt: str,
    image_b64: str,
    mime_type: str,
    timeout_s: float,
) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }

    resp = httpx.post(url, json=payload, headers=headers, timeout=timeout_s)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Gemini vision request failed ({resp.status_code}): {resp.text[:300]}"
        )

    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"Gemini returned no candidates: {data}")
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise RuntimeError("Gemini returned empty response text.")
    return text


def _query_custom_cloud_url(
    *,
    url: str,
    api_key: str,
    image_bytes: bytes,
    mime_type: str,
    room_name: Optional[str],
    device_hints: Optional[str],
    timeout_s: float,
) -> Dict[str, Any]:
    ext = _MIME_TO_EXT.get(mime_type, ".jpg")
    headers: Dict[str, str] = {}
    if _is_configured(api_key):
        headers["Authorization"] = f"Bearer {api_key}"

    form_data: Dict[str, str] = {}
    if room_name:
        form_data["room_name"] = room_name
    if device_hints:
        form_data["device_hints"] = device_hints

    files = {"image": (f"scene{ext}", image_bytes, mime_type)}
    resp = httpx.post(url, data=form_data, files=files, headers=headers, timeout=timeout_s)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Cloud onboarding endpoint failed ({resp.status_code}): {resp.text[:300]}"
        )
    return resp.json()


# ---------------------------------------------------------------------------
# Public Engine API
# ---------------------------------------------------------------------------

def generate_context(
    image_bytes: bytes,
    *,
    room_name: Optional[str] = None,
    device_hints: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    timeout_s: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Upload ``image_bytes`` to the configured cloud provider and return
    a validated ``room_context`` JSON payload + metadata.
    """
    start = time.perf_counter()
    active_provider = (provider or _env_str("ONBOARDING_PROVIDER", "groq")).lower()
    if active_provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"Unsupported onboarding provider '{active_provider}'. "
            f"Choose one of: {', '.join(SUPPORTED_PROVIDERS)}"
        )

    processed_bytes, mime_type = prepare_image(image_bytes)
    image_b64 = base64.b64encode(processed_bytes).decode("ascii")
    prompt = build_vision_prompt(room_name=room_name, device_hints=device_hints)
    timeout = timeout_s if timeout_s is not None else _env_float("ONBOARDING_TIMEOUT_S", 45.0)

    raw_text = ""
    used_model = ""

    with metrics.stage("onboarding_cloud", note=f"provider={active_provider}"):
        if active_provider == "groq":
            api_key = _env_str("GROQ_API_KEY")
            if not _is_configured(api_key):
                raise ValueError("GROQ_API_KEY is not configured.")
            used_model = model or _env_str(
                "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
            )
            raw_text = _query_openai_compatible_vision(
                url="https://api.groq.com/openai/v1/chat/completions",
                api_key=api_key,
                model=used_model,
                prompt=prompt,
                image_b64=image_b64,
                mime_type=mime_type,
                timeout_s=timeout,
                provider_name="Groq",
            )
        elif active_provider == "gemini":
            api_key = _env_str("GEMINI_API_KEY")
            if not _is_configured(api_key):
                raise ValueError("GEMINI_API_KEY is not configured.")
            used_model = model or _env_str("GEMINI_VISION_MODEL", "gemini-2.5-flash")
            raw_text = _query_gemini_vision(
                api_key=api_key,
                model=used_model,
                prompt=prompt,
                image_b64=image_b64,
                mime_type=mime_type,
                timeout_s=timeout,
            )
        elif active_provider == "openai":
            api_key = _env_str("OPENAI_API_KEY")
            if not _is_configured(api_key):
                raise ValueError("OPENAI_API_KEY is not configured.")
            base_url = _env_str("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
            used_model = model or _env_str("OPENAI_VISION_MODEL", "gpt-4o-mini")
            raw_text = _query_openai_compatible_vision(
                url=f"{base_url}/chat/completions",
                api_key=api_key,
                model=used_model,
                prompt=prompt,
                image_b64=image_b64,
                mime_type=mime_type,
                timeout_s=timeout,
                provider_name="OpenAI",
            )
        elif active_provider == "cloud_url":
            cloud_url = _env_str("CLOUD_ONBOARDING_URL")
            if not _is_configured(cloud_url):
                raise ValueError("CLOUD_ONBOARDING_URL is not configured.")
            used_model = model or cloud_url
            cloud_json = _query_custom_cloud_url(
                url=cloud_url,
                api_key=_env_str("CLOUD_ONBOARDING_API_KEY"),
                image_bytes=processed_bytes,
                mime_type=mime_type,
                room_name=room_name,
                device_hints=device_hints,
                timeout_s=timeout,
            )
            raw_text = json.dumps(cloud_json)

    parsed_dict = safe_parse.parse_dict(raw_text)
    normalized = normalize_context(parsed_dict, default_room_name=room_name)
    valid, errors = room_context.validate(normalized)
    duration_ms = round((time.perf_counter() - start) * 1000.0, 2)

    return {
        "provider": active_provider,
        "model": used_model,
        "mime_type": mime_type,
        "image_bytes": len(processed_bytes),
        "duration_ms": duration_ms,
        "validation": {"valid": valid, "errors": errors},
        "context": normalized,
        "raw": raw_text,
    }


def save_scene_image(image_bytes: bytes, dest_path: Optional[Path] = None) -> Path:
    """Persist the uploaded scene image atomically and return its Path."""
    target = dest_path or _scene_image_path()
    _atomic_write_bytes(target, image_bytes)
    return target


def save_context(
    ctx: Dict[str, Any],
    path: Optional[Path] = None,
    normalize: bool = False,
) -> Path:
    """Validate and atomically write ``room_context.json``."""
    data = normalize_context(ctx) if normalize else ctx
    ok, errors = room_context.validate(data)
    if not ok:
        raise ValueError("Invalid room_context: " + "; ".join(errors))
    target = path or _context_file_path()
    _atomic_write_text(target, json.dumps(data, indent=2) + "\n")
    return target


def save_draft(draft_payload: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Persist latest generated onboarding draft to disk."""
    target = path or DEFAULT_DRAFT_FILE
    _atomic_write_text(target, json.dumps(draft_payload, indent=2) + "\n")
    return target


def load_draft(path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Load latest generated onboarding draft from disk, if present."""
    target = path or DEFAULT_DRAFT_FILE
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None


def push_context_to_rpi(
    ctx: Dict[str, Any],
    rpi_url: Optional[str] = None,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    """
    Push a validated ``room_context`` JSON object to a Raspberry Pi's
    ``POST /context`` endpoint.
    """
    ok, errors = room_context.validate(ctx)
    if not ok:
        raise ValueError("Cannot push invalid room_context: " + "; ".join(errors))

    target_base = (rpi_url or _env_str("RPI_SERVER_URL")).rstrip("/")
    if not target_base:
        raise ValueError("RPI_SERVER_URL is not configured and no rpi_url was provided.")

    endpoint = target_base if target_base.endswith("/context") else f"{target_base}/context"
    resp = httpx.post(endpoint, json=ctx, timeout=timeout_s)
    if resp.status_code >= 400:
        raise RuntimeError(
            f"RPi POST /context failed ({resp.status_code}): {resp.text[:300]}"
        )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text}
    return {
        "target_url": endpoint,
        "status_code": resp.status_code,
        "response": body,
    }


def get_status() -> Dict[str, Any]:
    """Return onboarding readiness, provider configuration, and local artifact status."""
    provider = _env_str("ONBOARDING_PROVIDER", "groq").lower()
    provider_ready = {
        "groq": _is_configured(_env_str("GROQ_API_KEY")),
        "gemini": _is_configured(_env_str("GEMINI_API_KEY")),
        "openai": _is_configured(_env_str("OPENAI_API_KEY")),
        "cloud_url": _is_configured(_env_str("CLOUD_ONBOARDING_URL")),
    }
    models = {
        "groq": _env_str("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        "gemini": _env_str("GEMINI_VISION_MODEL", "gemini-2.5-flash"),
        "openai": _env_str("OPENAI_VISION_MODEL", "gpt-4o-mini"),
        "cloud_url": _env_str("CLOUD_ONBOARDING_URL") or "unconfigured",
    }

    ctx_path = _context_file_path()
    device_count = 0
    ctx_loaded = False
    if ctx_path.exists():
        try:
            ctx = room_context.load(ctx_path)
            ctx_loaded = True
            device_count = len(room_context.device_list(ctx))
        except Exception:
            pass

    return {
        "ready": provider_ready.get(provider, False),
        "provider": provider,
        "model": models.get(provider, ""),
        "providers_configured": provider_ready,
        "platform": _env_str("PLATFORM", "pc").lower(),
        "rpi_server_url": _env_str("RPI_SERVER_URL") or None,
        "room_context_loaded": ctx_loaded,
        "device_count": device_count,
        "has_draft": DEFAULT_DRAFT_FILE.exists(),
        "has_scene_image": _scene_image_path().exists(),
        "valid_device_types": sorted(room_context.VALID_TYPES),
        "max_image_mb": _env_float("ONBOARDING_MAX_IMAGE_MB", 10.0),
    }


# ---------------------------------------------------------------------------
# Headless CLI (works on RPi & PC without GUI)
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="INOT Mark1 Pi-safe Cloud Onboarding Engine"
    )
    parser.add_argument("image", help="Path to room image (JPEG/PNG/WEBP/GIF)")
    parser.add_argument("--room", default=None, help="Optional room name hint")
    parser.add_argument("--hints", default=None, help="Optional device hints (e.g. '2 lights, 1 fan')")
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS, default=None)
    parser.add_argument("--model", default=None, help="Override vision model")
    parser.add_argument("--save", action="store_true", help="Save validated context to ROOM_CONTEXT_PATH")
    parser.add_argument("--push", default=None, help="Push generated JSON to RPi URL (e.g. http://mark1pi.local:8000)")
    args = parser.parse_args()

    img_path = Path(args.image)
    if not img_path.exists():
        print(f"[ONBOARDING] Error: image not found: {img_path}", file=sys.stderr)
        sys.exit(1)

    res = generate_context(
        img_path.read_bytes(),
        room_name=args.room,
        device_hints=args.hints,
        provider=args.provider,
        model=args.model,
    )

    if args.save:
        saved = save_context(res["context"])
        res["saved_to"] = str(saved)

    if args.push:
        res["rpi_push"] = push_context_to_rpi(res["context"], rpi_url=args.push)

    output = {k: v for k, v in res.items() if k != "raw"}
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
