"""Tests for config.py"""
import os
import pytest


def test_settings_load(monkeypatch):
    """Settings.load() should read from env vars."""
    monkeypatch.setenv("PLATFORM", "rpi")
    monkeypatch.setenv("STT_BACKEND", "vosk")
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Fan1")
    monkeypatch.setenv("MQTT_BROKER_PORT", "1883")

    from config import Settings
    s = Settings.load()

    assert s.platform == "rpi"
    assert s.stt_backend == "vosk"
    assert s.llm_backend == "ollama"
    assert s.mqtt_dry_run is True
    assert s.enabled_devices == ["Light1", "Fan1"]
    assert s.mqtt_broker_port == 1883


def test_settings_defaults(monkeypatch):
    """Settings should have sane defaults when env vars are absent."""
    # Clear relevant env vars
    for key in ("PLATFORM", "STT_BACKEND", "LLM_BACKEND"):
        monkeypatch.delenv(key, raising=False)

    from config import Settings
    s = Settings.load()

    assert s.platform == "pc"
    assert s.stt_backend == "groq"
    assert s.llm_backend == "groq"
    assert s.audio_rate == 16000
    assert s.llm_max_tokens == 256


def test_settings_bool_parsing(monkeypatch):
    """Boolean env vars should parse correctly."""
    monkeypatch.setenv("MQTT_DRY_RUN", "false")
    monkeypatch.setenv("TTS_ENABLED", "yes")
    monkeypatch.setenv("WAKE_WORD_ENABLED", "0")

    from config import Settings
    s = Settings.load()

    assert s.mqtt_dry_run is False
    assert s.tts_enabled is True
    assert s.wake_word_enabled is False

