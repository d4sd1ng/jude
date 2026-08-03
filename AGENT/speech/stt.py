"""Lokales Wake-Word, sprachaktivitätsgesteuerte Aufnahme und Whisper-STT."""

from __future__ import annotations

import os
import re
import time
from collections import deque
from functools import lru_cache

import numpy as np

from speech.wakeword import JudeWakeWordModel

SAMPLE_RATE = 16_000
WAKE_BLOCK = 1_280  # 80 ms, Vorgabe des Wake-Word-Modells


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel
    default = "/media/d4sd1ng/AI-Data/Jude/models/whisper-small"
    configured = os.getenv("WHISPER_MODEL", default)
    if configured == default and not os.path.isfile(os.path.join(default, "model.bin")):
        raise RuntimeError("Lokales Whisper-Modell fehlt oder ist unvollständig: " + default)
    return WhisperModel(configured, device=os.getenv("WHISPER_DEVICE", "cpu"), compute_type="int8")


def transcribe(audio: np.ndarray) -> str:
    segments, _ = _model().transcribe(audio.astype(np.float32) / 32768.0,
                                      language=os.getenv("WHISPER_LANGUAGE") or "de",
                                      vad_filter=True, beam_size=3,
                                      condition_on_previous_text=False)
    return " ".join(segment.text.strip() for segment in segments).strip()


def listen(seconds: float = 5.0, sample_rate: int = SAMPLE_RATE) -> str:
    """Kompatibler fester Aufnahmeweg ohne Wake-Word."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError("Sprachmodus benötigt faster-whisper, numpy und sounddevice.") from exc
    audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    return transcribe(np.ravel(audio))


class WakeWordListener:
    def __init__(self, threshold: float | None = None):
        try:
            from pyopen_wakeword import OpenWakeWordFeatures
        except ImportError as exc:
            raise RuntimeError("Wake-Word-Modus benötigt pyopen-wakeword.") from exc
        self.detector = JudeWakeWordModel()
        self.features = OpenWakeWordFeatures.from_builtin()
        self.threshold = threshold if threshold is not None else self.detector.threshold
        self.trigger_frames = max(1, int(os.getenv("WAKE_WORD_TRIGGER_FRAMES", "2")))

    def wait(self, timeout: float | None = None) -> float:
        import sounddevice as sd
        started, consecutive, peak = time.monotonic(), 0, 0.0
        features_ready = False
        recent_audio: deque[bytes] = deque(maxlen=int(4.0 * SAMPLE_RATE / WAKE_BLOCK))
        expected = _spoken_phrase(os.getenv("JUDE_WAKE_PHRASE", "Jude angetreten"))
        self.detector.reset()
        self.features.reset()
        with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=WAKE_BLOCK) as stream:
            while timeout is None or time.monotonic() - started < timeout:
                block, overflowed = stream.read(WAKE_BLOCK)
                if overflowed:
                    continue
                recent_audio.append(bytes(block))
                generated = list(self.features.process_streaming(bytes(block)))
                if not features_ready:
                    # reset() erzeugt zunächst den internen Null-Autofill.
                    features_ready = True
                    continue
                for features in generated:
                    for probability in self.detector.process_streaming(features):
                        peak = max(peak, float(probability))
                        consecutive = consecutive + 1 if probability >= self.threshold else 0
                        if consecutive >= self.trigger_frames:
                            # Der Klassifikator ist nur der stromsparende Vorfilter. Whisper
                            # bestätigt lokal die vollständige seltene Phrase, bevor Jude aufwacht.
                            heard = _spoken_phrase(transcribe(np.frombuffer(b"".join(recent_audio), dtype=np.int16)))
                            if expected in heard:
                                return peak
                            consecutive, peak = 0, 0.0
                            self.detector.reset()
        raise TimeoutError("Aktivierungswort nicht erkannt.")

    def close(self) -> None:
        self.detector.close()
        self.features.close()


def _ready_tone() -> None:
    """Kurzer lokaler Ton bestätigt die Aktivierung; Fehler verhindern keine Aufnahme."""
    try:
        import sounddevice as sd
        samples = np.arange(int(SAMPLE_RATE * 0.09))
        tone = (0.12 * np.sin(2 * np.pi * 880 * samples / SAMPLE_RATE)).astype(np.float32)
        sd.play(tone, SAMPLE_RATE, blocking=True)
    except Exception:
        print("\a", end="", flush=True)


def _spoken_phrase(value: str) -> str:
    return " ".join(re.findall(r"[\wäöüß]+", value.casefold(), flags=re.UNICODE))


def record_until_silence(max_seconds: float = 12.0, start_timeout: float = 5.0,
                         silence_seconds: float = 0.9) -> np.ndarray:
    import sounddevice as sd
    blocksize = 800  # 50 ms
    calibration, speech, blocks = [], False, []
    silent_blocks = 0
    started = time.monotonic()
    speech_started: float | None = None
    with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=blocksize) as stream:
        while True:
            now = time.monotonic()
            if speech_started is not None and now - speech_started >= max_seconds:
                break
            if speech_started is None and now - started >= start_timeout:
                raise TimeoutError("Nach der Aktivierung wurde kein Befehl erkannt.")
            raw, overflowed = stream.read(blocksize)
            if overflowed:
                continue
            block = np.frombuffer(raw, dtype=np.int16).copy()
            rms = float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))
            if len(calibration) < 8:
                calibration.append(rms)
                continue
            threshold = max(float(os.getenv("VOICE_ENERGY_THRESHOLD", "180")), float(np.median(calibration)) * 2.7)
            if rms >= threshold:
                speech, silent_blocks = True, 0
                speech_started = speech_started or time.monotonic()
                blocks.append(block)
            elif speech:
                blocks.append(block)
                silent_blocks += 1
                if silent_blocks * blocksize / SAMPLE_RATE >= silence_seconds:
                    break
    if not speech or not blocks:
        raise TimeoutError("Kein Sprachbefehl erkannt.")
    return np.concatenate(blocks)


def listen_after_wake_word(listener: WakeWordListener, max_command_seconds: float = 12.0) -> tuple[str, float]:
    confidence = listener.wait()
    _ready_tone()
    command = transcribe(record_until_silence(max_seconds=max_command_seconds))
    if not command:
        raise RuntimeError("Whisper konnte keinen Sprachbefehl transkribieren.")
    return command, confidence


def listen_active_command(max_command_seconds: float = 12.0, start_timeout: float = 30.0) -> str:
    """Nimmt im dauerhaft aktiven Zustand den nächsten Befehl ohne neues Wake Word auf."""
    command = transcribe(record_until_silence(max_seconds=max_command_seconds,
                                              start_timeout=start_timeout))
    if not command:
        raise RuntimeError("Whisper konnte keinen Sprachbefehl transkribieren.")
    return command
