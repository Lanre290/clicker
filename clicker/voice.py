"""Voice in and out. Input: record until the user stops talking, transcribe
with a dedicated free STT engine — not Gemini, so voice capture spends no
API tokens/quota. "Auto-stop on silence" comes from SpeechRecognition's own
pause_threshold, not anything hand-rolled here. Output: pyttsx3, which
drives the OS's own offline SAPI5 voices on Windows — same "no API
tokens/quota" reasoning as the STT side.
"""

import speech_recognition as sr
import pyttsx3

from . import config


class VoiceError(Exception):
    pass


_recognizer = sr.Recognizer()
_recognizer.pause_threshold = config.VOICE_PAUSE_THRESHOLD_SECONDS


def listen_once():
    """Blocks the calling thread. Records from the default microphone until
    speech starts and then stops (silence-terminated), and returns the
    transcribed text. Raises VoiceError on timeout / no speech / STT failure.
    """
    try:
        with sr.Microphone() as source:
            _recognizer.adjust_for_ambient_noise(source, duration=0.3)
            audio = _recognizer.listen(
                source,
                timeout=config.VOICE_LISTEN_TIMEOUT_SECONDS,
                phrase_time_limit=config.VOICE_PHRASE_TIME_LIMIT_SECONDS,
            )
    except sr.WaitTimeoutError as e:
        raise VoiceError("Didn't hear anything.") from e
    except OSError as e:
        raise VoiceError(f"Couldn't access the microphone: {e}") from e

    try:
        return _recognizer.recognize_google(audio).strip()
    except sr.UnknownValueError as e:
        raise VoiceError("Couldn't make out what was said.") from e
    except sr.RequestError as e:
        raise VoiceError(f"Speech recognition service error: {e}") from e


def speak(text):
    """Speaks `text` aloud, blocking the calling thread until done. Builds a
    fresh engine per call instead of keeping one global instance — pyttsx3's
    SAPI5 driver is unreliable when reused across calls/threads (a widely
    reported gotcha: runAndWait() silently doing nothing the second time),
    and each caller here already runs on its own short-lived background
    thread, so the extra init cost is a non-issue.

    Feedback-channel semantics like sound.py's beeps: failures are logged,
    not raised, so a missing/broken TTS driver never breaks the guide cycle
    that's already been shown on screen.
    """
    if not text:
        return
    try:
        engine = pyttsx3.init()
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception as e:
        print(f"[clicker] Text-to-speech failed: {e}")
