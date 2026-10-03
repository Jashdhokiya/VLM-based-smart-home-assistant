import os
import sys
import cv2
import numpy as np
from dotenv import load_dotenv

load_dotenv()

def capture_scene(output_path: str = None) -> str:
    """
    Captures a single scene frame from the webcam with auto-exposure warmup.
    Saves to images/current_scene.jpg (or output_path) and returns the file path.
    """
    if output_path is None:
        output_path = os.getenv("IMAGE_PATH", "images/current_scene.jpg")

    cam_index = int(os.getenv("CAMERA_INDEX", "0"))
    width = int(os.getenv("CAMERA_WIDTH", "1280"))
    height = int(os.getenv("CAMERA_HEIGHT", "720"))
    warmup_frames = int(os.getenv("CAMERA_WARMUP_FRAMES", "20"))

    # Use DirectShow backend on Windows
    backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY

    cap = cv2.VideoCapture(cam_index, backend)
    try:
        if not cap.isOpened():
            raise RuntimeError(
                f"[CAMERA] Cannot open camera at index {cam_index}. "
                "Ensure webcam is connected, plugged in, and not in use by another application."
            )

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        print(f"[CAMERA] Warming up camera ({warmup_frames} frames)...")
        # Discard warmup frames to allow auto-exposure / white-balance to settle
        for _ in range(warmup_frames):
            ret, _ = cap.read()
            if not ret:
                break

        ret, frame = cap.read()
        if not ret or frame is None or frame.size == 0:
            raise RuntimeError(
                "[CAMERA] Failed to capture frame from webcam. Frame buffer was empty."
            )

        # Check for completely black image (closed shutter or pitch dark room)
        mean_brightness = float(np.mean(frame))
        if mean_brightness < 5.0:
            raise RuntimeError(
                f"[CAMERA] Captured frame is excessively dark/black (mean brightness: {mean_brightness:.1f}/255). "
                "Please verify your webcam privacy shutter is open and room lighting is sufficient."
            )

        # Ensure target directory exists
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        success = cv2.imwrite(output_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not success:
            raise IOError(f"[CAMERA] Failed to write captured image to '{output_path}'.")

        print(f"[CAMERA] Saved {output_path} (mean brightness: {mean_brightness:.1f})")
        return output_path

    finally:
        cap.release()

if __name__ == "__main__":
    try:
        saved_path = capture_scene()
        print(f"Captured scene saved to: {saved_path}")
    except Exception as e:
        print(f"Camera capture error: {e}", file=sys.stderr)
        sys.exit(1)
