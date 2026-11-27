#!/usr/bin/env python3
"""
INOT Mark1 — Raspberry Pi Preflight Checks

Verifies everything needed to run on the Pi:
  - Mic device (skip gracefully if none)
  - Mosquitto TCP (1883) and WebSocket (9001)
  - Ollama up and model pulled
  - Free RAM / swap
  - CPU throttling (vcgencmd)
  - Disk space
  - room_context.json validity
"""

import os
import sys
import json
import socket
import shutil
import subprocess
from pathlib import Path

# Add parent to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

RESULTS = {}


def _pass(name, msg):
    RESULTS[name] = True
    print(f"  [PASS] {msg}")


def _fail(name, msg, hint=""):
    RESULTS[name] = False
    print(f"  [FAIL] {msg}")
    if hint:
        print(f"         Fix: {hint}")


def _skip(name, msg):
    RESULTS[name] = True  # Skip counts as pass
    print(f"  [SKIP] {msg}")


def check_mic():
    print("\n── 1. Audio / Microphone ──")
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        input_devs = [d for d in devices if d['max_input_channels'] > 0]
        if input_devs:
            _pass("mic", f"Found {len(input_devs)} input device(s): {input_devs[0]['name']}")
        else:
            _skip("mic", "No input devices found. Use --wav mode until USB mic is connected.")
    except ImportError:
        _skip("mic", "sounddevice not installed. Mic features disabled.")
    except Exception as e:
        _skip("mic", f"Audio check error: {e}. Use --wav mode.")


def check_mosquitto():
    print("\n── 2. Mosquitto MQTT Broker ──")
    host = os.getenv("MQTT_BROKER_HOST", "localhost")

    # TCP 1883
    tcp_port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((host, tcp_port))
        s.close()
        _pass("mqtt_tcp", f"Mosquitto TCP reachable at {host}:{tcp_port}")
    except Exception as e:
        _fail("mqtt_tcp", f"Cannot reach Mosquitto TCP at {host}:{tcp_port}",
              "Run: docker compose up -d")

    # WebSocket 9001
    ws_port = int(os.getenv("MQTT_WS_PORT", "9001"))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((host, ws_port))
        s.close()
        _pass("mqtt_ws", f"Mosquitto WebSocket reachable at {host}:{ws_port}")
    except Exception as e:
        _fail("mqtt_ws", f"Cannot reach Mosquitto WS at {host}:{ws_port}",
              "Check mosquitto.conf has 'listener 9001' with 'protocol websockets'")


def check_ollama():
    print("\n── 3. Ollama LLM Server ──")
    llm_backend = os.getenv("LLM_BACKEND", "groq").lower()
    if llm_backend != "ollama":
        _skip("ollama", f"LLM_BACKEND={llm_backend}, not ollama. Skipping.")
        return

    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    # Strip /v1 to get base
    ollama_host = base_url.replace("/v1", "").rstrip("/")

    try:
        import httpx
        resp = httpx.get(f"{ollama_host}/api/tags", timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            models = [m["name"] for m in data.get("models", [])]
            expected = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b")
            if any(expected in m for m in models):
                _pass("ollama", f"Ollama running, model '{expected}' available")
            else:
                _fail("ollama", f"Ollama running but model '{expected}' not found. Models: {models}",
                      f"Run: docker compose exec ollama ollama pull {expected}")
        else:
            _fail("ollama", f"Ollama returned status {resp.status_code}",
                  "Run: docker compose up -d")
    except ImportError:
        # Try urllib as fallback
        import urllib.request
        try:
            req = urllib.request.urlopen(f"{ollama_host}/api/tags", timeout=5)
            _pass("ollama", "Ollama is reachable (install httpx for full check)")
        except Exception as e:
            _fail("ollama", f"Cannot reach Ollama at {ollama_host}: {e}",
                  "Run: docker compose up -d")
    except Exception as e:
        _fail("ollama", f"Cannot reach Ollama: {e}",
              "Run: docker compose up -d")


def check_ram():
    print("\n── 4. RAM & Swap ──")
    try:
        import psutil
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        total_gb = mem.total / (1024**3)
        avail_gb = mem.available / (1024**3)
        swap_gb = swap.total / (1024**3)
        status = f"RAM: {avail_gb:.1f}/{total_gb:.1f} GB free, Swap: {swap_gb:.1f} GB"

        if avail_gb < 0.3:
            _fail("ram", status,
                  "Free some RAM. Consider: OLLAMA_MODEL=qwen2.5:0.5b or use Groq cloud")
        elif avail_gb < 0.5:
            print(f"  [WARN] {status} — Low RAM, consider smaller model")
            RESULTS["ram"] = True
        else:
            _pass("ram", status)
    except ImportError:
        _skip("ram", "psutil not installed, cannot check RAM")


def check_throttle():
    print("\n── 5. CPU Throttle (vcgencmd) ──")
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=5
        )
        output = result.stdout.strip()
        if "0x0" in output:
            _pass("throttle", f"No throttling detected: {output}")
        else:
            _fail("throttle", f"Throttling detected: {output}",
                  "Improve cooling or use a heatsink/fan. Under-voltage? Check PSU.")
    except FileNotFoundError:
        _skip("throttle", "vcgencmd not found (not on a Pi?)")
    except Exception as e:
        _skip("throttle", f"Could not check throttling: {e}")


def check_disk():
    print("\n── 6. Disk Space ──")
    usage = shutil.disk_usage("/")
    free_gb = usage.free / (1024**3)
    total_gb = usage.total / (1024**3)
    if free_gb < 1.0:
        _fail("disk", f"Only {free_gb:.1f}/{total_gb:.1f} GB free",
              "Free up disk space or expand the SD card partition")
    else:
        _pass("disk", f"{free_gb:.1f}/{total_gb:.1f} GB free")


def check_room_context():
    print("\n── 7. Room Context ──")
    ctx_path = os.getenv("ROOM_CONTEXT_PATH", "room_context.json")
    full_path = ROOT / ctx_path

    if not full_path.exists():
        _fail("room_context", f"File not found: {full_path}",
              f"Copy from your PC: scp room_context.json pi@<IP>:~/Mark1/")
        return

    try:
        import src.core.room_context as rc
        ctx = rc.load(full_path)
        devs = rc.device_list(ctx)
        _pass("room_context", f"Valid — {len(devs)} device(s): {[d['id'] for d in devs]}")
    except Exception as e:
        _fail("room_context", f"Invalid: {e}",
              "Run: python room_context.py room_context.json")


def main():
    print("══════════════════════════════════════════════")
    print("  INOT Mark1 — Pi Preflight Diagnostics")
    print("══════════════════════════════════════════════")

    check_mic()
    check_mosquitto()
    check_ollama()
    check_ram()
    check_throttle()
    check_disk()
    check_room_context()

    print("\n══════════════════════════════════════════════")
    print("  Summary")
    print("══════════════════════════════════════════════")
    all_pass = True
    for name, passed in RESULTS.items():
        icon = "✓" if passed else "✗"
        print(f"  {icon} {name}")
        if not passed:
            all_pass = False

    print("══════════════════════════════════════════════")
    if all_pass:
        print("  All checks passed! Ready to run on the Pi.")
    else:
        print("  Some checks failed. See hints above.")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()

