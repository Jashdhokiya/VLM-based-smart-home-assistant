"""Tests for room_context.py"""
import json
import pytest
import room_context


VALID_CONTEXT = {
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


def test_validate_valid():
    ok, errors = room_context.validate(VALID_CONTEXT)
    assert ok is True
    assert errors == []


def test_validate_missing_devices():
    ok, errors = room_context.validate({"room_name": "test"})
    assert ok is False
    assert any("missing" in e.lower() or "devices" in e.lower() for e in errors)


def test_validate_empty_devices():
    ok, errors = room_context.validate({"devices": []})
    assert ok is False


def test_validate_bad_type():
    ctx = {"devices": [
        {"id": "x", "name": "X", "type": "toaster", "x": 0.5, "y": 0.5, "zone": "kitchen", "near": []}
    ]}
    ok, errors = room_context.validate(ctx)
    assert ok is False
    assert any("type" in e for e in errors)


def test_validate_bad_coords():
    ctx = {"devices": [
        {"id": "x", "name": "X", "type": "light", "x": 1.5, "y": -0.1, "zone": "room", "near": []}
    ]}
    ok, errors = room_context.validate(ctx)
    assert ok is False
    assert len(errors) >= 2  # both x and y bad


def test_validate_duplicate_id():
    ctx = {"devices": [
        {"id": "light1", "name": "A", "type": "light", "x": 0.1, "y": 0.1, "zone": "a", "near": []},
        {"id": "light1", "name": "B", "type": "light", "x": 0.9, "y": 0.9, "zone": "b", "near": []},
    ]}
    ok, errors = room_context.validate(ctx)
    assert ok is False
    assert any("duplicate" in e for e in errors)


def test_load_from_file(tmp_path):
    p = tmp_path / "ctx.json"
    p.write_text(json.dumps(VALID_CONTEXT))
    ctx = room_context.load(p)
    assert len(room_context.device_list(ctx)) == 2


def test_load_missing_file():
    with pytest.raises(FileNotFoundError):
        room_context.load("/nonexistent/ctx.json")


def test_load_invalid_file(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"devices": [{"id": "x"}]}))
    with pytest.raises(ValueError):
        room_context.load(p)


def test_device_by_id():
    d = room_context.device_by_id(VALID_CONTEXT, "fan1")
    assert d is not None
    assert d["name"] == "Ceiling Fan"


def test_device_by_id_case_insensitive():
    d = room_context.device_by_id(VALID_CONTEXT, "LIGHT1")
    assert d is not None
    assert d["id"] == "light1"


def test_devices_in_zone():
    devs = room_context.devices_in_zone(VALID_CONTEXT, "desk")
    assert len(devs) == 1
    assert devs[0]["id"] == "light1"


def test_devices_near():
    devs = room_context.devices_near(VALID_CONTEXT, "bed")
    assert len(devs) == 1
    assert devs[0]["id"] == "fan1"


def test_build_prompt_context():
    text = room_context.build_prompt_context(VALID_CONTEXT)
    assert "light1" in text
    assert "fan1" in text
    assert "desk" in text

