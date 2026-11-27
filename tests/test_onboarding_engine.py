"""
Tests for src/onboarding/onboarding_engine.py and open onboarding endpoints in src/web/server.py.

Runs without network access, GPU, OpenCV, or physical Raspberry Pi hardware.
"""

import base64
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("MQTT_DRY_RUN", "true")
os.environ.setdefault("ENABLED_DEVICES", "Light1,Light2")
os.environ.setdefault("PLATFORM", "rpi")

# Minimal valid 1x1 JPEG bytes (SOI + APP0 + EOI)
SAMPLE_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    b"\xff\xd9"
)

SAMPLE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _mock_httpx_response(status_code: int, json_body: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = json.dumps(json_body)
    resp.json.return_value = json_body
    return resp


def test_detect_image_mime():
    from src.onboarding import onboarding_engine

    assert onboarding_engine.detect_image_mime(SAMPLE_JPEG) == "image/jpeg"
    assert onboarding_engine.detect_image_mime(SAMPLE_PNG) == "image/png"

    with pytest.raises(ValueError):
        onboarding_engine.detect_image_mime(b"")

    with pytest.raises(ValueError):
        onboarding_engine.detect_image_mime(b"not an image file")


def test_prepare_image_size_limit():
    from src.onboarding import onboarding_engine

    with pytest.raises(ValueError, match="exceeds limit"):
        onboarding_engine.prepare_image(SAMPLE_JPEG + b"x" * 2048, max_mb=0.001, max_dim=0)


def test_normalize_context_handles_aliases_percentages_and_duplicates():
    from src.onboarding import onboarding_engine
    from src.core import room_context

    raw = {
        "context": {
            "room_name": "Studio",
            "devices": [
                {
                    "id": "Lamp One",
                    "name": "Desk Lamp",
                    "type": "lamp",
                    "x": 25,
                    "y": 40,
                    "zone": "desk",
                    "near": "desk, monitor",
                },
                {
                    "id": "Lamp One",
                    "name": "Window Lamp",
                    "type": "led",
                    "x": 0.8,
                    "y": 0.3,
                    "zone": "window",
                    "near": ["window"],
                },
                {
                    "id": "ac1",
                    "name": "Split AC",
                    "type": "air conditioner",
                    "x": 0.9,
                    "y": 0.1,
                    "zone": "",
                    "near": [],
                },
            ],
        }
    }

    normalized = onboarding_engine.normalize_context(raw)
    ok, errors = room_context.validate(normalized)
    assert ok is True, errors
    assert normalized["room_name"] == "Studio"
    assert [d["id"] for d in normalized["devices"]] == ["lamp_one", "lamp_one_2", "ac1"]
    assert normalized["devices"][0]["type"] == "light"
    assert normalized["devices"][0]["x"] == 0.25
    assert normalized["devices"][0]["y"] == 0.4
    assert normalized["devices"][0]["near"] == ["desk", "monitor"]
    assert normalized["devices"][2]["type"] == "ac"
    assert normalized["devices"][2]["zone"] == "room"


def test_generate_context_groq_provider(monkeypatch):
    from src.onboarding import onboarding_engine

    monkeypatch.setenv("ONBOARDING_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    monkeypatch.setenv("ONBOARDING_MAX_IMAGE_DIM", "0")

    vlm_json = {
        "room_name": "Bedroom",
        "devices": [
            {
                "id": "light1",
                "name": "Bedside Lamp",
                "type": "light",
                "x": 0.2,
                "y": 0.5,
                "zone": "bed",
                "near": ["bed", "nightstand"],
            }
        ],
    }
    mock_resp = _mock_httpx_response(
        200,
        {"choices": [{"message": {"content": json.dumps(vlm_json)}}]},
    )

    with patch("src.onboarding.onboarding_engine.httpx.post", return_value=mock_resp) as post_mock:
        res = onboarding_engine.generate_context(
            SAMPLE_JPEG, room_name="Bedroom", device_hints="1 lamp"
        )

    assert post_mock.called
    assert res["provider"] == "groq"
    assert res["validation"]["valid"] is True
    assert res["context"]["devices"][0]["id"] == "light1"


def test_generate_context_gemini_provider(monkeypatch):
    from src.onboarding import onboarding_engine

    monkeypatch.setenv("ONBOARDING_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "gem_test_key")
    monkeypatch.setenv("ONBOARDING_MAX_IMAGE_DIM", "0")

    vlm_json = {
        "room_name": "Living Room",
        "devices": [
            {
                "id": "tv1",
                "name": "Living Room TV",
                "type": "tv",
                "x": 0.5,
                "y": 0.6,
                "zone": "entertainment",
                "near": ["console"],
            }
        ],
    }
    mock_resp = _mock_httpx_response(
        200,
        {"candidates": [{"content": {"parts": [{"text": json.dumps(vlm_json)}]}}]},
    )

    with patch("src.onboarding.onboarding_engine.httpx.post", return_value=mock_resp) as post_mock:
        res = onboarding_engine.generate_context(SAMPLE_PNG)

    assert post_mock.called
    assert res["provider"] == "gemini"
    assert res["mime_type"] == "image/png"
    assert res["validation"]["valid"] is True
    assert res["context"]["devices"][0]["type"] == "tv"


def test_generate_context_cloud_url_provider(monkeypatch):
    from src.onboarding import onboarding_engine

    monkeypatch.setenv("ONBOARDING_PROVIDER", "cloud_url")
    monkeypatch.setenv("CLOUD_ONBOARDING_URL", "https://cloud.example.com/onboard")
    monkeypatch.setenv("ONBOARDING_MAX_IMAGE_DIM", "0")

    cloud_payload = {
        "context": {
            "room_name": "Study",
            "devices": [
                {
                    "id": "fan1",
                    "name": "Ceiling Fan",
                    "type": "fan",
                    "x": 0.5,
                    "y": 0.15,
                    "zone": "center",
                    "near": ["desk"],
                }
            ],
        }
    }
    mock_resp = _mock_httpx_response(200, cloud_payload)

    with patch("src.onboarding.onboarding_engine.httpx.post", return_value=mock_resp) as post_mock:
        res = onboarding_engine.generate_context(SAMPLE_JPEG)

    assert post_mock.called
    assert res["provider"] == "cloud_url"
    assert res["validation"]["valid"] is True
    assert res["context"]["devices"][0]["id"] == "fan1"


def test_push_context_to_rpi():
    from src.onboarding import onboarding_engine

    ctx = {
        "room_name": "Office",
        "devices": [
            {
                "id": "light1",
                "name": "Desk Lamp",
                "type": "light",
                "x": 0.3,
                "y": 0.5,
                "zone": "desk",
                "near": ["desk"],
            }
        ],
    }
    mock_resp = _mock_httpx_response(200, {"status": "ok", "device_count": 1})

    with patch("src.onboarding.onboarding_engine.httpx.post", return_value=mock_resp) as post_mock:
        res = onboarding_engine.push_context_to_rpi(ctx, rpi_url="http://mark1pi.local:8000")

    assert res["target_url"] == "http://mark1pi.local:8000/context"
    assert res["status_code"] == 200
    assert post_mock.call_args.kwargs["json"] == ctx


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Light2")
    monkeypatch.setenv("ROOM_CONTEXT_PATH", str(tmp_path / "room_context.json"))
    monkeypatch.setenv("IMAGE_PATH", str(tmp_path / "scene.jpg"))
    monkeypatch.setenv("ONBOARDING_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    monkeypatch.setenv("ONBOARDING_MAX_IMAGE_DIM", "0")

    sys.modules.setdefault("command_processor", MagicMock())
    sys.modules.setdefault("transcriptor", MagicMock())

    from src.onboarding import onboarding_engine
    monkeypatch.setattr(onboarding_engine, "DEFAULT_DRAFT_FILE", tmp_path / "draft.json")

    from fastapi.testclient import TestClient
    from src.web import server
    with TestClient(server.app) as c:
        yield c, tmp_path


def test_onboard_status_and_upload_and_apply_endpoints(api_client):
    client, tmp_path = api_client

    status_res = client.get("/api/onboard/status")
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert status_data["ready"] is True
    assert status_data["provider"] == "groq"

    vlm_json = {
        "room_name": "Bedroom",
        "devices": [
            {
                "id": "light1",
                "name": "Desk Light",
                "type": "light",
                "x": 0.25,
                "y": 0.45,
                "zone": "desk",
                "near": ["desk"],
            }
        ],
    }
    mock_resp = _mock_httpx_response(
        200,
        {"choices": [{"message": {"content": json.dumps(vlm_json)}}]},
    )

    with patch("src.onboarding.onboarding_engine.httpx.post", return_value=mock_resp):
        upload_res = client.post(
            "/api/onboard/upload",
            files={"image": ("room.jpg", SAMPLE_JPEG, "image/jpeg")},
            data={"room_name": "Bedroom", "device_hints": "desk light"},
        )

    assert upload_res.status_code == 200
    body = upload_res.json()
    assert body["status"] == "ok"
    assert body["validation"]["valid"] is True
    assert body["saved"] is False
    assert body["context"]["devices"][0]["id"] == "light1"
    assert (tmp_path / "scene.jpg").exists()

    draft_res = client.get("/api/onboard/draft")
    assert draft_res.status_code == 200
    assert draft_res.json()["context"]["room_name"] == "Bedroom"

    apply_res = client.post("/api/onboard/apply")
    assert apply_res.status_code == 200
    assert apply_res.json()["device_count"] == 1
    assert (tmp_path / "room_context.json").exists()


def test_onboard_analyze_base64_and_push_to_rpi(api_client):
    client, _ = api_client

    vlm_json = {
        "room_name": "Lab",
        "devices": [
            {
                "id": "fan1",
                "name": "Exhaust Fan",
                "type": "fan",
                "x": 0.6,
                "y": 0.2,
                "zone": "ceiling",
                "near": ["window"],
            }
        ],
    }
    cloud_resp = _mock_httpx_response(
        200,
        {"choices": [{"message": {"content": json.dumps(vlm_json)}}]},
    )
    rpi_resp = _mock_httpx_response(200, {"status": "ok", "device_count": 1})

    b64_img = base64.b64encode(SAMPLE_JPEG).decode("ascii")
    with patch("src.onboarding.onboarding_engine.httpx.post", side_effect=[cloud_resp, rpi_resp]):
        res = client.post(
            "/api/onboard/analyze",
            json={
                "image_base64": f"data:image/jpeg;base64,{b64_img}",
                "save": True,
                "push_to_rpi": True,
                "rpi_url": "http://192.168.1.50:8000",
            },
        )

    assert res.status_code == 200
    data = res.json()
    assert data["saved"] is True
    assert data["pushed_to_rpi"] is True
    assert data["rpi_push"]["target_url"] == "http://192.168.1.50:8000/context"

    val_res = client.post("/api/onboard/validate?normalize=false", json={"devices": [{"id": "bad"}]})
    assert val_res.status_code == 200
    assert val_res.json()["valid"] is False

    with patch("src.onboarding.onboarding_engine.httpx.post", return_value=rpi_resp):
        push_res = client.post(
            "/api/onboard/push-to-rpi",
            json={"rpi_url": "http://mark1pi.local:8000"},
        )
    assert push_res.status_code == 200
    assert push_res.json()["status"] == "ok"
