#!/usr/bin/env python3
"""
INOT Mark1 — Comprehensive Evaluation & Benchmark Suite (Complex Edition)

Evaluates intent classification, execution speed (latency), and command accuracy
across 120+ complex test cases: multi-intent, negation/exceptions, scenes,
comfort inference, spatial reasoning, level setting, conversational phrasing,
typos/synonyms, and self-corrections.

Outputs:
  - Console summary with accuracy %, per-category breakdown, and latency statistics (min, avg, p95, max)
  - Detailed CSV log saved to logs/benchmark_results_<timestamp>.csv

Conventions (room_context_room.json):
  fan1       -> Ceiling Fan      (on / off / set:<speed>)
  light_desk -> Desk Lamp        (on / off / set:<brightness>)
  light_tv   -> TV Backlight     (on / off / set:<brightness>)
  tv1        -> Smart TV         (on / off / set:<volume>)
  curtain1   -> Balcony Curtain  (on = open, off = close, set:<percent open>)

Each test case may include an optional "forbid" list: devices that must NOT
appear in the actual output (catches over-triggering on "except"/"but not" queries).
"""

import os
import sys
import time
import json
import csv
from datetime import datetime, timezone
from pathlib import Path

# Ensure root directory is in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Ensure offline execution for testing
os.environ["MQTT_DRY_RUN"] = "true"

from src.core import room_context
from src import pipeline


def T(query, expected, category, forbid=None):
    """Helper to build a compact test case."""
    return {
        "query": query,
        "expected": expected,
        "category": category,
        "forbid": forbid or [],
    }


# Shorthand for the 4 non-curtain devices turned off / on
ALL_OFF_NO_CURTAIN = {"fan1": "off", "light_desk": "off", "light_tv": "off", "tv1": "off"}

# Comprehensive complex benchmark dataset
BENCHMARK_DATA = [
    # ── 1. Multi-Intent Commands (20 cases) ─────────────────────────────────
    T("turn on the desk lamp and the ceiling fan",
      {"light_desk": "on", "fan1": "on"}, "multi_intent"),
    T("switch off the tv and the tv backlight",
      {"tv1": "off", "light_tv": "off"}, "multi_intent"),
    T("turn on the fan and turn off the desk lamp",
      {"fan1": "on", "light_desk": "off"}, "multi_intent"),
    T("open the curtains and switch on the fan",
      {"curtain1": "on", "fan1": "on"}, "multi_intent"),
    T("turn off the fan, the tv and the desk lamp",
      {"fan1": "off", "tv1": "off", "light_desk": "off"}, "multi_intent", ["light_tv", "curtain1"]),
    T("turn on smart tv and close the balcony curtain",
      {"tv1": "on", "curtain1": "off"}, "multi_intent"),
    T("switch on the desk lamp, tv backlight and ceiling fan",
      {"light_desk": "on", "light_tv": "on", "fan1": "on"}, "multi_intent", ["tv1", "curtain1"]),
    T("close the curtain, turn on the tv, and turn off the desk lamp",
      {"curtain1": "off", "tv1": "on", "light_desk": "off"}, "multi_intent"),
    T("turn off the lights and turn on the fan",
      {"light_desk": "off", "light_tv": "off", "fan1": "on"}, "multi_intent"),
    T("turn on all lights and close the curtain",
      {"light_desk": "on", "light_tv": "on", "curtain1": "off"}, "multi_intent"),
    T("fan on, desk lamp off, tv on",
      {"fan1": "on", "light_desk": "off", "tv1": "on"}, "multi_intent"),
    T("turn on tv and its backlight, then switch off the fan",
      {"tv1": "on", "light_tv": "on", "fan1": "off"}, "multi_intent"),
    T("shut the curtain and the fan",
      {"curtain1": "off", "fan1": "off"}, "multi_intent"),
    T("open the curtain and turn off all the lights",
      {"curtain1": "on", "light_desk": "off", "light_tv": "off"}, "multi_intent"),
    T("turn on the ceiling fan and set it to 3",
      {"fan1": "set:3"}, "multi_intent"),
    T("turn on the desk lamp and dim the tv backlight to 20",
      {"light_desk": "on", "light_tv": "set:20"}, "multi_intent"),
    T("set fan to 2 and turn off the tv",
      {"fan1": "set:2", "tv1": "off"}, "multi_intent"),
    T("turn off the television and the ambient light behind it",
      {"tv1": "off", "light_tv": "off"}, "multi_intent"),
    T("start the fan and open the balcony curtain",
      {"fan1": "on", "curtain1": "on"}, "multi_intent"),
    T("turn off everything in the entertainment area and the fan",
      {"tv1": "off", "light_tv": "off", "fan1": "off"}, "multi_intent", ["light_desk"]),

    # ── 2. Negation / Exceptions (12 cases) ─────────────────────────────────
    T("turn off everything except the fan",
      {"light_desk": "off", "light_tv": "off", "tv1": "off", "curtain1": "off"}, "negation", ["fan1"]),
    T("turn on all lights except the desk lamp",
      {"light_tv": "on"}, "negation", ["light_desk"]),
    T("turn off all lights but keep the tv on",
      {"light_desk": "off", "light_tv": "off"}, "negation", ["tv1"]),
    T("switch everything off but leave the ceiling fan running",
      {"light_desk": "off", "light_tv": "off", "tv1": "off", "curtain1": "off"}, "negation", ["fan1"]),
    T("turn off all devices except the tv and the backlight",
      {"fan1": "off", "light_desk": "off", "curtain1": "off"}, "negation", ["tv1", "light_tv"]),
    T("don't turn off the desk lamp, but turn off everything else",
      {"fan1": "off", "light_tv": "off", "tv1": "off", "curtain1": "off"}, "negation", ["light_desk"]),
    T("turn on everything but the curtain",
      {"fan1": "on", "light_desk": "on", "light_tv": "on", "tv1": "on"}, "negation", ["curtain1"]),
    T("turn on all except tv",
      {"fan1": "on", "light_desk": "on", "light_tv": "on", "curtain1": "on"}, "negation", ["tv1"]),
    T("turn off the entertainment zone but not the fan",
      {"tv1": "off", "light_tv": "off"}, "negation", ["fan1"]),
    T("turn off every light except the one near the laptop",
      {"light_tv": "off"}, "negation", ["light_desk"]),
    T("all off except the curtain",
      {"fan1": "off", "light_desk": "off", "light_tv": "off", "tv1": "off"}, "negation", ["curtain1"]),
    T("switch on both lights but not the fan",
      {"light_desk": "on", "light_tv": "on"}, "negation", ["fan1"]),

    # ── 3. Scene / Activity Inference (15 cases) ────────────────────────────
    T("It's movie time, make the room cinematic",
      {"tv1": "on", "light_tv": "on", "curtain1": "off", "light_desk": "off"}, "scene"),
    T("Time to wake up, let the morning sun in",
      {"curtain1": "on"}, "scene"),
    T("Heading out for the weekend, power down everything",
      ALL_OFF_NO_CURTAIN, "scene"),
    T("Study session starting, I need focus lighting and some airflow",
      {"light_desk": "on", "fan1": "on"}, "scene"),
    T("Relax mode: keep the tv backlight on and turn off the desk lamp",
      {"light_tv": "on", "light_desk": "off"}, "scene"),
    T("Gaming time, turn on the tv and the backlight, and close the curtains to reduce glare",
      {"tv1": "on", "light_tv": "on", "curtain1": "off"}, "scene"),
    T("I'm back from the office, it's hot, switch on the fan and the lights",
      {"fan1": "on", "light_desk": "on", "light_tv": "on"}, "scene"),
    T("Sunrise: open curtain and turn off all the lights",
      {"curtain1": "on", "light_desk": "off", "light_tv": "off"}, "scene"),
    T("Going to bed, but leave the fan on",
      {"light_desk": "off", "light_tv": "off", "tv1": "off"}, "scene", ["fan1"]),
    T("Presentation mode: turn on tv, switch off the backlight, keep the room lit with the desk lamp",
      {"tv1": "on", "light_tv": "off", "light_desk": "on"}, "scene"),
    T("Late night coding session, desk lamp on, everything else off",
      {"light_desk": "on", "fan1": "off", "light_tv": "off", "tv1": "off"}, "scene"),
    T("Netflix and chill",
      {"tv1": "on", "light_tv": "on"}, "scene"),
    T("Guests are arriving, turn on all the lights and the fan",
      {"light_desk": "on", "light_tv": "on", "fan1": "on"}, "scene"),
    T("Power saving mode: turn off the tv, backlight and fan",
      {"tv1": "off", "light_tv": "off", "fan1": "off"}, "scene", ["light_desk"]),
    T("Afternoon nap, close the curtain, switch off the lights and tv",
      {"curtain1": "off", "light_desk": "off", "light_tv": "off", "tv1": "off"}, "scene"),

    # ── 4. Comfort / Implicit Intent (14 cases) ─────────────────────────────
    T("it's stuffy in here",
      {"fan1": "on"}, "comfort"),
    T("I'm freezing, kill the fan",
      {"fan1": "off"}, "comfort"),
    T("the sun is blinding me on the screen",
      {"curtain1": "off"}, "comfort"),
    T("it's too dark in here",
      {"light_desk": "on", "light_tv": "on"}, "comfort"),
    T("it's too bright, switch off the lights",
      {"light_desk": "off", "light_tv": "off"}, "comfort"),
    T("I can't see my keyboard",
      {"light_desk": "on"}, "comfort"),
    T("the glare behind the tv is bugging me, cut the backlight",
      {"light_tv": "off"}, "comfort", ["tv1"]),
    T("I want some fresh air flow",
      {"fan1": "on"}, "comfort"),
    T("the room feels like an oven",
      {"fan1": "on"}, "comfort"),
    T("I'm getting a headache from the bright lights, turn off the tv backlight and desk lamp",
      {"light_tv": "off", "light_desk": "off"}, "comfort"),
    T("It's chilly, shut the balcony curtain and stop the fan",
      {"curtain1": "off", "fan1": "off"}, "comfort"),
    T("Let the daylight in, it's gloomy",
      {"curtain1": "on"}, "comfort"),
    T("I'm done watching, switch off the screen",
      {"tv1": "off"}, "comfort"),
    T("I need some privacy, draw the curtains",
      {"curtain1": "off"}, "comfort"),

    # ── 5. Complex Spatial Reasoning (15 cases) ─────────────────────────────
    T("turn on the light closest to the window",
      {"light_desk": "on"}, "spatial_complex", ["light_tv"]),
    T("turn off the device near the door",
      {"curtain1": "off"}, "spatial_complex"),
    T("switch on the lamp near my laptop",
      {"light_desk": "on"}, "spatial_complex"),
    T("turn off the light on the shelf",
      {"light_tv": "off"}, "spatial_complex", ["light_desk"]),
    T("turn on whatever is near the console",
      {"light_tv": "on", "tv1": "on"}, "spatial_complex"),
    T("turn off the thing near the balcony",
      {"curtain1": "off"}, "spatial_complex"),
    T("switch on the fan above the center of the room",
      {"fan1": "on"}, "spatial_complex"),
    T("turn on the light near the tv",
      {"light_tv": "on"}, "spatial_complex", ["light_desk"]),
    T("turn off the appliances near the shelf and console",
      {"light_tv": "off", "tv1": "off"}, "spatial_complex"),
    T("switch on the light near the window and the table",
      {"light_desk": "on"}, "spatial_complex"),
    T("turn off the light in the entertainment area",
      {"light_tv": "off"}, "spatial_complex", ["light_desk"]),
    T("turn on the device on the ceiling",
      {"fan1": "on"}, "spatial_complex"),
    T("turn off the lamp at my study table",
      {"light_desk": "off"}, "spatial_complex"),
    T("open the curtain near the balcony door",
      {"curtain1": "on"}, "spatial_complex"),
    T("close the window curtain and switch on the light near the desk",
      {"curtain1": "off", "light_desk": "on"}, "spatial_complex"),

    # ── 6. Level / Value Setting (14 cases) ─────────────────────────────────
    T("set the ceiling fan speed to level 2",
      {"fan1": "set:2"}, "set_level_complex"),
    T("dim the desk lamp to 40 percent",
      {"light_desk": "set:40"}, "set_level_complex"),
    T("make the tv backlight 25 percent brightness",
      {"light_tv": "set:25"}, "set_level_complex"),
    T("set the television volume to 15",
      {"tv1": "set:15"}, "set_level_complex"),
    T("turn the fan up to 5",
      {"fan1": "set:5"}, "set_level_complex"),
    T("lower the desk lamp brightness to 10",
      {"light_desk": "set:10"}, "set_level_complex"),
    T("set the curtain to 50 percent",
      {"curtain1": "set:50"}, "set_level_complex"),
    T("open the curtain halfway",
      {"curtain1": "set:50"}, "set_level_complex"),
    T("set desk lamp to 60 and tv backlight to 30",
      {"light_desk": "set:60", "light_tv": "set:30"}, "set_level_complex"),
    T("fan to speed 1 and lamp to 90",
      {"fan1": "set:1", "light_desk": "set:90"}, "set_level_complex"),
    T("dim all lights to 20",
      {"light_desk": "set:20", "light_tv": "set:20"}, "set_level_complex"),
    T("set both lights to 50 percent",
      {"light_desk": "set:50", "light_tv": "set:50"}, "set_level_complex"),
    T("set the fan to 3 and turn on the tv",
      {"fan1": "set:3", "tv1": "on"}, "set_level_complex"),
    T("set the desk lamp to maximum",
      {"light_desk": "set:100"}, "set_level_complex"),

    # ── 7. Conversational / Polite Phrasing (12 cases) ──────────────────────
    T("hey could you please switch on the ceiling fan for me",
      {"fan1": "on"}, "conversational"),
    T("would you mind turning off the television",
      {"tv1": "off"}, "conversational"),
    T("can you kill the desk lamp",
      {"light_desk": "off"}, "conversational"),
    T("please open up the balcony drapes",
      {"curtain1": "on"}, "conversational"),
    T("I'd like the tv backlight off, thanks",
      {"light_tv": "off"}, "conversational", ["tv1"]),
    T("Hey, can you turn on the fan and dim the lamp to 30?",
      {"fan1": "on", "light_desk": "set:30"}, "conversational"),
    T("uh, switch the, um, desk light on",
      {"light_desk": "on"}, "conversational"),
    T("would you kindly shut everything down",
      ALL_OFF_NO_CURTAIN, "conversational"),
    T("I'd appreciate it if you could light up the study area",
      {"light_desk": "on"}, "conversational"),
    T("fire up the telly",
      {"tv1": "on"}, "conversational"),
    T("yo, lights out",
      {"light_desk": "off", "light_tv": "off"}, "conversational"),
    T("please activate the ceiling fan and the smart tv",
      {"fan1": "on", "tv1": "on"}, "conversational"),

    # ── 8. Synonyms / Typos / Casing (14 cases) ─────────────────────────────
    T("turn on the cieling fan",
      {"fan1": "on"}, "synonym_typo"),
    T("swich off desk lamp",
      {"light_desk": "off"}, "synonym_typo"),
    T("turn of the tv",
      {"tv1": "off"}, "synonym_typo"),
    T("tv bcklight on",
      {"light_tv": "on"}, "synonym_typo"),
    T("fan1 off pls",
      {"fan1": "off"}, "synonym_typo"),
    T("DESK LAMP ON",
      {"light_desk": "on"}, "synonym_typo"),
    T("Turn ON The Ceiling FAN",
      {"fan1": "on"}, "synonym_typo"),
    T("kill the telly",
      {"tv1": "off"}, "synonym_typo"),
    T("flip on the study lamp",
      {"light_desk": "on"}, "synonym_typo"),
    T("shut off the drapes",
      {"curtain1": "off"}, "synonym_typo"),
    T("lights out in the tv area",
      {"light_tv": "off"}, "synonym_typo", ["light_desk"]),
    T("enable ceiling fan",
      {"fan1": "on"}, "synonym_typo"),
    T("deactivate smart tv",
      {"tv1": "off"}, "synonym_typo"),
    T("curtian open",
      {"curtain1": "on"}, "synonym_typo"),

    # ── 9. Self-Correction / Sequential Intent (10 cases) ───────────────────
    T("turn on the desk lamp, no wait, turn on the tv backlight instead",
      {"light_tv": "on"}, "correction", ["light_desk"]),
    T("turn off the fan, actually keep it on",
      {"fan1": "on"}, "correction"),
    T("switch off the tv, never mind, switch on the tv",
      {"tv1": "on"}, "correction"),
    T("turn on the lights, actually just the desk one",
      {"light_desk": "on"}, "correction", ["light_tv"]),
    T("open the curtain, no close it",
      {"curtain1": "off"}, "correction"),
    T("turn on everything, then turn the tv off",
      {"fan1": "on", "light_desk": "on", "light_tv": "on", "curtain1": "on", "tv1": "off"}, "correction"),
    T("turn off all lights and then turn the desk lamp back on",
      {"light_tv": "off", "light_desk": "on"}, "correction"),
    T("close the curtain and then open it again",
      {"curtain1": "on"}, "correction"),
    T("turn on the fan and the tv; after that turn off the tv backlight",
      {"fan1": "on", "tv1": "on", "light_tv": "off"}, "correction"),
    T("I changed my mind, leave the fan off and turn on the lamp",
      {"fan1": "off", "light_desk": "on"}, "correction"),
]


def compare_commands(actual: dict, expected: dict, forbid=None) -> bool:
    """
    Evaluates whether actual matches expected intent.
    Passes if:
      - every expected device action is present in actual with the same value, and
      - no device listed in `forbid` appears in actual.
    """
    forbid = forbid or []

    if not actual and not expected:
        return True
    if not actual:
        return False

    # Check each expected device action
    for dev, action in expected.items():
        if dev not in actual:
            return False
        act_val = str(actual[dev]).lower()
        exp_val = str(action).lower()
        if act_val != exp_val:
            return False

    # Check forbidden devices were not touched
    for dev in forbid:
        if dev in actual:
            return False

    return True


def run_benchmark(context_path: str = "room_context_room.json", max_items: int = 0):
    ctx_file = Path(context_path)
    if not ctx_file.is_absolute():
        ctx_file = ROOT_DIR / ctx_file

    if not ctx_file.exists():
        print(f"Error: Context file not found: {ctx_file}")
        sys.exit(1)

    os.environ["ROOM_CONTEXT_PATH"] = str(ctx_file)

    # Force pipeline context reload
    pipeline._room_ctx = None

    dataset = BENCHMARK_DATA[:max_items] if max_items > 0 else BENCHMARK_DATA
    total_tests = len(dataset)

    print("════════════════════════════════════════════════════════════════")
    print(f"  INOT Mark1 Benchmark Suite — {total_tests} Complex Test Sentences")
    print(f"  Room Context: {ctx_file.name}")
    print("════════════════════════════════════════════════════════════════\n")

    logs_dir = ROOT_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_csv = logs_dir / f"benchmark_results_{timestamp}.csv"

    category_stats = {}
    latencies = []
    correct_count = 0

    with open(report_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "index", "category", "query", "expected", "forbid", "actual",
            "path", "is_correct", "duration_ms"
        ])

        for i, item in enumerate(dataset, 1):
            query = item["query"]
            expected = item["expected"]
            forbid = item.get("forbid", [])
            cat = item["category"]

            if cat not in category_stats:
                category_stats[cat] = {"total": 0, "correct": 0, "latencies": []}

            category_stats[cat]["total"] += 1

            start_t = time.perf_counter()
            res = pipeline.process_command(query)
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0

            actual = res.get("commands", {})
            path_used = res.get("path", "unknown")

            is_correct = compare_commands(actual, expected, forbid)
            if is_correct:
                correct_count += 1
                category_stats[cat]["correct"] += 1

            category_stats[cat]["latencies"].append(elapsed_ms)
            latencies.append(elapsed_ms)

            mark = "PASS" if is_correct else "FAIL"
            print(f"[{i:03d}/{total_tests:03d}] [{mark}] ({path_used:9s} {elapsed_ms:6.1f}ms) \"{query}\"")
            if not is_correct:
                print(f"       Expected: {expected}")
                if forbid:
                    print(f"       Forbid:   {forbid}")
                print(f"       Actual:   {actual}")

            writer.writerow([
                i, cat, query, json.dumps(expected), json.dumps(forbid),
                json.dumps(actual), path_used, is_correct, f"{elapsed_ms:.2f}"
            ])

    # Compute overall statistics
    latencies.sort()
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
