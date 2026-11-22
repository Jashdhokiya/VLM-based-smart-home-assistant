"""
INOT Mark1 — Runtime pipeline (new main entry point for Pi & PC)

Flow:
  [optional wake word + VAD]
  → local/cloud STT
  → rule-based fast path (simple commands)
  → local LLM (with room context) only when rules don't match
  → validate JSON
  → publish to MQTT
  → wait for state ack
  → log metrics

Usage:
  python pipeline.py                        # live mic loop
  python pipeline.py --wav path/to/cmd.wav  # single WAV file
  python pipeline.py --text "turn on light1" # direct text (skip STT)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
import metrics
import room_context
import fast_path
import safe_parse
import stt_backend
import llm_backend


# ── Globals ───────────────────────────────────────────────────────────
_room_ctx: Optional[Dict[str, Any]] = None
_mqtt_executor = None  # lazy import to avoid circular deps


def _get_executor():
    global _mqtt_executor
    if _mqtt_executor is None:
        import mqtt_executor  # type: ignore
        _mqtt_executor = mqtt_executor
    return _mqtt_executor


def _load_room_context() -> Dict[str, Any]:
    """Load room context, falling back to a minimal default."""
    global _room_ctx
    if _room_ctx is not None:
        return _room_ctx

    ctx_path = Path(settings.room_context_path)
    if ctx_path.exists():
        try:
            _room_ctx = room_context.load(ctx_path)
            devs = room_context.device_list(_room_ctx)
            print(f"[PIPELINE] Loaded room context: {len(devs)} device(s) from {ctx_path}")
            return _room_ctx
        except Exception as e:
            print(f"[PIPELINE] [WARN] Failed to load room context: {e}")

    # Fallback: build minimal context from ENABLED_DEVICES
    print("[PIPELINE] No room_context.json — using default device list from env")
    _room_ctx = {
        "devices": [
            {
                "id": name.lower(),
                "name": name,
                "type": "light",
                "x": round(0.2 + i * 0.3, 2),
                "y": 0.5,
                "zone": "room",
                "near": [],
            }
            for i, name in enumerate(settings.enabled_devices)
        ]
    }
    return _room_ctx


def _get_device_ids(ctx: Dict[str, Any]) -> List[str]:
    """Get list of device IDs from context."""
    return [d["id"] for d in room_context.device_list(ctx)]


def _execute_commands(commands: Dict[str, str], command_id: str) -> Dict[str, Any]:
    """
    Execute validated commands via MQTT.

    ``commands``: {device_id: action} where action is "on", "off", or "set:<value>"
    Returns execution results.
    """
    executor = _get_executor()
    executed = {}

    for device_id, action in commands.items():
        # Map action to executor format
        if action.lower() in ("on", "off"):
            cmd = action.capitalize()  # "On" or "Off"
        elif action.lower().startswith("set:"):
            # For now, treat set as "On" (brightness control is device-specific)
            cmd = "On"
        else:
            print(f"[PIPELINE] [WARN] Unknown action '{action}' for {device_id}, skipping")
            executed[device_id] = {"command": action, "success": False, "reason": "unknown_action"}
            continue

        with metrics.stage("mqtt_execute", command_id=command_id, note=f"{device_id}={cmd}"):
            # Try to find canonical device name in executor
            success = executor.execute(device_id, cmd)

        executed[device_id] = {"command": action, "success": success}

    return executed


def _parse_llm_commands(raw: str, device_ids: List[str]) -> Dict[str, str]:
    """
    Parse LLM output into validated {device_id: action} dict.

    Uses safe_parse for extraction, then validates device IDs.
    """
    try:
        parsed = safe_parse.parse_dict(raw)
    except ValueError:
        return {}

    result = {}
    id_map = {did.lower(): did for did in device_ids}

    for key, val in parsed.items():
        key_lower = str(key).strip().lower()
        val_str = str(val).strip().lower()

        if key_lower not in id_map:
            continue

        if val_str in ("on", "off") or val_str.startswith("set:"):
            result[id_map[key_lower]] = val_str

    return result


def process_command(
    text: str,
    command_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process a single natural-language command through the full pipeline.

    Returns a result dict with keys: command_id, query, path, commands,
    executed, duration_ms, backend, etc.
    """
    cid = command_id or uuid.uuid4().hex[:6]
    start = time.perf_counter()

    ctx = _load_room_context()
    device_ids = _get_device_ids(ctx)
    context_text = room_context.build_prompt_context(ctx)

    result: Dict[str, Any] = {
        "command_id": cid,
        "query": text,
        "path": "none",
        "commands": {},
        "executed": {},
        "raw_llm": "",
        "backend": "",
        "model": "",
        "fallback_used": False,
        "duration_ms": 0,
    }

    # ── 1. Fast path ──────────────────────────────────────────────
    with metrics.stage("fast_path", command_id=cid):
        fp_result = fast_path.match(text, ctx)

    if fp_result:
        result["path"] = "fast_path"
        result["commands"] = fp_result
        print(f"[PIPELINE] Fast path matched: {fp_result}")
    else:
        # ── 2. LLM path ──────────────────────────────────────────
        result["path"] = "llm"
        try:
            llm_result = llm_backend.query(
                user_command=text,
                room_context_text=context_text,
                device_ids=device_ids,
            )
            result["raw_llm"] = llm_result["raw"]
            result["backend"] = llm_result["backend"]
            result["model"] = llm_result["model"]
            result["fallback_used"] = llm_result["fallback_used"]

            with metrics.stage("parse", command_id=cid):
                result["commands"] = _parse_llm_commands(llm_result["raw"], device_ids)

            if not result["commands"]:
                print(f"[PIPELINE] LLM returned no valid commands from: {llm_result['raw'][:200]}")
        except Exception as e:
            print(f"[PIPELINE] [ERROR] LLM failed: {e}")
            result["error"] = str(e)

    # ── 3. Execute ────────────────────────────────────────────────
    if result["commands"]:
        result["executed"] = _execute_commands(result["commands"], cid)

    result["duration_ms"] = (time.perf_counter() - start) * 1000.0
    metrics.log_stage(
        "pipeline_total",
        result["duration_ms"],
        command_id=cid,
        note=f"path={result['path']},cmds={len(result['commands'])}",
    )

    return result


def run_single(wav_path: Optional[str] = None, text: Optional[str] = None) -> Dict[str, Any]:
    """
    Run a single command from WAV file or direct text.
    """
    if text:
        print(f"[PIPELINE] Direct text input: \"{text}\"")
        return process_command(text)

    if wav_path:
        transcript = stt_backend.transcribe_file(wav_path)
        if not transcript:
            print("[PIPELINE] No speech detected in WAV file.")
            return {"error": "no_speech", "query": ""}
        print(f"[PIPELINE] Transcribed: \"{transcript}\"")
        return process_command(transcript)

    raise ValueError("Either --wav or --text must be provided for single-shot mode")


def run_loop():
    """
    Run the interactive mic loop.
    """
    _load_room_context()
    print(f"[PIPELINE] Starting voice loop (STT={settings.stt_backend}, LLM={settings.llm_backend})")
    print("[PIPELINE] Speak a command, or say 'exit' / 'quit' to stop.\n")

    while True:
        try:
            wav_path = stt_backend.record_audio()
            transcript = stt_backend.transcribe_file(wav_path)
        except RuntimeError as e:
            print(f"[PIPELINE] [ERROR] {e}")
            print("[PIPELINE] No mic available. Use --wav <file> or --text <cmd> instead.")
            break

        if not transcript:
            continue

        norm = re.sub(r"[^\w\s]", "", transcript).strip().lower()
        if norm in ("exit", "quit", "stop"):
            print("[PIPELINE] Exit requested.")
            break

        print(f"[PIPELINE] You said: \"{transcript}\"")
        result = process_command(transcript)

        if result["commands"]:
            for dev, action in result["commands"].items():
                exec_info = result["executed"].get(dev, {})
                status = "✓" if exec_info.get("success") else "✗"
                print(f"  {status} {dev} → {action}")
        else:
            print("  (no actions)")

        print(f"  [{result['path']}] {result['duration_ms']:.0f}ms\n")

        # Brief pause
        time.sleep(0.5)


# ── CLI entry point ───────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="INOT Mark1 Pipeline")
    parser.add_argument("--wav", type=str, help="Path to WAV file for single-shot processing")
    parser.add_argument("--text", type=str, help="Direct text command (skip STT)")
    parser.add_argument("--loop", action="store_true", help="Run interactive mic loop")
    args = parser.parse_args()

    if args.wav or args.text:
        result = run_single(wav_path=args.wav, text=args.text)
        print(json.dumps(result, indent=2, default=str))
    elif args.loop:
        run_loop()
    else:
        # Default: try loop, fall back to prompting for text
        try:
            run_loop()
        except RuntimeError:
            print("\n[PIPELINE] No microphone. Entering text mode (type 'exit' to quit):\n")
            while True:
                try:
                    text = input("Command> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not text or text.lower() in ("exit", "quit"):
                    break
                result = process_command(text)
                for dev, action in result.get("commands", {}).items():
                    exec_info = result["executed"].get(dev, {})
                    status = "✓" if exec_info.get("success") else "✗"
                    print(f"  {status} {dev} → {action}")
                if not result.get("commands"):
                    print("  (no actions)")
                print(f"  [{result['path']}] {result['duration_ms']:.0f}ms\n")


if __name__ == "__main__":
    main()

