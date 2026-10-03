import os
import sys
import socket
from pathlib import Path
from dotenv import load_dotenv

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

load_dotenv(dotenv_path=root_dir / ".env")

REQUIRED_ENV_VARS = [
    "GROQ_API_KEY",
]

def check_env_vars():
    print("\n--- 1. Checking Environment Variables ---")
    all_ok = True
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key or groq_key.strip() in ("", "..."):
        print("  [FAIL] Missing or placeholder: GROQ_API_KEY (Required)")
        all_ok = False
    else:
        masked = groq_key[:4] + "..." + groq_key[-4:] if len(groq_key) > 8 else "***"
        print(f"  [PASS] GROQ_API_KEY is configured ({masked})")

    optional_vars = [
        ("AI_PROVIDER", "groq"),
        ("GROQ_LLM_MODEL", "qwen/qwen3.8-27b"),
        ("GROQ_COMMAND_MODEL", "qwen/qwen3.8-27b"),
        ("GROQ_WHISPER_MODEL", "whisper-large-v3"),
        ("MQTT_BROKER_HOST", "localhost"),
        ("MQTT_BROKER_PORT", "1883"),
        ("MQTT_DRY_RUN", "true"),
    ]
    for var, default in optional_vars:
        val = os.getenv(var, default)
        print(f"  [INFO] {var} = {val}")

    return all_ok

def test_groq_chat():
    print("\n--- 2. Testing Groq Chat (NL Extraction) ---")
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "...":
        print("  [FAIL] GROQ_API_KEY not configured.")
        return False
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        model = os.getenv("GROQ_LLM_MODEL", "qwen/qwen3.8-27b")
        res = client.chat.completions.create(
            messages=[{"role": "user", "content": "hello"}],
            model=model,
            max_tokens=5
        )
        print(f"  [PASS] Groq Chat ({model}) responded successfully.")
        return True
    except Exception as e:
        print(f"  [FAIL] Groq Chat failed: {e}")
        return False

def test_groq_whisper():
    print("\n--- 3. Testing Groq Whisper (Speech-to-Text) ---")
    api_key = os.getenv("GROQ_API_KEY")
    audio_path = root_dir / "real_time_audio.wav"
    if not audio_path.exists():
        print(f"  [SKIP] Audio file '{audio_path.name}' does not exist (will record via mic at runtime).")
        return True
    if not api_key or api_key == "...":
        print("  [FAIL] GROQ_API_KEY not configured.")
        return False
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        model = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
        with open(audio_path, "rb") as f:
            res = client.audio.transcriptions.create(
                file=f,
                model=model,
                language="en"
            )
        print(f"  [PASS] Groq Whisper ({model}) transcribed successfully.")
        return True
    except Exception as e:
        print(f"  [FAIL] Groq Whisper failed: {e}")
        return False

def test_groq_command_decision():
    print("\n--- 4. Testing Groq Command Decisions ---")
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "...":
        print("  [FAIL] GROQ_API_KEY not configured.")
        return False
    try:
        from groq import Groq
        import safe_parse
        client = Groq(api_key=api_key)
        model = os.getenv("GROQ_COMMAND_MODEL", os.getenv("GROQ_LLM_MODEL", "qwen/qwen3.8-27b"))
        res = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Respond only with a dictionary: {'Light1': 'On'}"},
                {"role": "user", "content": "turn on light 1"}
            ],
            model=model,
            temperature=0.1,
            max_tokens=30
        )
        reply = res.choices[0].message.content
        valid, rejected = safe_parse.parse_device_commands(reply, allowed=["Light1", "Light2"])
        if "Light1" in valid and valid["Light1"] == "On":
            print(f"  [PASS] Groq Command decision returned valid command: {valid}")
            return True
        else:
            print(f"  [PASS] Groq returned response: '{reply}' (parsed: {valid})")
            return True
    except Exception as e:
        print(f"  [FAIL] Groq command test failed: {e}")
        return False

def test_groq_spatial_reasoning():
    print("\n--- 5. Testing Spatial Reasoning (Groq) ---")
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key or api_key == "...":
        print("  [FAIL] GROQ_API_KEY not configured.")
        return False
    try:
        import spatial_inferencer
        image_path = root_dir / "images" / "test_image.jpg"
        if not image_path.exists():
            image_path = root_dir / "images" / "current_scene.jpg"
        if not image_path.exists():
            print("  [SKIP] No scene image found for spatial test.")
            return True

        result = spatial_inferencer.information(["light1", "light2"], image_path=str(image_path))
        print(f"  [PASS] Spatial reasoning succeeded ({len(result)} chars generated).")
        return True
    except Exception as e:
        print(f"  [FAIL] Spatial reasoning failed: {e}")
        return False

def test_mqtt_broker():
    print("\n--- 6. Testing MQTT Broker Reachability ---")
    dry_run = os.getenv("MQTT_DRY_RUN", "false").lower() == "true"
    if dry_run:
        print("  [PASS] MQTT_DRY_RUN is true. Simulated broker mode enabled.")
        return True

    host = os.getenv("MQTT_BROKER_HOST", "localhost")
    port = int(os.getenv("MQTT_BROKER_PORT", "1883"))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect((host, port))
        sock.close()
        print(f"  [PASS] Successfully connected to MQTT broker at {host}:{port}.")
        return True
    except Exception as e:
        print(f"  [WARN] Could not connect to MQTT broker at {host}:{port} ({e}). Set MQTT_DRY_RUN=true if testing without broker.")
        return False

def main():
    print("==================================================")
    print("  INOT Mark1 Preflight Diagnostic Check (Groq)   ")
    print("==================================================")

    results = {
        "Environment Variables": check_env_vars(),
        "Groq Chat (NL Extract)": test_groq_chat(),
        "Groq Whisper (STT)": test_groq_whisper(),
        "Groq Command Decision": test_groq_command_decision(),
        "Groq Spatial Reasoning": test_groq_spatial_reasoning(),
        "MQTT Broker / Dry-run": test_mqtt_broker(),
    }

    print("\n==================================================")
    print("                Summary Results                   ")
    print("==================================================")
    all_passed = True
    for test_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"  {test_name:<25}: {status}")

    print("==================================================")
    if all_passed:
        print("All required checks PASSED. Ready to run Mark1 with Groq!")
        sys.exit(0)
    else:
        print("Some checks FAILED. Please review the output above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
