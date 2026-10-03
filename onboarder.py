import os
import nl_processor
import annotator
import pyttsx3
import transcriptor
import spatial_inferencer
import safe_parse
import metrics
from dotenv import load_dotenv

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

def onboarding_system(file_path):
    print("[ONBOARDING] Starting onboarding process...")
    system = "Welcome to the onboarding system of InOT. Please let me know the smart devices in the home."
    text_to_speech(system)

    user = transcriptor.transcribe()
    image_path = file_path

    with metrics.stage("nl_extract"):
        obj_str = nl_processor.refine(user)
    text_to_speech(f"I have received {obj_str}")

    try:
        obj_dic = safe_parse.parse_device_counts(obj_str)
    except Exception as e:
        print(f"[ONBOARDING] Initial parse error: {e}. Re-asking user once...")
        text_to_speech("I could not understand the device count. Please tell me again, for example: two lights.")
        user = transcriptor.transcribe()
        with metrics.stage("nl_extract"):
            obj_str = nl_processor.refine(user)
        text_to_speech(f"I have received {obj_str}")
        obj_dic = safe_parse.parse_device_counts(obj_str)

    obj_list = list(obj_dic.keys())
    print(f"[ONBOARDING] Detected device dictionary: {obj_dic}")

    text_to_speech("Starting the Fully Automatic Annotation Process...")

    with metrics.stage("detection_api"):
        object_list = annotator.start_annot(obj_list, image_path, obj_dic)

    text_to_speech("Annotation Complete! Moving to Spatial Reasoning System.")
    print(f"[ONBOARDING] Annotated objects: {object_list}")

    with metrics.stage("spatial_gpt"):
        spatial = spatial_inferencer.information(object_list)

    with open("spatial_information.txt", "w", encoding="utf-8") as file:
        file.write(spatial)

    print("[ONBOARDING] Spatial Data Saved to spatial_information.txt.")
    return spatial