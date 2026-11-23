#!/usr/bin/env python3
"""
INOT Mark1 — Comprehensive Evaluation & Benchmark Suite

Evaluates intent classification, execution speed (latency), and command accuracy
across hundreds of test cases (fast-path, spatial/near, zone, contextual/complex).

Outputs:
  - Console summary with accuracy %, per-category breakdown, and latency statistics (min, avg, p95, max)
  - Detailed CSV log saved to logs/benchmark_results_<timestamp>.csv
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

import room_context
import pipeline

# Comprehensive benchmark dataset: 100 realistic sentences
BENCHMARK_DATA = [
    # ── 1. Simple Device Direct (20 cases) ──────────────────────────────────
    {"query": "turn on the ceiling fan", "expected": {"fan1": "on"}, "category": "direct"},
    {"query": "turn off the ceiling fan", "expected": {"fan1": "off"}, "category": "direct"},
    {"query": "fan1 on", "expected": {"fan1": "on"}, "category": "direct"},
    {"query": "fan1 off", "expected": {"fan1": "off"}, "category": "direct"},
    {"query": "switch on ceiling fan", "expected": {"fan1": "on"}, "category": "direct"},
    {"query": "switch off ceiling fan", "expected": {"fan1": "off"}, "category": "direct"},
    {"query": "turn on desk lamp", "expected": {"light_desk": "on"}, "category": "direct"},
    {"query": "turn off desk lamp", "expected": {"light_desk": "off"}, "category": "direct"},
    {"query": "desk lamp on", "expected": {"light_desk": "on"}, "category": "direct"},
    {"query": "desk lamp off", "expected": {"light_desk": "off"}, "category": "direct"},
    {"query": "switch on desk lamp", "expected": {"light_desk": "on"}, "category": "direct"},
    {"query": "switch off desk lamp", "expected": {"light_desk": "off"}, "category": "direct"},
    {"query": "turn on tv backlight", "expected": {"light_tv": "on"}, "category": "direct"},
    {"query": "turn off tv backlight", "expected": {"light_tv": "off"}, "category": "direct"},
    {"query": "tv backlight on", "expected": {"light_tv": "on"}, "category": "direct"},
    {"query": "tv backlight off", "expected": {"light_tv": "off"}, "category": "direct"},
    {"query": "turn on smart tv", "expected": {"tv1": "on"}, "category": "direct"},
    {"query": "turn off smart tv", "expected": {"tv1": "off"}, "category": "direct"},
    {"query": "smart tv on", "expected": {"tv1": "on"}, "category": "direct"},
    {"query": "smart tv off", "expected": {"tv1": "off"}, "category": "direct"},

    # ── 2. Zone-Based Commands (15 cases) ───────────────────────────────────
    {"query": "turn on desk", "expected": {"light_desk": "on"}, "category": "zone"},
    {"query": "turn off desk", "expected": {"light_desk": "off"}, "category": "zone"},
    {"query": "desk on", "expected": {"light_desk": "on"}, "category": "zone"},
    {"query": "desk off", "expected": {"light_desk": "off"}, "category": "zone"},
    {"query": "turn on ceiling", "expected": {"fan1": "on"}, "category": "zone"},
    {"query": "turn off ceiling", "expected": {"fan1": "off"}, "category": "zone"},
    {"query": "ceiling on", "expected": {"fan1": "on"}, "category": "zone"},
    {"query": "ceiling off", "expected": {"fan1": "off"}, "category": "zone"},
    {"query": "turn on window", "expected": {"curtain1": "on"}, "category": "zone"},
    {"query": "turn off window", "expected": {"curtain1": "off"}, "category": "zone"},
    {"query": "window on", "expected": {"curtain1": "on"}, "category": "zone"},
    {"query": "window off", "expected": {"curtain1": "off"}, "category": "zone"},
    {"query": "turn on entertainment", "expected": {"light_tv": "on", "tv1": "on"}, "category": "zone"},
    {"query": "turn off entertainment", "expected": {"light_tv": "off", "tv1": "off"}, "category": "zone"},
    {"query": "entertainment off", "expected": {"light_tv": "off", "tv1": "off"}, "category": "zone"},

    # ── 3. Spatial & Landmark Relations ('near X') (20 cases) ───────────────
    {"query": "turn on the light near desk", "expected": {"light_desk": "on"}, "category": "spatial"},
    {"query": "turn off the light near desk", "expected": {"light_desk": "off"}, "category": "spatial"},
    {"query": "turn on the light near laptop", "expected": {"light_desk": "on"}, "category": "spatial"},
    {"query": "turn off the light near laptop", "expected": {"light_desk": "off"}, "category": "spatial"},
    {"query": "turn on near laptop", "expected": {"light_desk": "on"}, "category": "spatial"},
    {"query": "turn off near laptop", "expected": {"light_desk": "off"}, "category": "spatial"},
    {"query": "turn on the light near table", "expected": {"light_desk": "on"}, "category": "spatial"},
    {"query": "turn off the light near table", "expected": {"light_desk": "off"}, "category": "spatial"},
    {"query": "turn on near table", "expected": {"light_desk": "on"}, "category": "spatial"},
    {"query": "turn off near table", "expected": {"light_desk": "off"}, "category": "spatial"},
    {"query": "turn on near center", "expected": {"fan1": "on"}, "category": "spatial"},
    {"query": "turn off near center", "expected": {"fan1": "off"}, "category": "spatial"},
    {"query": "turn on the device near ceiling", "expected": {"fan1": "on"}, "category": "spatial"},
    {"query": "turn off the device near ceiling", "expected": {"fan1": "off"}, "category": "spatial"},
    {"query": "turn on near balcony", "expected": {"curtain1": "on"}, "category": "spatial"},
    {"query": "turn off near balcony", "expected": {"curtain1": "off"}, "category": "spatial"},
    {"query": "turn on near door", "expected": {"curtain1": "on"}, "category": "spatial"},
    {"query": "turn off near door", "expected": {"curtain1": "off"}, "category": "spatial"},
    {"query": "turn on near shelf", "expected": {"light_tv": "on", "tv1": "on"}, "category": "spatial"},
    {"query": "turn off near console", "expected": {"light_tv": "off", "tv1": "off"}, "category": "spatial"},

    # ── 4. Type & Multi-Device / Batch (15 cases) ───────────────────────────
    {"query": "turn on all lights", "expected": {"light_desk": "on", "light_tv": "on"}, "category": "type_batch"},
    {"query": "turn off all lights", "expected": {"light_desk": "off", "light_tv": "off"}, "category": "type_batch"},
    {"query": "all lights on", "expected": {"light_desk": "on", "light_tv": "on"}, "category": "type_batch"},
    {"query": "all lights off", "expected": {"light_desk": "off", "light_tv": "off"}, "category": "type_batch"},
    {"query": "turn on all", "expected": {"fan1": "on", "light_desk": "on", "light_tv": "on", "tv1": "on", "curtain1": "on"}, "category": "type_batch"},
    {"query": "turn off all", "expected": {"fan1": "off", "light_desk": "off", "light_tv": "off", "tv1": "off", "curtain1": "off"}, "category": "type_batch"},
    {"query": "all on", "expected": {"fan1": "on", "light_desk": "on", "light_tv": "on", "tv1": "on", "curtain1": "on"}, "category": "type_batch"},
    {"query": "all off", "expected": {"fan1": "off", "light_desk": "off", "light_tv": "off", "tv1": "off", "curtain1": "off"}, "category": "type_batch"},
    {"query": "turn on lights", "expected": {"light_desk": "on", "light_tv": "on"}, "category": "type_batch"},
    {"query": "turn off lights", "expected": {"light_desk": "off", "light_tv": "off"}, "category": "type_batch"},
    {"query": "lights on", "expected": {"light_desk": "on", "light_tv": "on"}, "category": "type_batch"},
    {"query": "lights off", "expected": {"light_desk": "off", "light_tv": "off"}, "category": "type_batch"},
    {"query": "fan on", "expected": {"fan1": "on"}, "category": "type_batch"},
    {"query": "fan off", "expected": {"fan1": "off"}, "category": "type_batch"},
    {"query": "curtain off", "expected": {"curtain1": "off"}, "category": "type_batch"},

    # ── 5. Value/Level Setting (10 cases) ───────────────────────────────────
    {"query": "set ceiling fan to 3", "expected": {"fan1": "set:3"}, "category": "set_level"},
    {"query": "set ceiling fan to 5", "expected": {"fan1": "set:5"}, "category": "set_level"},
    {"query": "set desk lamp to 70", "expected": {"light_desk": "set:70"}, "category": "set_level"},
    {"query": "dim desk lamp to 20", "expected": {"light_desk": "set:20"}, "category": "set_level"},
    {"query": "set tv backlight to 100", "expected": {"light_tv": "set:100"}, "category": "set_level"},
    {"query": "dim tv backlight to 30", "expected": {"light_tv": "set:30"}, "category": "set_level"},
    {"query": "set fan1 to 4", "expected": {"fan1": "set:4"}, "category": "set_level"},
    {"query": "set smart tv to 50", "expected": {"tv1": "set:50"}, "category": "set_level"},
    {"query": "adjust desk lamp to 80", "expected": {"light_desk": "set:80"}, "category": "set_level"},
    {"query": "change tv backlight to 60", "expected": {"light_tv": "set:60"}, "category": "set_level"},

    # ── 6. Complex Contextual Natural Language (20 cases) ───────────────────
    {"query": "I am sitting at the study table working, give me light", "expected": {"light_desk": "on"}, "category": "complex"},
    {"query": "I am done studying for today, turn it off", "expected": {"light_desk": "off"}, "category": "complex"},
    {"query": "It is getting very warm in the room, start the fan", "expected": {"fan1": "on"}, "category": "complex"},
    {"query": "I feel cold, stop the fan", "expected": {"fan1": "off"}, "category": "complex"},
    {"query": "Let us watch a movie on the television", "expected": {"tv1": "on", "light_tv": "on"}, "category": "complex"},
    {"query": "Movie is over, shut down the TV setup", "expected": {"tv1": "off", "light_tv": "off"}, "category": "complex"},
    {"query": "I am going to sleep, turn everything off", "expected": {"fan1": "off", "light_desk": "off", "light_tv": "off", "tv1": "off"}, "category": "complex"},
    {"query": "Good night, shutdown all lights", "expected": {"light_desk": "off", "light_tv": "off"}, "category": "complex"},
    {"query": "Leaving the room for work, turn everything off", "expected": {"fan1": "off", "light_desk": "off", "light_tv": "off", "tv1": "off"}, "category": "complex"},
    {"query": "I just entered the living room, illuminate the space", "expected": {"light_desk": "on", "light_tv": "on"}, "category": "complex"},
    {"query": "Too much sunlight coming from the balcony, close curtains", "expected": {"curtain1": "off"}, "category": "complex"},
    {"query": "Open the balcony curtains to let light in", "expected": {"curtain1": "on"}, "category": "complex"},
    {"query": "I need to read a book on the left desk", "expected": {"light_desk": "on"}, "category": "complex"},
    {"query": "Turn off the illumination behind the television", "expected": {"light_tv": "off"}, "category": "complex"},
    {"query": "Switch on the ambient LEDs on the entertainment unit", "expected": {"light_tv": "on"}, "category": "complex"},
    {"query": "Power down the media console", "expected": {"tv1": "off", "light_tv": "off"}, "category": "complex"},
    {"query": "Turn on reading lamp and start the fan", "expected": {"light_desk": "on", "fan1": "on"}, "category": "complex"},
    {"query": "Turn off everything except the desk light", "expected": {"fan1": "off", "light_tv": "off", "tv1": "off", "light_desk": "on"}, "category": "complex"},
    {"query": "Prepare the room for night mode", "expected": {"light_desk": "off", "tv1": "off", "light_tv": "off"}, "category": "complex"},
    {"query": "Make the room bright for working", "expected": {"light_desk": "on", "light_tv": "on"}, "category": "complex"},
]


def compare_commands(actual: dict, expected: dict) -> bool:
    """
    Evaluates whether actual matches expected intent.
    Matches if all expected device actions are present in actual.
    """
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
    print(f"  INOT Mark1 Benchmark Suite — {total_tests} Test Sentences")
    print(f"  Room Context: {ctx_file.name}")
    print("════════════════════════════════════════════════════════════════\n")

    logs_dir = ROOT_DIR / "logs"
    logs_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_csv = logs_dir / f"benchmark_results_{timestamp}.csv"

    results = []
    category_stats = {}
    latencies = []
    correct_count = 0

    with open(report_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "index", "category", "query", "expected", "actual",
            "path", "is_correct", "duration_ms"
        ])

        for i, item in enumerate(dataset, 1):
            query = item["query"]
            expected = item["expected"]
            cat = item["category"]

            if cat not in category_stats:
                category_stats[cat] = {"total": 0, "correct": 0, "latencies": []}

            category_stats[cat]["total"] += 1

            start_t = time.perf_counter()
            res = pipeline.process_command(query)
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0

            actual = res.get("commands", {})
            path_used = res.get("path", "unknown")

            is_correct = compare_commands(actual, expected)
            if is_correct:
                correct_count += 1
                category_stats[cat]["correct"] += 1

            category_stats[cat]["latencies"].append(elapsed_ms)
            latencies.append(elapsed_ms)

            mark = "PASS" if is_correct else "FAIL"
            print(f"[{i:03d}/{total_tests:03d}] [{mark}] ({path_used:9s} {elapsed_ms:6.1f}ms) \"{query}\"")
            if not is_correct:
                print(f"       Expected: {expected}")
                print(f"       Actual:   {actual}")

            writer.writerow([
                i, cat, query, json.dumps(expected), json.dumps(actual),
                path_used, is_correct, f"{elapsed_ms:.2f}"
            ])

    # Compute overall statistics
    latencies.sort()
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    min_lat = latencies[0] if latencies else 0.0
    max_lat = latencies[-1] if latencies else 0.0
    p95_idx = int(len(latencies) * 0.95)
    p95_lat = latencies[min(p95_idx, len(latencies) - 1)] if latencies else 0.0
    accuracy = (correct_count / total_tests) * 100.0

    print("\n════════════════════════════════════════════════════════════════")
    print("  Benchmark Summary Results")
    print("════════════════════════════════════════════════════════════════")
    print(f"Total Tests : {total_tests}")
    print(f"Accuracy    : {correct_count}/{total_tests} ({accuracy:.1f}%)")
    print(f"Latency     : Min={min_lat:.1f}ms | Avg={avg_lat:.1f}ms | P95={p95_lat:.1f}ms | Max={max_lat:.1f}ms")
    print("\nCategory Breakdown:")
    print(f"{'Category':<15s} {'Accuracy':<14s} {'Avg Latency':<12s} {'P95 Latency':<12s}")
    print("─" * 55)

    for cat, stat in category_stats.items():
        c_acc = (stat["correct"] / stat["total"]) * 100.0
        c_lats = sorted(stat["latencies"])
        c_avg = sum(c_lats) / len(c_lats)
        c_p95 = c_lats[min(int(len(c_lats) * 0.95), len(c_lats) - 1)]
        acc_str = f"{stat['correct']}/{stat['total']} ({c_acc:.0f}%)"
        print(f"{cat:<15s} {acc_str:<14s} {c_avg:>7.1f} ms    {c_p95:>7.1f} ms")

    print("════════════════════════════════════════════════════════════════")
    print(f"Report saved to: {report_csv}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="INOT Mark1 Benchmark Suite")
    parser.add_argument("--context", default="room_context_room.json", help="Context JSON file")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of test items")
    args = parser.parse_args()

    run_benchmark(args.context, args.limit)

