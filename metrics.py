import os
import time
import csv
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
METRICS_FILE = os.path.join(LOGS_DIR, "metrics.csv")
RUN_ID = os.getenv("RUN_ID") or uuid.uuid4().hex[:8]

def _ensure_metrics_file():
    os.makedirs(LOGS_DIR, exist_ok=True)
    if not os.path.exists(METRICS_FILE) or os.path.getsize(METRICS_FILE) == 0:
        with open(METRICS_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp_iso",
                "run_id",
                "platform",
                "command_id",
                "stage",
                "duration_ms",
                "note"
            ])

def log_stage(stage: str, duration_ms: float, command_id: str = None, note: str = ""):
    """Appends a single stage metric entry to logs/metrics.csv."""
    _ensure_metrics_file()
    now_iso = datetime.now(timezone.utc).isoformat()
    platform = os.getenv("PLATFORM_LABEL", "laptop")
    cid = command_id or ""
    with open(METRICS_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([now_iso, RUN_ID, platform, cid, stage, f"{duration_ms:.2f}", note])

@contextmanager
def stage(stage_name: str, command_id: str = None, note: str = ""):
    """Context manager to measure and log the duration of a stage."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        log_stage(stage_name, elapsed_ms, command_id=command_id, note=note)
