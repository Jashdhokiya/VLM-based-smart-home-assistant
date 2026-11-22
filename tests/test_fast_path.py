"""Tests for fast_path.py"""
import pytest
import fast_path


CONTEXT = {
    "devices": [
        {
            "id": "light1",
            "name": "Light 1",
            "type": "light",
            "x": 0.2,
            "y": 0.35,
            "zone": "desk",
            "near": ["desk", "monitor"],
        },
        {
            "id": "light2",
            "name": "Light 2",
            "type": "light",
            "x": 0.8,
            "y": 0.35,
            "zone": "window",
            "near": ["window", "curtain"],
        },
        {
            "id": "fan1",
            "name": "Ceiling Fan",
            "type": "fan",
            "x": 0.5,
            "y": 0.15,
            "zone": "center",
            "near": ["bed"],
        },
    ]
}


class TestSimpleOnOff:
    def test_turn_on_specific(self):
        result = fast_path.match("turn on light 1", CONTEXT)
        assert result is not None
        assert result.get("light1") == "on"

    def test_turn_off_specific(self):
        result = fast_path.match("turn off the ceiling fan", CONTEXT)
        assert result is not None
        assert result.get("fan1") == "off"

    def test_switch_on(self):
        result = fast_path.match("switch on light 2", CONTEXT)
        assert result is not None
        assert result.get("light2") == "on"

    def test_trailing_on(self):
        result = fast_path.match("light 1 on", CONTEXT)
        assert result is not None
        assert result.get("light1") == "on"


class TestAllDevices:
    def test_all_on(self):
        result = fast_path.match("turn on all lights", CONTEXT)
        assert result is not None
        assert result.get("light1") == "on"
        assert result.get("light2") == "on"
        assert "fan1" not in result

    def test_all_off(self):
        result = fast_path.match("turn off everything", CONTEXT)
        assert result is not None
        assert len(result) == 3
        assert all(v == "off" for v in result.values())

    def test_all_lights_off(self):
        result = fast_path.match("all lights off", CONTEXT)
        assert result is not None
        assert result.get("light1") == "off"
        assert result.get("light2") == "off"
        assert "fan1" not in result


class TestZoneAndNear:
    def test_by_zone(self):
        result = fast_path.match("turn on desk", CONTEXT)
        assert result is not None
        assert "light1" in result

    def test_near_window(self):
        result = fast_path.match("turn on the light near window", CONTEXT)
        assert result is not None
        assert "light2" in result

    def test_near_bed(self):
        result = fast_path.match("turn off near bed", CONTEXT)
        assert result is not None
        assert "fan1" in result


class TestSetValue:
    def test_set_brightness(self):
        result = fast_path.match("set light 1 to 50", CONTEXT)
        assert result is not None
        assert result.get("light1") == "set:50"


class TestNoMatch:
    def test_ambiguous(self):
        """Complex/ambiguous commands should return None for LLM fallback."""
        result = fast_path.match("I'm going to sleep now", CONTEXT)
        assert result is None

    def test_empty(self):
        assert fast_path.match("", CONTEXT) is None

    def test_nonsense(self):
        assert fast_path.match("the weather is nice today", CONTEXT) is None

