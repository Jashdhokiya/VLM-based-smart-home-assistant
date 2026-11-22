#!/usr/bin/env python3
"""
INOT Mark1 — Metrics Summary Script

Reads logs/metrics.csv and prints per-stage statistics.

Usage:
  python scripts/metrics_summary.py
  python scripts/metrics_summary.py --last 50
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METRICS_FILE = ROOT / "logs" / "metrics.csv"


def summarise(limit: int = 0):
    if not METRICS_FILE.exists():
        print(f"No metrics file at {METRICS_FILE}")
        sys.exit(1)

    with open(METRICS_FILE, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    if limit > 0:
        reader = reader[-limit:]

    if not reader:
        print("No metric records found.")
        return

    # Group by stage
    by_stage = defaultdict(list)
    by_platform = defaultdict(int)

    for row in reader:
        stage = row.get("stage", "?")
        try:
            ms = float(row.get("duration_ms", 0))
        except ValueError:
            continue
        by_stage[stage].append(ms)
        by_platform[row.get("platform", "?")] += 1

    print(f"{'Stage':<20s} {'Count':>5s} {'Min':>8s} {'Avg':>8s} {'Max':>8s} {'P95':>8s}")
    print("─" * 60)

    for stage in sorted(by_stage.keys()):
        vals = sorted(by_stage[stage])
        n = len(vals)
        avg = sum(vals) / n
        p95_idx = int(n * 0.95)
        p95 = vals[min(p95_idx, n - 1)]
        print(f"{stage:<20s} {n:>5d} {vals[0]:>7.1f}ms {avg:>7.1f}ms {vals[-1]:>7.1f}ms {p95:>7.1f}ms")

    print(f"\nTotal records: {len(reader)}")
    print(f"Platforms: {dict(by_platform)}")


def main():
    parser = argparse.ArgumentParser(description="INOT Mark1 Metrics Summary")
    parser.add_argument("--last", type=int, default=0, help="Only summarise last N records")
    args = parser.parse_args()
    summarise(args.last)


if __name__ == "__main__":
    main()

