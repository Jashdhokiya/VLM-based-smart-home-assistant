import os
from pathlib import Path
from dotenv import load_dotenv
import camera_capture
from onboarder import onboarding_system
import command_processor

load_dotenv()

def env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")

def main():
    force = env_bool("FORCE_ONBOARDING", True)
    spatial_file = Path("spatial_information.txt")

    if force or not spatial_file.exists():
        if env_bool("CAMERA_ENABLED", True):
            print("[CAMERA] Capturing scene from webcam...")
            image_path = camera_capture.capture_scene()
        else:
            image_path = os.getenv("IMAGE_PATH", "images/test_image.jpg")
            print(f"[CAMERA] CAMERA_ENABLED=false. Using existing image at {image_path}")

        spatial = onboarding_system(image_path)
    else:
        print("[ONBOARDING] Reusing existing spatial_information.txt")
        with open(spatial_file, "r", encoding="utf-8") as f:
            spatial = f.read()

    command_processor.generate(spatial)

if __name__ == "__main__":
    main()
