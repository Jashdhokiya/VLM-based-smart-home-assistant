"""Tests for mqtt_executor.py with mocked MQTT client."""
import pytest
import mqtt_executor


def test_execute_dry_run(monkeypatch):
    """Dry-run mode should succeed without a broker."""
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Light2")

    assert mqtt_executor.execute("Light1", "On") is True
    assert mqtt_executor.execute("Light2", "Off") is True
    assert mqtt_executor.get_device_state("Light1") == "ON"
    assert mqtt_executor.get_device_state("Light2") == "OFF"


def test_execute_reject_invalid_device(monkeypatch):
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Light2")

    assert mqtt_executor.execute("Fan1", "On") is False
    assert mqtt_executor.execute("Light99", "On") is False


def test_execute_reject_invalid_command(monkeypatch):
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1")

    assert mqtt_executor.execute("Light1", "Toggle") is False
    assert mqtt_executor.execute("Light1", "Dim") is False


def test_device_states_isolated(monkeypatch):
    """Device state should update correctly in dry-run."""
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Light2")

    mqtt_executor.execute("Light1", "On")
    mqtt_executor.execute("Light2", "Off")

    states = mqtt_executor.get_device_states()
    assert states["Light1"] == "ON"
    assert states["Light2"] == "OFF"


def test_enabled_devices(monkeypatch):
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Light3")
    devs = mqtt_executor.enabled_devices()
    assert "Light1" in devs
    assert "Light3" in devs
    assert "Light2" not in devs


def test_state_listener_callback(monkeypatch):
    """State listener should be called on dry-run execute."""
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1")

    events = []
    def on_event(event_type, data):
        events.append((event_type, data))

    mqtt_executor.add_state_listener(on_event)
    try:
        mqtt_executor.execute("Light1", "On")
        assert len(events) >= 1
        assert events[-1][0] == "device_state"
        assert events[-1][1]["device"] == "Light1"
        assert events[-1][1]["state"] == "ON"
    finally:
        mqtt_executor.remove_state_listener(on_event)

