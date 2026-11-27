"""
INOT Mark1 — Room context manager

Loads, validates, and queries the ``room_context.json`` file that the
onboarding pipeline produces.  The Pi only reads this file; onboarding
(photo → YOLO → cloud VLM) happens off-device.

Schema for each device in ``devices`` list::

    {
      "id":   "light1",          # unique slug
      "name": "Light 1",         # human label
      "type": "light",           # light|fan|ac|tv|curtain|lock
      "x":    0.25,              # normalised [0,1] horizontal position
      "y":    0.40,              # normalised [0,1] vertical position
      "zone": "desk",            # semantic zone name
      "near": ["desk", "window"] # nearby landmarks
    }
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Valid device types
VALID_TYPES = {"light", "fan", "ac", "tv", "curtain", "lock"}

# Required keys per device
_REQUIRED_KEYS = {"id", "name", "type", "x", "y", "zone"}


def _validate_device(dev: dict, idx: int) -> List[str]:
    """Return list of error strings for a single device entry."""
    errors: List[str] = []
    prefix = f"devices[{idx}]"

    missing = _REQUIRED_KEYS - set(dev.keys())
    if missing:
        errors.append(f"{prefix}: missing keys {missing}")
        return errors  # can't validate further

    if not isinstance(dev["id"], str) or not dev["id"].strip():
        errors.append(f"{prefix}: 'id' must be a non-empty string")
    if not isinstance(dev["name"], str) or not dev["name"].strip():
        errors.append(f"{prefix}: 'name' must be a non-empty string")
    if dev["type"] not in VALID_TYPES:
        errors.append(f"{prefix}: 'type' must be one of {VALID_TYPES}, got '{dev['type']}'")

    for coord in ("x", "y"):
        v = dev[coord]
        if not isinstance(v, (int, float)) or not (0.0 <= v <= 1.0):
            errors.append(f"{prefix}: '{coord}' must be a number in [0, 1], got {v}")

    if not isinstance(dev["zone"], str) or not dev["zone"].strip():
        errors.append(f"{prefix}: 'zone' must be a non-empty string")

    near = dev.get("near", [])
    if not isinstance(near, list):
        errors.append(f"{prefix}: 'near' must be a list")
    elif not all(isinstance(n, str) for n in near):
        errors.append(f"{prefix}: every element in 'near' must be a string")

    return errors


def validate(data: Any) -> Tuple[bool, List[str]]:
    """
    Validate the full room_context structure.

    Returns (ok, errors).
    """
    errors: List[str] = []

    if not isinstance(data, dict):
        return False, ["root must be a JSON object"]

    if "devices" not in data:
        return False, ["missing top-level 'devices' key"]

    devices = data["devices"]
    if not isinstance(devices, list) or len(devices) == 0:
        return False, ["'devices' must be a non-empty list"]

    ids_seen: set = set()
    for idx, dev in enumerate(devices):
        if not isinstance(dev, dict):
            errors.append(f"devices[{idx}]: must be a JSON object, got {type(dev).__name__}")
            continue
        errors.extend(_validate_device(dev, idx))
        did = dev.get("id", "")
        if did in ids_seen:
            errors.append(f"devices[{idx}]: duplicate id '{did}'")
        ids_seen.add(did)

    return len(errors) == 0, errors


def load(path: str | Path) -> Dict[str, Any]:
    """
    Load and validate room_context.json.  Returns the parsed dict.
    Raises ``ValueError`` on schema errors, ``FileNotFoundError`` if missing.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Room context file not found: {p}")

    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)

    ok, errors = validate(data)
    if not ok:
        raise ValueError(
            f"Invalid room_context.json ({len(errors)} error(s)):\n"
            + "\n".join(f"  • {e}" for e in errors)
        )
    return data


def device_list(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the devices list from a loaded context."""
    return ctx.get("devices", [])


def device_by_id(ctx: Dict[str, Any], device_id: str) -> Optional[Dict[str, Any]]:
    """Look up a device by its ID (case-insensitive)."""
    lower = device_id.lower()
    for d in device_list(ctx):
        if d["id"].lower() == lower:
            return d
    return None


def devices_in_zone(ctx: Dict[str, Any], zone: str) -> List[Dict[str, Any]]:
    """Return all devices in a given zone (case-insensitive)."""
    lower = zone.lower()
    return [d for d in device_list(ctx) if d["zone"].lower() == lower]


def devices_near(ctx: Dict[str, Any], landmark: str) -> List[Dict[str, Any]]:
    """Return devices whose 'near' list contains *landmark* (case-insensitive)."""
    lower = landmark.lower()
    return [
        d for d in device_list(ctx)
        if lower in [n.lower() for n in d.get("near", [])]
    ]


def device_names_map(ctx: Dict[str, Any]) -> Dict[str, str]:
    """Return {canonical_name: device_id} from context (e.g. 'Light 1' -> 'light1')."""
    return {d["name"]: d["id"] for d in device_list(ctx)}


def build_prompt_context(ctx: Dict[str, Any]) -> str:
    """
    Build a compact text representation of the room context for LLM prompts.
    """
    lines = ["Room devices:"]
    for d in device_list(ctx):
        near_str = ", ".join(d.get("near", [])) or "none"
        lines.append(
            f"  - {d['id']} ({d['type']}, \"{d['name']}\"): "
            f"zone={d['zone']}, position=({d['x']:.2f},{d['y']:.2f}), near=[{near_str}]"
        )
    return "\n".join(lines)


# ── CLI validator ─────────────────────────────────────────────────────
def _cli_main():
    """``python room_context.py <file>`` — validate and pretty-print."""
    if len(sys.argv) < 2:
        print("Usage: python room_context.py <room_context.json>")
        sys.exit(1)

    path = sys.argv[1]
    try:
        ctx = load(path)
        print(f"✓ Valid room_context.json — {len(device_list(ctx))} device(s)")
        for d in device_list(ctx):
            print(f"  [{d['id']}] {d['name']} ({d['type']}) zone={d['zone']} "
                  f"pos=({d['x']:.2f},{d['y']:.2f}) near={d.get('near', [])}")
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli_main()

