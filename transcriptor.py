import os
import wave
from dotenv import load_dotenv
from groq import Groq
import metrics

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Audio settings
CHANNELS = 1
RATE = 16000  # Whisper prefers 16kHz audio
CHUNK = 1024
RECORD_SECONDS = 5
WAVE_OUTPUT_FILENAME = "real_time_audio.wav"

def _record_with_pyaudio():
    import pyaudio
    audio = pyaudio.PyAudio()
    stream = audio.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK
    )
    print("Recording... Speak now!")
    frames = []
    for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
        data = stream.read(CHUNK)
        frames.append(data)
    print("Recording complete!")

    stream.stop_stream()
    stream.close()
    audio.terminate()

    with wave.open(WAVE_OUTPUT_FILENAME, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(audio.get_sample_size(pyaudio.paInt16))
        wf.setframerate(RATE)
        wf.writeframes(b"".join(frames))
    return WAVE_OUTPUT_FILENAME

def _record_with_sounddevice():
    import sounddevice as sd
    print("Recording... Speak now!")
    audio_data = sd.rec(int(RATE * RECORD_SECONDS), samplerate=RATE, channels=CHANNELS, dtype="int16")
    sd.wait()
    print("Recording complete!")

    with wave.open(WAVE_OUTPUT_FILENAME, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(RATE)
        wf.writeframes(audio_data.tobytes())
    return WAVE_OUTPUT_FILENAME

def record_audio():
    """Records audio from the microphone and saves it as a WAV file."""
    try:
        return _record_with_pyaudio()
    except (ImportError, Exception):
        try:
            return _record_with_sounddevice()
        except Exception as e:
            if os.path.exists(WAVE_OUTPUT_FILENAME):
                print(f"[TRANSCRIPTION] [WARN] Microphone recording failed ({e}). Reusing existing {WAVE_OUTPUT_FILENAME}.")
                return WAVE_OUTPUT_FILENAME
            raise RuntimeError(f"Microphone recording failed and no fallback audio found: {e}") from e

def transcribe_file(audio_file_path: str) -> str:
    """Transcribes a given audio file path using Groq Whisper API."""
    api_key = os.getenv("GROQ_API_KEY")
    whisper_model = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3")
    client = Groq(api_key=api_key)

    with open(audio_file_path, "rb") as file:
        with metrics.stage("stt_api"):
            transcription = client.audio.transcriptions.create(
                file=file,
                model=whisper_model,
                prompt="Specify context or spelling. this usually contains the count of electronic devices. Translate the audio to english if needed.",
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
                language="en",
                temperature=0.0
            )

    print(f"[TRANSCRIPTION] USER: {transcription.text}")
    return transcription.text

def transcribe():
    audio_file = record_audio()
    return transcribe_file(audio_file)