"""
INOT Mark1 — Deterministic fast-path intent matcher

Handles simple commands (on/off/set level) without hitting the LLM.
Uses device names, zones, and "near" relations from room_context.json.

Returns ``None`` when no fast-path match is found, signalling the
pipeline to fall through to the LLM.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from src.core import room_context


# ── Intent patterns ──────────────────────────────────────────────────
# We normalise the input to lowercase, strip punctuation, and match
# against these patterns.  Order matters: more specific patterns first.

_ON_WORDS = {"on", "enable", "activate", "start"}
_OFF_WORDS = {"off", "disable", "deactivate", "stop", "kill"}
_ALL_WORDS = {"all", "every", "everything"}

# "set <device> to <value>" / "dim <device> to 50"
_SET_RE = re.compile(
    r"(?:set|dim|adjust|change)\s+(.+?)\s+(?:to|at)\s+(\d+)",
    re.IGNORECASE,
)

# "turn on/off <device>" / "switch on/off <device>"
_TURN_RE = re.compile(
    r"(?:turn|switch|put)\s+(on|off)\s+(?:the\s+)?(.+)",
    re.IGNORECASE,
)

# "<device> on/off" (trailing action)
_TRAILING_RE = re.compile(
    r"(.+?)\s+(on|off)\s*$",
    re.IGNORECASE,
)

# "turn on/off all lights" or "all off"
_ALL_RE = re.compile(
    r"(?:turn\s+)?(on|off)\s+(?:all|every)\s*(.+)?",
    re.IGNORECASE,
)
_ALL_TRAILING_RE = re.compile(
    r"(?:all|every)\s*(.+)?\s+(on|off)",
    re.IGNORECASE,
)


def _normalise(text: str) -> str:
    """Lowercase, strip non-alphanumeric except spaces."""
    return re.sub(r"[^\w\s]", "", text).strip().lower()


def _resolve_target(
    target_phrase: str,
    ctx: Dict[str, Any],
) -> List[str]:
    """
    Resolve a natural-language target phrase to a list of device IDs.

    Tries, in order:
      1. Exact device ID match   ("light1")
      2. Exact device name match ("light 1", "ceiling fan")
      3. Device type match       ("lights" -> all lights)
      4. Zone match              ("desk" -> all devices in desk zone)
      5. Near match              ("near window" -> devices near window)
    """
    phrase = _normalise(target_phrase)
    devices = room_context.device_list(ctx)

    if not phrase or not devices:
        return []

    # 1. Exact ID
    for d in devices:
        if _normalise(d["id"]) == phrase:
            return [d["id"]]

    # 2. Exact name
    for d in devices:
        if _normalise(d["name"]) == phrase:
            return [d["id"]]

    # 3. Type match (singular or plural)
    type_phrase = phrase.rstrip("s")  # "lights" -> "light"
    matched_by_type = [d["id"] for d in devices if d["type"] == type_phrase]
    if matched_by_type:
        return matched_by_type

    # 4. Zone match
    matched_by_zone = [d["id"] for d in devices if _normalise(d["zone"]) == phrase]
    if matched_by_zone:
        return matched_by_zone

    # 5. "near X" pattern — also handles "the light near X", "device near X"
    near_match = re.search(r"(?:near|by|next\s+to|beside)\s+(.+)", phrase)
    if near_match:
        landmark = near_match.group(1).strip()
        matched_near = [d["id"] for d in room_context.devices_near(ctx, landmark)]
        if matched_near:
            return matched_near

    # 5b. Check if the phrase itself is a landmark in any device's near list
    matched_near = [d["id"] for d in room_context.devices_near(ctx, phrase)]
    if matched_near:
        return matched_near

    # 6. Substring match in name (fuzzy fallback)
    for d in devices:
        if phrase in _normalise(d["name"]):
            return [d["id"]]

    return []


def match(
    text: str,
    ctx: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """
    Try to deterministically match a command.

    Returns a dict ``{device_id: action}`` if matched, or ``None`` if the
    command is too complex / ambiguous for the fast path.

    Actions are: "on", "off", or "set:<int>".
    """
    if not text or not text.strip():
        return None

    norm = _normalise(text)
    devices = room_context.device_list(ctx)

    if not devices:
        return None

    all_ids = [d["id"] for d in devices]

    # ── "all on/off" ──────────────────────────────────────────────
    m = _ALL_RE.match(norm)
    if not m:
        m = _ALL_TRAILING_RE.match(norm)
        if m:
            # Swap groups: group1=type, group2=action
            type_filter = (m.group(1) or "").strip()
            action = m.group(2).strip().lower()
            m = None  # fall through to manual handling
            targets = all_ids
            if type_filter:
                type_resolved = _resolve_target(type_filter, ctx)
                if type_resolved:
                    targets = type_resolved
            return {did: action for did in targets}

    if m:
        action = m.group(1).strip().lower()
        type_filter = (m.group(2) or "").strip()
        targets = all_ids
        if type_filter:
            type_resolved = _resolve_target(type_filter, ctx)
            if type_resolved:
                targets = type_resolved
        return {did: action for did in targets}

    # ── "set <target> to <value>" ─────────────────────────────────
    m = _SET_RE.match(norm)
    if m:
        target_phrase = m.group(1).strip()
        value = m.group(2).strip()
        resolved = _resolve_target(target_phrase, ctx)
        if resolved:
            return {did: f"set:{value}" for did in resolved}

    # ── "turn on/off <target>" ────────────────────────────────────
    m = _TURN_RE.match(norm)
    if m:
        action = m.group(1).strip().lower()
        target_phrase = m.group(2).strip()
        resolved = _resolve_target(target_phrase, ctx)
        if resolved:
            return {did: action for did in resolved}

    # ── "<target> on/off" (trailing) ──────────────────────────────
    m = _TRAILING_RE.match(norm)
    if m:
        target_phrase = m.group(1).strip()
        action = m.group(2).strip().lower()
        resolved = _resolve_target(target_phrase, ctx)
        if resolved:
            return {did: action for did in resolved}

    # ── Single word on/off with implicit "all" ────────────────────
    # (not matched — too ambiguous for fast path)

    return None

