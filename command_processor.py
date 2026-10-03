import os
import sys
import time
import re
import uuid
from dotenv import load_dotenv
from groq import Groq
import mqtt_executor
import safe_parse
import metrics
import transcriptor
import pyttsx3

load_dotenv()

def text_to_speech(text):
    print(text)
    if os.getenv("TTS_ENABLED", "true").lower() == "true":
        try:
            engine = pyttsx3.init()
            engine.say(text)
            engine.runAndWait()
        except Exception as e:
            print(f"[TTS] Warning: pyttsx3 failed: {e}")

_groq_client = None
_conversation_history = []
_provider = os.getenv("AI_PROVIDER", "groq").lower()

def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key or api_key == "...":
            raise ValueError("GROQ_API_KEY is not set in .env")
        _groq_client = Groq(api_key=api_key)
    return _groq_client

def response(query):
    """Sends a user query to the LLM (Groq default) with conversation context."""
    global _conversation_history
    provider = os.getenv("AI_PROVIDER", "groq").lower()

    if provider == "gemini":
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY")
        model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
        client = genai.Client(api_key=api_key)
        chat = client.chats.create(model=model)
        res = chat.send_message(query)
        return res.text

    # Default: Groq (Qwen 3.8 27B)
    client = _get_groq_client()
    model = os.getenv("GROQ_COMMAND_MODEL", os.getenv("GROQ_LLM_MODEL", "qwen/qwen3.8-27b"))

    _conversation_history.append({"role": "user", "content": query})
    res = client.chat.completions.create(
        model=model,
        messages=_conversation_history,
        temperature=0.1
    )
    reply = res.choices[0].message.content
    _conversation_history.append({"role": "assistant", "content": reply})
    return reply

def init_conversation(spatial_information=None):
    global _conversation_history
    if spatial_information is None:
        try:
            with open("spatial_information.txt", "r", encoding="utf-8") as f:
                spatial_information = f.read()
        except Exception:
            spatial_information = "Light1 is on the left. Light2 is on the right."

    devices = mqtt_executor.enabled_devices()
    devices_str = ", ".join(devices)

    system_prompt = (
        "You are an IoT Device Controller. Your task is to execute user commands based on natural language input.\n"
        "Take decisions based on the user's natural language.\n"
        "For instance if the user completes studying, turn off the lights that are used for studying - probably near a desk.\n"
        "Another instance: if the user wants to sleep, turn off all the lights and turn on the fan.\n"
        "Respond strictly with only the necessary device commands in the format of a dictionary with key value pairs as 'deviceName: On' or 'deviceName: Off'.\n"
        f"Valid device names are {devices_str}. Respond with a bare dictionary only (e.g. {{'Light1': 'On'}}).\n"
        "1. If the user indicates they are leaving the room, turn off all devices.\n"
        "2. If the user is present in the room, intelligently turn on devices that would be useful for them.\n"
        "3. Provide only the dictionary commands without additional explanations or markdown formatting.\n\n"
        "The spatial information for your context is as follows:\n" + str(spatial_information)
    )

    _conversation_history = [
        {"role": "system", "content": system_prompt}
    ]
    return _conversation_history

def process_single_command(comm: str, spatial_information=None, speak: bool = False) -> dict:
    """Processes a single natural language command, invokes LLM, parses commands, and executes them."""
    global _conversation_history
    if not _conversation_history:
        init_conversation(spatial_information)

    command_id = uuid.uuid4().hex[:6]
    start_time = time.perf_counter()

    with metrics.stage("llm_decision", command_id=command_id):
        res = response(comm)

    with metrics.stage("parse", command_id=command_id):
        valid, rejected = safe_parse.parse_device_commands(res, allowed=mqtt_executor.enabled_devices())

    executed = {}
    for device, cmd in valid.items():
        if speak:
            text_to_speech(f"Turning {cmd} the device {device}")
        success = mqtt_executor.execute(device, cmd)
        executed[device] = {"command": cmd, "success": success}

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    tts_msg = ""
    if valid:
        tts_parts = [f"Turning {cmd} {dev}" for dev, cmd in valid.items()]
        tts_msg = ", and ".join(tts_parts)
    else:
        tts_msg = "No matching device action recognized."

    return {
        "command_id": command_id,
        "query": comm,
        "raw_response": res,
        "valid_commands": valid,
        "rejected": rejected,
        "executed": executed,
        "tts_message": tts_msg,
        "duration_ms": elapsed_ms
    }

def generate(spatial_information):
    global _conversation_history
    init_conversation(spatial_information)

    provider = os.getenv("AI_PROVIDER", "groq").lower()
    print(f"[COMMAND] Command Processor started (Provider: {provider.upper()}).")

    while True:
        comm = transcriptor.transcribe()
        if not comm or not comm.strip():
            continue

        # Normalise exit check: strip punctuation, lowercase
        norm_comm = re.sub(r"[^\w\s]", "", comm).strip().lower()
        if norm_comm in ("exit", "quit"):
            print("[COMMAND] Exit requested. Exiting loop.")
            break

        print(f"[TRANSCRIPTION] User: {comm}")
        result = process_single_command(comm, spatial_information, speak=True)
        print(f"[COMMAND] LLM raw output: {result['raw_response']}")
        if result['rejected']:
            print(f"[PARSE] Rejected items: {result['rejected']}")
        if not result['valid_commands']:
            print("[PARSE] No valid executable commands found in response.")

        print("Tell your next command in 3 seconds..")
        for i in range(3):
            time.sleep(1)
            print(3 - i, "....")
