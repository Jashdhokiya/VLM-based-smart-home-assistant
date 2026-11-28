"""
INOT Mark1 — Speech-to-Text backend abstraction

Supported backends (selected via ``STT_BACKEND`` env var):
  - ``vosk``           : Offline, small English model, arm64-compatible
  - ``faster_whisper`` : Offline, tiny.en int8, arm64-compatible
  - ``groq``           : Cloud Whisper API (existing default)

All backends expose ``transcribe_file(wav_path) -> str``.
Live mic recording is optional and handled separately.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Optional

from src.core.config import settings
from src.core import metrics

# ---------------------------------------------------------------------------
# Lazy-loaded backend singletons (save RAM on Pi)
# ---------------------------------------------------------------------------
_vosk_model = None
_fw_model = None


def _load_vosk():
    """Lazy-load Vosk model."""
    global _vosk_model
    if _vosk_model is not None:
        return _vosk_model

    import vosk  # type: ignore[import-untyped]

    model_path = settings.vosk_model_path
    if not model_path:
        # Auto-detect: look for vosk-model-small-en-us-* next to repo
        base = settings.base_dir
        candidates = sorted(base.glob("vosk-model-small-en*"))
        if candidates:
            model_path = str(candidates[0])
        else:
            model_path = str(base / "vosk-model-small-en-us-0.15")

    if not Path(model_path).is_dir():
        raise FileNotFoundError(
            f"Vosk model directory not found: {model_path}\n"
            f"Download from https://alphacephei.com/vosk/models and extract here."
        )

    vosk.SetLogLevel(-1)
    _vosk_model = vosk.Model(model_path)
    return _vosk_model


def _ensure_mono_wav(audio_path: str) -> tuple[str, bool]:
    """Return a 16-bit mono WAV path, converting via ffmpeg if needed."""
    try:
        with wave.open(audio_path, "rb") as wf:
            if wf.getnchannels() == 1 and wf.getsampwidth() == 2:
                return audio_path, False
    except Exception:
        pass

    fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-i", audio_path,
                "-ar", str(settings.audio_rate),
                "-ac", "1",
                "-c:a", "pcm_s16le",
                tmp_wav,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg conversion failed: {proc.stderr.strip()[:200]}")
        return tmp_wav, True
    except Exception:
        if os.path.exists(tmp_wav):
            os.remove(tmp_wav)
        raise


def _transcribe_vosk(wav_path: str) -> str:
    """Transcribe audio (WAV, webm, etc.) using Vosk offline recognizer."""
    import json as _json
    import vosk  # type: ignore[import-untyped]

    model = _load_vosk()
    target_wav, is_temp = _ensure_mono_wav(wav_path)
    wf = None
    try:
        wf = wave.open(target_wav, "rb")
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("Vosk requires mono 16-bit WAV")

        rec = vosk.KaldiRecognizer(model, wf.getframerate())
        rec.SetWords(False)

        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            rec.AcceptWaveform(data)

        result = _json.loads(rec.FinalResult())
        return result.get("text", "").strip()
    finally:
        if wf is not None:
            wf.close()
        if is_temp and os.path.exists(target_wav):
            try:
                os.remove(target_wav)
            except Exception:
                pass


def _load_faster_whisper():
    """Lazy-load faster-whisper model."""
    global _fw_model
    if _fw_model is not None:
        return _fw_model

    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    _fw_model = WhisperModel(
        settings.faster_whisper_model,
        device="cpu",
        compute_type=settings.faster_whisper_compute,
    )
    return _fw_model


def _transcribe_faster_whisper(wav_path: str) -> str:
    """Transcribe WAV using faster-whisper offline."""
    model = _load_faster_whisper()
    segments, _info = model.transcribe(
        wav_path,
        language="en",
        beam_size=1,
        best_of=1,
        temperature=0.0,
        vad_filter=True,
    )
    return " ".join(seg.text.strip() for seg in segments).strip()


def _transcribe_groq(wav_path: str) -> str:
    """Transcribe WAV using Groq Whisper cloud API."""
    from groq import Groq  # type: ignore[import-untyped]

    api_key = settings.groq_api_key
    if not api_key or api_key == "...":
        raise ValueError("GROQ_API_KEY is not set — cannot use Groq STT backend")

    client = Groq(api_key=api_key)
    with open(wav_path, "rb") as f:
        transcription = client.audio.transcriptions.create(
            file=f,
            model=settings.groq_whisper_model,
            prompt="Smart home voice command. Translate to English if needed.",
            response_format="verbose_json",
            language="en",
            temperature=0.0,
        )
    return transcription.text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_BACKENDS = {
    "vosk": _transcribe_vosk,
    "faster_whisper": _transcribe_faster_whisper,
    "groq": _transcribe_groq,
}


def transcribe_file(wav_path: str, backend: Optional[str] = None) -> str:
    """
    Transcribe a WAV file using the configured (or specified) backend.

    Returns the transcribed text, or empty string on silence.
    """
    be = (backend or settings.stt_backend).lower()
    fn = _BACKENDS.get(be)
    if fn is None:
        raise ValueError(f"Unknown STT backend '{be}'. Choose from {list(_BACKENDS)}")

    with metrics.stage("stt", note=f"backend={be}"):
        text = fn(wav_path)

    print(f"[STT] ({be}) \"{text}\"")
    return text


def record_audio(output_path: str = "real_time_audio.wav") -> str:
    """
    Record audio from the microphone.  Returns the WAV file path.
    Tries sounddevice first, then pyaudio.  Raises RuntimeError if
    no audio device is available.
    """
    rate = settings.audio_rate
    channels = settings.audio_channels
    duration = settings.audio_record_seconds

    # Try sounddevice first (lighter, works well on arm64)
    try:
        import sounddevice as sd  # type: ignore[import-untyped]

        device = settings.audio_device
        print(f"[STT] Recording {duration}s from mic (sounddevice)...")
        audio_data = sd.rec(
            int(rate * duration),
            samplerate=rate,
            channels=channels,
            dtype="int16",
            device=device,
        )
        sd.wait()
        print("[STT] Recording complete.")

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(audio_data.tobytes())
        return output_path
    except Exception:
        pass

    # Fallback: pyaudio
    try:
        import pyaudio  # type: ignore[import-untyped]

        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            frames_per_buffer=1024,
        )
        print(f"[STT] Recording {duration}s from mic (pyaudio)...")
        frames = []
        for _ in range(int(rate / 1024 * duration)):
            frames.append(stream.read(1024))
        print("[STT] Recording complete.")
        stream.stop_stream()
        stream.close()
        audio.terminate()

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(audio.get_sample_size(pyaudio.paInt16))
            wf.setframerate(rate)
            wf.writeframes(b"".join(frames))
        return output_path
    except Exception as e:
        raise RuntimeError(
            f"No audio device available. Connect a USB mic or use --wav <file>.\n"
            f"Details: {e}"
        ) from e

