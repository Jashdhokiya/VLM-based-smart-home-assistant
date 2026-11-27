"""
INOT Mark1 — MQTT Executor

Publishes device commands to inot/devices/<id>/set and waits for state acks
on inot/devices/<id>/state (retained, QoS 1).

Device registry is built dynamically from room_context.json at startup so
any device type (light, fan, ac, tv, curtain, lock) is supported.

LWT / online status:
  Publish : inot/online   = "online"   (retained, on connect)
  LWT     : inot/online   = "offline"  (retained, broker delivers on disconnect)

Dry-run mode (MQTT_DRY_RUN=true) works with no broker.
"""

from __future__ import annotations

import atexit
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Device registry — built from room_context.json if available, otherwise
# falls back to the classic Light1-4 static set.
# ---------------------------------------------------------------------------

_TOPIC_PREFIX = "inot/devices"
_ONLINE_TOPIC = "inot/online"
_ESP32_STATUS_TOPIC = "inot/esp32/status"


def _build_device_registry() -> Dict[str, Dict[str, str]]:
    """
    Returns a dict  { CanonicalName: {topic, state, id} }
    built from room_context.json when present, otherwise returns
    the legacy four-light static registry.
    """
    ctx_path = Path(os.getenv("ROOM_CONTEXT_PATH", "room_context.json"))
    # Try project-relative path
    if not ctx_path.is_absolute():
        ctx_path = Path(__file__).resolve().parent.parent.parent / ctx_path

    if ctx_path.exists():
        try:
            with open(ctx_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            registry: Dict[str, Dict[str, str]] = {}
            for dev in data.get("devices", []):
                dev_id = dev.get("id", "")
                if not dev_id:
                    continue
                name = dev.get("name", dev_id)
                registry[name] = {
                    "id": dev_id,
                    "topic": f"{_TOPIC_PREFIX}/{dev_id}/set",
                    "state": f"{_TOPIC_PREFIX}/{dev_id}/state",
                }
            if registry:
                return registry
        except Exception as exc:
            print(f"[MQTT] [WARN] Could not load room_context.json: {exc}")

    # Legacy fallback
    return {
        "Light1": {
            "id": "light1",
            "topic": f"{_TOPIC_PREFIX}/light1/set",
            "state": f"{_TOPIC_PREFIX}/light1/state",
        },
        "Light2": {
            "id": "light2",
            "topic": f"{_TOPIC_PREFIX}/light2/set",
            "state": f"{_TOPIC_PREFIX}/light2/state",
        },
        "Light3": {
            "id": "light3",
            "topic": f"{_TOPIC_PREFIX}/light3/set",
            "state": f"{_TOPIC_PREFIX}/light3/state",
        },
        "Light4": {
            "id": "light4",
            "topic": f"{_TOPIC_PREFIX}/light4/set",
            "state": f"{_TOPIC_PREFIX}/light4/state",
        },
    }


# Build once at module load; server can call reload_devices() after pushing
# a new context.
ALL_DEVICES: Dict[str, Dict[str, str]] = _build_device_registry()

# Valid action payloads
PAYLOADS = {
    "On": "ON",
    "Off": "OFF",
}

# ---------------------------------------------------------------------------
# Global MQTT state
# ---------------------------------------------------------------------------
_client: Optional[mqtt.Client] = None
_client_lock = threading.Lock()
_connected = False
_pending_acks: Dict[str, dict] = {}
_pending_lock = threading.Lock()

_device_states: Dict[str, str] = {}  # populated after registry build
_esp32_status: str = "offline"
_state_callbacks: List = []


def _reset_device_states():
    global _device_states
    _device_states = {dev: "OFF" for dev in ALL_DEVICES}


_reset_device_states()


# ---------------------------------------------------------------------------
# Public registry helpers
# ---------------------------------------------------------------------------

def reload_devices():
    """
    Re-read room_context.json and rebuild the device registry.
    Call after POST /context.
    """
    global ALL_DEVICES
    ALL_DEVICES = _build_device_registry()
    _reset_device_states()
    print(f"[MQTT] Device registry reloaded: {list(ALL_DEVICES.keys())}")


def add_state_listener(callback):
    with _pending_lock:
        if callback not in _state_callbacks:
            _state_callbacks.append(callback)


def remove_state_listener(callback):
    with _pending_lock:
        if callback in _state_callbacks:
            _state_callbacks.remove(callback)


def _notify_listeners(event_type: str, data: dict):
    with _pending_lock:
        callbacks = list(_state_callbacks)
    for cb in callbacks:
        try:
            cb(event_type, data)
        except Exception as e:
            print(f"[MQTT] Listener callback error: {e}")


def get_device_states() -> Dict[str, str]:
    return dict(_device_states)


def get_device_state(device: str) -> str:
    return _device_states.get(device, "OFF")


def get_esp32_status() -> str:
    return _esp32_status


def is_connected() -> bool:
    return _connected


def enabled_devices() -> List[str]:
    env_str = os.getenv("ENABLED_DEVICES", "Light1,Light2")
    active = []
    for item in env_str.split(","):
        name = item.strip()
        for k in ALL_DEVICES:
            if k.lower() == name.lower():
                active.append(k)
                break
    return active if active else list(ALL_DEVICES.keys())[:2]


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------

def _on_connect(client, userdata, flags, rc, properties=None):
    global _connected
    if rc == 0:
        _connected = True
        print("[MQTT] Connected successfully to broker.")
        # Publish online status (retained)
        client.publish(_ONLINE_TOPIC, "online", qos=1, retain=True)
        # Subscribe to all device state topics + ESP32 status
        client.subscribe(f"{_TOPIC_PREFIX}/+/state", qos=1)
        client.subscribe(_ESP32_STATUS_TOPIC, qos=1)
        client.subscribe(_ONLINE_TOPIC, qos=1)
        _notify_listeners("mqtt_connect", {"connected": True})
    else:
        print(f"[MQTT] Connection failed with result code: {rc}")
        _notify_listeners("mqtt_connect", {"connected": False, "rc": rc})


def _on_message(client, userdata, msg):
    global _esp32_status
    topic = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore").strip()

    if topic == _ESP32_STATUS_TOPIC:
        _esp32_status = payload.lower()
        print(f"[MQTT] ESP32 Status: {payload}")
        _notify_listeners("esp32_status", {"status": _esp32_status})
        return

    if topic == _ONLINE_TOPIC:
        return  # our own LWT echo, ignore

    # Match device state topics
    matched_dev = None
    for dev_name, meta in ALL_DEVICES.items():
        if meta["state"] == topic:
            matched_dev = dev_name
            _device_states[dev_name] = payload.upper()
            break

    # Resolve pending ack
    with _pending_lock:
        if topic in _pending_acks:
            info = _pending_acks[topic]
            elapsed_ms = (time.perf_counter() - info["time"]) * 1000.0
            dev = info["device"]
            print(f"[ESP32-ACK] {dev} {payload} ({elapsed_ms:.0f} ms after publish)")
            try:
                import src.core.metrics as metrics
                metrics.log_stage("esp32_ack", elapsed_ms, note=f"{dev} {payload}")
            except Exception:
                pass
            info["event"].set()
            del _pending_acks[topic]
            _notify_listeners("device_state", {
                "device": dev,
                "state": payload.upper(),
                "source": "esp32_ack",
                "latency_ms": elapsed_ms,
            })
            return

    print(f"[MQTT] State update on {topic}: {payload}")
    if matched_dev:
        _notify_listeners("device_state", {
            "device": matched_dev,
            "state": payload.upper(),
            "source": "esp32_state",
        })


def _on_disconnect(client, userdata, disconnect_flags, rc=None, properties=None):
    global _connected
    _connected = False
    print(f"[MQTT] Disconnected (rc={rc})")


# ---------------------------------------------------------------------------
# Client lifecycle
# ---------------------------------------------------------------------------

def _get_client() -> Optional[mqtt.Client]:
    global _client, _connected

    if os.getenv("MQTT_DRY_RUN", "false").lower() in ("true", "1", "yes"):
        return None

    with _client_lock:
        if _client is not None and _connected:
            return _client

        host = os.getenv("MQTT_BROKER_HOST", "localhost")
        port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
        keepalive = int(os.getenv("MQTT_KEEPALIVE", "60"))
        client_id = os.getenv("MQTT_CLIENT_ID", "inot-mark1")

        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
            )
            client.on_connect = _on_connect
            client.on_message = _on_message
            client.on_disconnect = _on_disconnect

            # LWT — broker delivers "offline" with retain=True if we vanish
            client.will_set(_ONLINE_TOPIC, payload="offline", qos=1, retain=True)

            print(f"[MQTT] Connecting to broker at {host}:{port}...")
            client.connect(host, port, keepalive=keepalive)
            client.loop_start()

            # Wait briefly for connection
            start_wait = time.time()
            while not _connected and time.time() - start_wait < 2.0:
                time.sleep(0.05)

            _client = client
            return _client
        except Exception as e:
            print(f"[MQTT] [ERROR] Could not connect to MQTT broker {host}:{port}: {e}")
            return None


def _cleanup():
    global _client
    if _client is not None:
        try:
            _client.publish(_ONLINE_TOPIC, "offline", qos=1, retain=True)
            _client.loop_stop()
            _client.disconnect()
        except Exception:
            pass


atexit.register(_cleanup)


# ---------------------------------------------------------------------------
# Public execute API
# ---------------------------------------------------------------------------

def execute(device: str, command: str) -> bool:
    """
    Execute a command on a device via MQTT.

    device  : device name (e.g. 'Light1') or device id (e.g. 'light1')
    command : 'On', 'Off', or 'set:<value>'  (set maps to ON publish)

    Returns True on success, False on validation failure or broker error.
    """
    # Resolve device — accept both canonical name and id
    allowed = enabled_devices()
    canonical_dev = None
    for d in allowed:
        if d.lower() == device.strip().lower():
            canonical_dev = d
            break
    if canonical_dev is None:
        # Try matching by device id
        for name, meta in ALL_DEVICES.items():
            if meta["id"].lower() == device.strip().lower() and name in allowed:
                canonical_dev = name
                break

    if not canonical_dev or canonical_dev not in ALL_DEVICES:
        print(f"[MQTT] [REJECT] Device '{device}' not valid or enabled. Enabled: {allowed}")
        return False

    # Normalise command
    cmd_str = command.strip()
    if cmd_str.lower().startswith("set:"):
        # Treat set as ON; value could be used by firmware in future
        payload_str = "ON"
        canonical_cmd = "On"
    elif cmd_str.lower() in ("on", "off"):
        canonical_cmd = cmd_str.capitalize()
        payload_str = PAYLOADS[canonical_cmd]
    else:
        # Try exact match
        canonical_cmd = None
        for k in PAYLOADS:
            if k.lower() == cmd_str.lower():
                canonical_cmd = k
                break
        if not canonical_cmd:
            print(f"[MQTT] [REJECT] Command '{command}' invalid. Allowed: On, Off, set:<value>")
            return False
        payload_str = PAYLOADS[canonical_cmd]

    topic = ALL_DEVICES[canonical_dev]["topic"]
    state_topic = ALL_DEVICES[canonical_dev]["state"]

    # Dry-run
    dry_run = os.getenv("MQTT_DRY_RUN", "false").lower() in ("true", "1", "yes")
    if dry_run:
        print(f"[MQTT] [DRY RUN] topic={topic} payload={payload_str}")
        _device_states[canonical_dev] = payload_str.upper()
        _notify_listeners("device_state", {
            "device": canonical_dev,
            "state": payload_str.upper(),
            "source": "dry_run",
        })
        return True

    client = _get_client()
    if client is None:
        print(f"[MQTT] [ERROR] Broker unreachable. Cannot publish {canonical_dev} {canonical_cmd}.")
        return False

    retry_count = int(os.getenv("MQTT_RETRY_COUNT", "2"))
    retry_delay = float(os.getenv("MQTT_RETRY_DELAY_S", "0.5"))

    for attempt in range(retry_count + 1):
        ack_event = threading.Event()
        with _pending_lock:
            _pending_acks[state_topic] = {
                "event": ack_event,
                "time": time.perf_counter(),
                "device": canonical_dev,
                "command": canonical_cmd,
            }

        start_pub = time.perf_counter()
        try:
            msg_info = client.publish(topic, payload_str, qos=1, retain=False)
            msg_info.wait_for_publish(timeout=2.0)
        except Exception as e:
            print(f"[MQTT] [WARN] Publish attempt {attempt+1} failed: {e}")
            with _pending_lock:
                _pending_acks.pop(state_topic, None)
            if attempt < retry_count:
                time.sleep(retry_delay)
                continue
            return False

        pub_duration_ms = (time.perf_counter() - start_pub) * 1000.0
        try:
            import src.core.metrics as metrics
            metrics.log_stage("mqtt_publish", pub_duration_ms, note=f"{canonical_dev} {payload_str}")
        except Exception:
            pass

        _device_states[canonical_dev] = payload_str.upper()
        _notify_listeners("device_state", {
            "device": canonical_dev,
            "state": payload_str.upper(),
            "source": "mqtt_publish",
        })

        ack_timeout = float(os.getenv("ACK_TIMEOUT_S", "2"))
        ack_required = os.getenv("ACK_REQUIRED", "false").lower() in ("true", "1", "yes")
        got_ack = ack_event.wait(timeout=ack_timeout)

        if not got_ack:
            with _pending_lock:
                _pending_acks.pop(state_topic, None)
            if ack_required:
                print(f"[MQTT] [ERROR] Ack required but not received within {ack_timeout}s for {canonical_dev}.")
                if attempt < retry_count:
                    time.sleep(retry_delay)
                    continue
                return False
            else:
                print(f"[MQTT] [WARN] Ack not received within {ack_timeout}s for {canonical_dev} (ACK_REQUIRED=false).")

        return True

    return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python mqtt_executor.py <Device> <Command>")
        print("Example: python mqtt_executor.py Light1 On")
        sys.exit(1)

    success = execute(sys.argv[1], sys.argv[2])
    print(f"Result: {'SUCCESS' if success else 'FAILED'}")
    sys.exit(0 if success else 1)
