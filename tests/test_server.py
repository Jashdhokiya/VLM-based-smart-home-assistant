import pytest
from fastapi.testclient import TestClient
import os
import server

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Light2")
    with TestClient(server.app) as c:
        yield c

def test_root_serves_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "INOT" in response.text
    assert "Mark1" in response.text

def test_api_status(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "mqtt" in data
    assert "esp32" in data
    assert "ai" in data
    assert "Light1" in data["enabled_devices"]

def test_api_devices(client):
    response = client.get("/api/devices")
    assert response.status_code == 200
    data = response.json()
    assert "devices" in data
    device_names = [d["name"] for d in data["devices"]]
    assert "Light1" in device_names
    assert "Light2" in device_names

def test_api_device_toggle(client):
    response = client.post("/api/device/Light1/toggle")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["device"] == "Light1"
    assert data["new_state"] in ("ON", "OFF")

def test_api_device_set(client):
    response = client.post("/api/device/Light1/set", json={"state": "On"})
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["state"] == "ON"

    response_off = client.post("/api/device/Light1/set", json={"state": "Off"})
    assert response_off.status_code == 200
    assert response_off.json()["state"] == "OFF"

def test_api_batch_actions(client):
    res_on = client.post("/api/devices/batch", json={"action": "all_on"})
    assert res_on.status_code == 200
    assert res_on.json()["results"]["Light1"] is True

    res_off = client.post("/api/devices/batch", json={"action": "all_off"})
    assert res_off.status_code == 200
    assert res_off.json()["results"]["Light1"] is True

def test_api_spatial(client):
    response = client.get("/api/spatial")
    assert response.status_code == 200
    data = response.json()
    assert "spatial_text" in data
