"""
End-to-end pipeline test.

Runs on a normal PC with no Pi, no mic, no internet.
STT and LLM are mocked; only fast-path and parsing are exercised for real.
"""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure test environment
os.environ.setdefault("MQTT_DRY_RUN", "true")
os.environ.setdefault("ENABLED_DEVICES", "Light1,Light2")
os.environ.setdefault("PLATFORM", "pc")


@pytest.fixture
def room_context_file(tmp_path):
    """Create a temporary room_context.json."""
    ctx = {
        "devices": [
            {"id": "light1", "name": "Light 1", "type": "light",
             "x": 0.2, "y": 0.35, "zone": "desk", "near": ["desk", "monitor"]},
            {"id": "light2", "name": "Light 2", "type": "light",
             "x": 0.8, "y": 0.35, "zone": "window", "near": ["window"]},
        ]
    }
    p = tmp_path / "room_context.json"
    p.write_text(json.dumps(ctx))
    return str(p)


def test_fast_path_e2e(monkeypatch, room_context_file):
    """Test a simple command goes through fast path without hitting LLM."""
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ROOM_CONTEXT_PATH", room_context_file)
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Light2")

    # Force reload settings and pipeline room context cache
    import src.core.config as config
    new_settings = config.Settings.load()
    monkeypatch.setattr(config, "settings", new_settings)

    from src import pipeline
    pipeline._room_ctx = None
    # Also patch pipeline's own reference to settings
    monkeypatch.setattr(pipeline, "settings", new_settings, raising=False)

    result = pipeline.process_command("turn on light 1")

    assert result["path"] == "fast_path"
    assert "light1" in result["commands"]
    assert result["commands"]["light1"] == "on"


def test_llm_fallback_e2e(monkeypatch, room_context_file):
    """Test a complex command falls through to mocked LLM."""
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ROOM_CONTEXT_PATH", room_context_file)
    monkeypatch.setenv("LLM_BACKEND", "groq")

    from src import pipeline
    pipeline._room_ctx = None
    import src.core.config as config
    monkeypatch.setattr(config, "settings", config.Settings.load())

    # Mock the LLM backend
    mock_result = {
        "raw": '{"light1": "off", "light2": "off"}',
        "backend": "groq",
        "model": "mock",
        "fallback_used": False,
    }

    with patch("src.ai.llm_backend.query", return_value=mock_result):
        result = pipeline.process_command("I'm going to sleep now")

    assert result["path"] == "llm"
    assert "light1" in result["commands"]
    assert result["commands"]["light1"] == "off"


def test_wav_file_pipeline(monkeypatch, room_context_file):
    """Test processing a WAV file (mocked STT)."""
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ROOM_CONTEXT_PATH", room_context_file)

    import src.core.config as config
    new_settings = config.Settings.load()
    monkeypatch.setattr(config, "settings", new_settings)

    from src import pipeline
    pipeline._room_ctx = None
    monkeypatch.setattr(pipeline, "settings", new_settings, raising=False)

    wav_file = str(Path(__file__).parent / "fixtures" / "test_command.wav")

    with patch("src.audio.stt_backend.transcribe_file", return_value="turn on light 1"):
        result = pipeline.run_single(wav_path=wav_file)

    assert result["path"] == "fast_path"
    assert result["commands"].get("light1") == "on"


def test_no_speech_in_wav(monkeypatch, room_context_file):
    """Test graceful handling of empty transcription."""
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ROOM_CONTEXT_PATH", room_context_file)

    from src import pipeline
    pipeline._room_ctx = None

    wav_file = str(Path(__file__).parent / "fixtures" / "test_command.wav")

    with patch("src.audio.stt_backend.transcribe_file", return_value=""):
        result = pipeline.run_single(wav_path=wav_file)

    assert result.get("error") == "no_speech"

