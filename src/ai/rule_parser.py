"""
rule_parser.py - deterministic, offline command parser for INOT Mark1.

parse(text, room_ctx) -> {"device_id": "on" | "off" | "set:N"}  or  None
Returns None when it is not confident, so the caller should fall back to the LLM.

Conventions: curtain on = open, off = close.
"""
import re
import json
import difflib
from pathlib import Path

# ── Per-device phrases (edit for your room). Longest phrase wins. ──────────
ALIASES = {
    "fan1": ["ceiling fan", "ceiling fans", "fan1", "fans", "fan"],
    "light_desk": ["desk lamp", "desk light", "light_desk", "reading lamp", "reading light",
                   "study lamp", "study light", "study table", "study area", "lamp", "lamps"],
    "light_tv": ["tv backlight", "tv back light", "backlight", "back light", "tv light", "tv lights",
                 "tv led", "tv leds", "light_tv", "ambient light", "ambient lights", "ambient led",
                 "ambient leds", "ambient lighting", "bias light", "bias lighting"],
    "tv1": ["smart tv", "television", "telly", "tv1", "tv", "screen"],
    "curtain1": ["balcony curtain", "balcony curtains", "window curtain", "window curtains",
                 "curtain1", "curtains", "curtain", "drapes", "drape", "blinds", "blind", "shades"],
}
LM_SYNONYMS = {"television": "tv", "telly": "tv"}
TYPE_WORDS = {"light": "light", "lights": "light", "lighting": "light"}
GENERIC = {"device", "devices", "appliance", "appliances", "thing", "things", "whatever",
           "anything", "everything", "stuff"}
QUANT = {"all", "every", "each", "both", "everything"}
FILLERS = {"the", "a", "an", "my", "just", "only", "one", "ones", "also", "please", "too", "again",
           "back", "instead", "it", "its", "of", "and", "also", "that", "those", "them", "area",
           "zone", "unit", "side", "part"}
ACTION_VERBS = {"turn", "switch", "close", "open", "start", "set", "shut", "dim", "power", "kill",
                "stop", "fire", "flip", "light", "activate", "enable", "disable", "deactivate",
                "cut", "draw", "put", "brighten", "lower", "raise", "adjust", "change", "make"}
STEMS = {"turning": "turn", "turns": "turn", "switching": "switch", "powering": "power",
         "starting": "start", "closing": "close", "opening": "open", "stopping": "stop",
         "shutting": "shut", "killing": "kill", "dimming": "dim", "activating": "activate",
         "enabling": "enable", "disabling": "disable", "cutting": "cut"}
MARKER = re.compile(r"^(?:no wait|wait|never ?mind|actually|scratch that|on second thought|"
                    r"i changed my mind|no)\b[ ]*(.*)$")
NEG = re.compile(r"^(?:please )?(?:dont|do not|never|not)\b[ ]*(.*)$")
EXC_SPLIT = re.compile(r"\b(?:except(?: for)?|other than|apart from|besides|but)\b")
SET_WORDS = {"dim", "brighten", "lower", "raise", "adjust", "change", "set", "increase", "decrease"}


class RoomModel:
    def __init__(self, ctx):
        self.devices = {d["id"]: d for d in ctx.get("devices", [])}
        self.ids = list(self.devices)
        self.landmarks = set()
        self.dev_lm = {}
        for did, d in self.devices.items():
            lms = set(d.get("near", [])) | {d.get("zone", "")}
            lms.discard("")
            self.dev_lm[did] = lms
            self.landmarks |= lms
        self.aliases = []
        for did, phrases in ALIASES.items():
            if did in self.devices:
                for p in phrases:
                    self.aliases.append((p, did))
        self.aliases.sort(key=lambda x: -len(x[0]))
        self.vocab = set()
        for p, _ in self.aliases:
            self.vocab |= set(p.split())
        self.vocab |= self.landmarks | {"switch", "turn", "start", "activate", "enable", "disable",
                                         "deactivate", "close", "open", "brightness", "everything",
                                         "television", "curtain", "curtains"}


def _lm_norm(w):
    return LM_SYNONYMS.get(w, w)


def _normalize(text, room):
    t = text.lower().replace("’", "'")
    t = t.replace("'", "")
    t = re.sub(r"[^a-z0-9_,;:\s%]", " ", t)
    t = re.sub(r"\b(turn|switch)\s+of\b", r"\1 off", t)
    t = re.sub(r"\blights?\s+out\b", "lights off", t)
    t = re.sub(r"\b(?:light|fire|power)\s+up\b", "start", t)
    t = re.sub(r"\bopen\s+up\b", "open", t)
    toks = []
    for w in re.findall(r"[a-z0-9_]+|[,;:]", t):
        w = STEMS.get(w, w)
        if (w.isalpha() and len(w) >= 5 and w not in room.vocab and w not in ACTION_VERBS
                and w not in STEMS.values()):
            cand = [v for v in room.vocab if v[0] == w[0] and len(v) >= 4]
            m = difflib.get_close_matches(w, cand, n=1, cutoff=0.84)
            if m:
                w = m[0]
        toks.append(w)
    return " ".join(toks)


class Seg:
    pass


def _resolve(text, room, prev_types=None, allow_landmark_only=False):
    """Resolve a text chunk to devices. Returns (devices:set, explicit:bool, ambiguous:bool, rest:str)."""
    s = Seg()
    t = " " + text + " "
    lm_found = []
    prep = re.compile(r"\b(?:near|nearest|next to|close to|closest to|beside|around|by|behind|above|"
                      r"below|under|at|in|over|inside)\s+(?:the\s+|my\s+|a\s+)?("
                      + "|".join(sorted(list(room.landmarks) + list(LM_SYNONYMS), key=len, reverse=True))
                      + r")\b")

    def _take_lm(m):
        lm_found.append(_lm_norm(m.group(1)))
        return " "
    t = prep.sub(_take_lm, t)
    specific = []
    for phrase, did in room.aliases:
        pat = r"(?<![a-z0-9_])" + re.escape(phrase) + r"(?![a-z0-9_])"
        if re.search(pat, t):
            t = re.sub(pat, " § ", t)
            specific.append(did)
    words = t.split()
    type_w, quant, generic = set(), False, False
    for w in words:
        if w in TYPE_WORDS:
            type_w.add(TYPE_WORDS[w])
        if w in QUANT:
            quant = True
        if w in GENERIC:
            generic = True
        if w in room.landmarks or w in LM_SYNONYMS:
            lm_found.append(_lm_norm(w))
    plural_type = any(w in ("lights", "lighting") for w in words)
    singular_type = "light" in words
    rest = [w for w in words if w not in FILLERS and w != "§" and w not in room.landmarks
            and w not in LM_SYNONYMS]
    only_lm = bool(lm_found) and not specific and all(
        w in ("just", "only", "the", "one", "ones") or w in FILLERS for w in rest)

    if specific:
        return set(specific), True, False, words
    cands = set(room.ids)
    if type_w:
        cands = {d for d in cands if room.devices[d]["type"] in type_w}
    if lm_found:
        cands = {d for d in cands if any(l in room.dev_lm[d] for l in lm_found)}
        if only_lm and prev_types and not type_w:
            cands = {d for d in cands if room.devices[d]["type"] in prev_types} or cands
        if type_w or generic or quant or only_lm or allow_landmark_only:
            return cands, True, False, words
        return set(), False, False, words
    if type_w and (quant or plural_type):
        return cands, True, False, words
    if type_w and singular_type:
        return (cands, True, False, words) if len(cands) == 1 else (set(), False, True, words)
    if quant or generic:
        return set(room.ids), True, False, words
    return set(), False, False, words


def _find_action(words, has_dev):
    """Earliest action cue in the token list -> ('on'|'off'|None)."""
    n = len(words)
    on_v = {"start", "activate", "enable", "open", "illuminate"}
    off_v = {"kill", "stop", "deactivate", "disable", "cut", "close", "shut", "draw"}
    for i, w in enumerate(words):
        if w in {"turn", "switch", "flip", "power", "shut", "put", "keep", "leave", "set"}:
            for j in range(i + 1, min(n, i + 5)):
                x = words[j]
                if x in ("on", "up") and w != "set":
                    return "on" if x == "on" or w in ("keep", "leave") else "up"
                if x in ("off", "down", "out"):
                    return "off"
                if x in on_v | off_v:
                    break
        if w in on_v:
            return "on"
        if w in off_v:
            return "off"
        if w in ("on", "off"):
            prev = words[i - 1] if i else ""
            last = i == n - 1
            if last or prev in ("§", "it", "them", "all", "everything", "else", "lights", "both"):
                return w
    return None


def _level(words):
    for i, w in enumerate(words):
        if w.isdigit() and 0 <= int(w) <= 100:
            return int(w)
    if any(w in ("maximum", "max", "full") for w in words):
        return 100
    if any(w in ("halfway", "half") for w in words):
        return 50
    return None


def _split_exceptions(t):
    parts = []
    last = 0
    exc = []
    main = []
    for m in EXC_SPLIT.finditer(t):
        rest = t[m.end():].strip()
        first = rest.split()[0] if rest else ""
        if m.group(0) == "but" and first in ACTION_VERBS:
            main.append(t[last:m.start()])
            main.append(",")
            last = m.end()
            continue
        main.append(t[last:m.start()])
        exc.append(rest)
        last = len(t)
        break
    main.append(t[last:])
    return " ".join(main), (exc[0] if exc else "")


def parse(text, room_ctx):
    room = room_ctx if isinstance(room_ctx, RoomModel) else RoomModel(room_ctx)
    t = _normalize(text, room)
    main, exc = _split_exceptions(t)
    exclusions = set()
    if exc:
        junk = {"not", "keep", "leave", "dont", "do", "only", "just", "on", "off", "running",
                "as", "is", "alone", "untouched", "but"}
        for chunk in re.split(r",|\band\b", exc):
            chunk = " ".join(w for w in chunk.split() if w not in junk)
            devs, ok, amb, _ = _resolve(chunk, room, allow_landmark_only=True)
            exclusions |= devs

    segs = [s.strip() for s in re.split(r",|;|:|\band then\b|\bthen\b|\bafter that\b|\band\b", main)]
    segs = [s for s in segs if s]

    # pass 1: strip markers / negations, collect explicit mentions
    items = []
    for s in segs:
        reset = False
        m = MARKER.match(s)
        if m:
            reset, s = True, m.group(1).strip()
            if not s:
                items.append({"reset": True, "text": ""})
                continue
        neg = NEG.match(s)
        items.append({"reset": reset, "text": s, "neg": bool(neg), "body": neg.group(1) if neg else s})
    mentioned = set()
    for it in items:
        if it["text"] and "else" not in it["text"].split():
            d, ex, _, _ = _resolve(it["body"], room)
            if ex and it.get("neg"):
                exclusions |= d
            elif ex and "everything" not in it["text"].split():
                mentioned |= d

    commands = {}
    last_devs, last_types, last_action = set(), set(), None
    seen_action = False
    for it in items:
        if it["reset"]:
            commands = {}
        if not it["text"] or it.get("neg"):
            continue
        txt = it["body"]
        devs, explicit, amb, words = _resolve(txt, room, last_types)
        if amb:
            return None
        else_flag = "else" in words
        if else_flag:
            devs = set(room.ids) - mentioned - exclusions
            explicit = True
        action = _find_action(words, explicit)
        level = _level([w for w in words if w != "§"])
        if not devs and ("it" in words or "them" in words) and last_devs:
            devs, explicit = set(last_devs), True
        has_setword = any(w in SET_WORDS for w in words)
        if level is not None and devs:
            action = "set:%d" % level
        elif has_setword and devs and action is None:
            return None
        if action == "up":
            action = "on"
        if devs and action is None:
            first = [w for w in words if w not in ("§",)]
            starts_bare = words and (words[0] in ("the", "a", "my", "its", "both", "§", "all") or
                                      any(words[0] in p.split() for p, _ in room.aliases))
            if starts_bare and last_action and seen_action:
                action = last_action
            elif seen_action:
                return None
            else:
                continue  # context chatter before the first real command
        if action is None:
            continue
        if not devs:
            continue
        seen_action = True
        last_action = action
        last_devs = set(devs)
        last_types = {room.devices[d]["type"] for d in devs}
        for d in devs:
            commands[d] = action
    for d in exclusions:
        commands.pop(d, None)
    if not commands and not exclusions:
        commands = _comfort_fallback(t, room)
    return commands or None


_COLD_RE = re.compile(r"\b(cold|chilly|freezing|shivering|frosty)\b")
_HOT_RE = re.compile(r"\b(hot|stuffy|oven|sweating|humid|warm|fresh air|airflow)\b")
_DARK_RE = re.compile(r"\b(too dark|so dark|dark in|gloomy|cant see|pitch black)\b")
_GLARE_RE = re.compile(r"\b(blinding|sun in my eyes|privacy)\b")
_MORNING_RE = re.compile(r"\b(wake up|morning sun|let the .*sun in|let daylight in)\b")


def _comfort_fallback(t, room):
    """Deterministic fallback for pure comfort/implicit commands without explicit device names."""
    out = {}
    if _COLD_RE.search(t):
        for d in room.ids:
            if room.devices[d]["type"] in ("fan", "ac"):
                out[d] = "off"
        return out
    if _HOT_RE.search(t):
        for d in room.ids:
            if room.devices[d]["type"] in ("fan", "ac"):
                out[d] = "on"
        return out
    if _MORNING_RE.search(t):
        for d in room.ids:
            if room.devices[d]["type"] == "curtain":
                out[d] = "on"
        return out
    if _DARK_RE.search(t):
        target_type = "curtain" if ("daylight" in t or "sun" in t) else "light"
        for d in room.ids:
            if room.devices[d]["type"] == target_type:
                out[d] = "on"
        return out
    if _GLARE_RE.search(t):
        for d in room.ids:
            if room.devices[d]["type"] == "curtain":
                out[d] = "off"
        return out
    return out


def load_room(path=None):
    p = Path(path) if path else None
    return RoomModel(json.loads(p.read_text(encoding="utf-8")))
