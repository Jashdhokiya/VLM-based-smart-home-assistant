import os
import sys
import time
import atexit
import threading
from typing import List, Dict, Optional
from dotenv import load_dotenv
import paho.mqtt.client as mqtt
import metrics

load_dotenv()

ALL_DEVICES = {
    "Light1": {"topic": "inot/devices/light1/set", "state": "inot/devices/light1/state"},
    "Light2": {"topic": "inot/devices/light2/set", "state": "inot/devices/light2/state"},
    "Light3": {"topic": "inot/devices/light3/set", "state": "inot/devices/light3/state"},
    "Light4": {"topic": "inot/devices/light4/set", "state": "inot/devices/light4/state"},
}

PAYLOADS = {
    "On": "ON",
    "Off": "OFF",
}

_client: Optional[mqtt.Client] = None
_client_lock = threading.Lock()
_connected = False
_pending_acks: Dict[str, dict] = {}
_pending_lock = threading.Lock()

_device_states: Dict[str, str] = {dev: "OFF" for dev in ALL_DEVICES}
_esp32_status: str = "offline"
_state_callbacks: List = []

def add_state_listener(callback):
    """Registers a callback fn(event_type: str, data: dict)."""
    with _pending_lock:
        if callback not in _state_callbacks:
            _state_callbacks.append(callback)

def remove_state_listener(callback):
    """Unregisters a callback."""
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
    """Returns a copy of the current device states dictionary."""
    return dict(_device_states)

def get_device_state(device: str) -> str:
    """Returns the state of a single device (e.g. 'ON' or 'OFF')."""
    return _device_states.get(device, "OFF")

def get_esp32_status() -> str:
    """Returns the last known ESP32 status ('online', 'offline', etc.)."""
    return _esp32_status

def is_connected() -> bool:
    """Returns True if the MQTT client is currently connected to the broker."""
    return _connected

def enabled_devices() -> List[str]:
    """Returns the list of canonically enabled device names from .env."""
    env_str = os.getenv("ENABLED_DEVICES", "Light1,Light2")
    active = []
    for item in env_str.split(","):
        name = item.strip()
        for k in ALL_DEVICES:
            if k.lower() == name.lower():
                active.append(k)
                break
    return active if active else ["Light1", "Light2"]

def _on_connect(client, userdata, flags, rc, properties=None):
    global _connected
    if rc == 0:
        _connected = True
        print("[MQTT] Connected successfully to broker.")
        client.subscribe("inot/devices/+/state", qos=1)
        client.subscribe("inot/esp32/status", qos=1)
        _notify_listeners("mqtt_connect", {"connected": True})
    else:
        print(f"[MQTT] Connection failed with result code: {rc}")
        _notify_listeners("mqtt_connect", {"connected": False, "rc": rc})

def _on_message(client, userdata, msg):
    global _esp32_status
    topic = msg.topic
    payload = msg.payload.decode("utf-8", errors="ignore").strip()

    if topic == "inot/esp32/status":
        _esp32_status = payload.lower()
        print(f"[MQTT] ESP32 Status: {payload}")
        _notify_listeners("esp32_status", {"status": _esp32_status})
        return

    # Check which device this state belongs to
    matched_dev = None
    for dev_name, meta in ALL_DEVICES.items():
        if meta["state"] == topic:
            matched_dev = dev_name
            _device_states[dev_name] = payload.upper()
            break

    with _pending_lock:
        if topic in _pending_acks:
            info = _pending_acks[topic]
            elapsed_ms = (time.perf_counter() - info["time"]) * 1000.0
            dev = info["device"]
            print(f"[ESP32-ACK] {dev} {payload} ({elapsed_ms:.0f} ms after publish)")
            metrics.log_stage("esp32_ack", elapsed_ms, note=f"{dev} {payload}")
            info["event"].set()
            del _pending_acks[topic]
            _notify_listeners("device_state", {
                "device": dev,
                "state": payload.upper(),
                "source": "esp32_ack",
                "latency_ms": elapsed_ms
            })
            return

    print(f"[ESP32-ACK] State update on {topic}: {payload}")
    if matched_dev:
        _notify_listeners("device_state", {
            "device": matched_dev,
            "state": payload.upper(),
            "source": "esp32_state"
        })

def _get_client() -> Optional[mqtt.Client]:
    """Lazily initializes and connects the MQTT client singleton."""
    global _client, _connected
    if os.getenv("MQTT_DRY_RUN", "false").lower() == "true":
        return None

    with _client_lock:
        if _client is not None and _connected:
            return _client

        host = os.getenv("MQTT_BROKER_HOST", "localhost")
        port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
        keepalive = int(os.getenv("MQTT_KEEPALIVE", "60"))

        try:
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id="inot-mark1"
            )
            client.on_connect = _on_connect
            client.on_message = _on_message

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
            _client.loop_stop()
            _client.disconnect()
        except Exception:
            pass

atexit.register(_cleanup)

def execute(device: str, command: str) -> bool:
    """
    Executes a command on a physical or simulated device via MQTT.
    device: e.g. 'Light1', 'Light2'
    command: 'On' or 'Off'
    """
    allowed = enabled_devices()
    # Defense-in-depth validation
    canonical_dev = None
    for d in allowed:
        if d.lower() == device.strip().lower():
            canonical_dev = d
            break

    if not canonical_dev or canonical_dev not in ALL_DEVICES:
        print(f"[MQTT] [REJECT] Device '{device}' is not valid or enabled. Enabled: {allowed}")
        return False

    canonical_cmd = None
    for k in PAYLOADS:
        if k.lower() == command.strip().lower():
            canonical_cmd = k
            break

    if not canonical_cmd:
        print(f"[MQTT] [REJECT] Command '{command}' is invalid. Allowed: {list(PAYLOADS.keys())}")
        return False

    topic = ALL_DEVICES[canonical_dev]["topic"]
    state_topic = ALL_DEVICES[canonical_dev]["state"]
    payload = PAYLOADS[canonical_cmd]

    dry_run = os.getenv("MQTT_DRY_RUN", "false").lower() == "true"
    if dry_run:
        print(f"[MQTT] [DRY RUN] Publishing topic={topic} payload={payload}")
        _device_states[canonical_dev] = payload.upper()
        _notify_listeners("device_state", {
            "device": canonical_dev,
            "state": payload.upper(),
            "source": "dry_run"
        })
        return True

    client = _get_client()
    if client is None:
        print(f"[MQTT] [ERROR] MQTT broker unreachable. Skipping publish for {canonical_dev} {canonical_cmd}.")
        return False

    ack_event = threading.Event()
    with _pending_lock:
        _pending_acks[state_topic] = {
            "event": ack_event,
            "time": time.perf_counter(),
            "device": canonical_dev,
            "command": canonical_cmd,
        }

    start_pub = time.perf_counter()
    print(f"[MQTT] Publishing topic={topic} payload={payload}")
    msg_info = client.publish(topic, payload, qos=1, retain=False)
    
    try:
        msg_info.wait_for_publish(timeout=2.0)
        pub_duration_ms = (time.perf_counter() - start_pub) * 1000.0
        metrics.log_stage("mqtt_publish", pub_duration_ms, note=f"{canonical_dev} {payload}")
        _device_states[canonical_dev] = payload.upper()
        _notify_listeners("device_state", {
            "device": canonical_dev,
            "state": payload.upper(),
            "source": "mqtt_publish"
        })
    except Exception as e:
        print(f"[MQTT] [WARN] wait_for_publish failed for {topic}: {e}")
        with _pending_lock:
            _pending_acks.pop(state_topic, None)
        return False

    # Check for acknowledgement
    ack_timeout = float(os.getenv("ACK_TIMEOUT_S", "2"))
    ack_required = os.getenv("ACK_REQUIRED", "false").lower() == "true"
    got_ack = ack_event.wait(timeout=ack_timeout)

    if not got_ack:
        with _pending_lock:
            _pending_acks.pop(state_topic, None)
        if ack_required:
            print(f"[MQTT] [ERROR] Ack required but not received within {ack_timeout}s for {canonical_dev}.")
            return False
        else:
            print(f"[MQTT] [WARN] Ack not received within {ack_timeout}s for {canonical_dev} (ACK_REQUIRED=false).")

    return True

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python mqtt_executor.py <Device> <Command>")
        print("Example: python mqtt_executor.py Light1 On")
        sys.exit(1)

    dev_arg = sys.argv[1]
    cmd_arg = sys.argv[2]
    success = execute(dev_arg, cmd_arg)
    print(f"Result: {'SUCCESS' if success else 'FAILED'}")
    sys.exit(0 if success else 1)
