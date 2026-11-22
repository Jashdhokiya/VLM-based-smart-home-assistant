#!/usr/bin/env python3
"""
INOT Mark1 — Microphone Helper

Lists audio devices and optionally records a 3-second test clip.

Usage:
  python scripts/mic_helper.py               # list devices
  python scripts/mic_helper.py --test         # record 3s test clip
  python scripts/mic_helper.py --test -o out.wav
"""

import argparse
import sys
import wave


def list_devices():
    """List all audio input devices."""
    try:
        import sounddevice as sd
        print("Available audio devices:\n")
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            direction = ""
            if d['max_input_channels'] > 0:
                direction += "IN "
            if d['max_output_channels'] > 0:
                direction += "OUT"
            marker = " ◄ default input" if i == sd.default.device[0] else ""
            print(f"  [{i:2d}] {d['name']:<40s}  {direction:6s}  "
                  f"ch={d['max_input_channels']}/{d['max_output_channels']}{marker}")

        input_devs = [d for d in devices if d['max_input_channels'] > 0]
        print(f"\n  Total input devices: {len(input_devs)}")
        if not input_devs:
            print("\n  ⚠  No input devices found. Connect a USB microphone.")
    except ImportError:
        print("sounddevice is not installed. Run: pip install sounddevice")
        sys.exit(1)
    except Exception as e:
        print(f"Error listing devices: {e}")
        sys.exit(1)


def record_test(output: str = "test_recording.wav", duration: float = 3.0):
    """Record a short test clip."""
    try:
        import sounddevice as sd
        import numpy as np

        rate = 16000
        channels = 1
        print(f"Recording {duration}s at {rate}Hz mono → {output}")
        print("Speak now...")
        audio = sd.rec(int(rate * duration), samplerate=rate, channels=channels, dtype='int16')
        sd.wait()
        print("Done!")

        with wave.open(output, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(audio.tobytes())

        # Basic stats
        rms = (sum(int(s)**2 for s in audio.flatten()) / len(audio.flatten())) ** 0.5
        print(f"  Saved: {output}  ({len(audio)} samples, RMS={rms:.0f})")
        if rms < 100:
            print("  ⚠  Audio is very quiet. Check mic gain or distance.")
    except ImportError:
        print("sounddevice is not installed. Run: pip install sounddevice")
        sys.exit(1)
    except Exception as e:
        print(f"Recording failed: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="INOT Mark1 Mic Helper")
    parser.add_argument("--test", action="store_true", help="Record a 3s test clip")
    parser.add_argument("-o", "--output", default="test_recording.wav", help="Output WAV file")
    parser.add_argument("-d", "--duration", type=float, default=3.0, help="Recording duration (s)")
    args = parser.parse_args()

    list_devices()

    if args.test:
        print()
        record_test(output=args.output, duration=args.duration)


if __name__ == "__main__":
    main()

