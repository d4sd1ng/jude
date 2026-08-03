"""Hintergrund-Sprachsteuerung für GUI- und Desktop-Betrieb.

Kapselt den Wake-Word→Befehl→Antwort-Kreislauf aus dem CLI-Sprachmodus als
stoppbaren Thread, damit Web-GUI und Desktop-Fenster dieselbe Sprachsteuerung
nutzen können. Ereignisse (gehörte Befehle, Antworten, Zustandswechsel) werden
in einem Ringpuffer gesammelt, den die GUI per Polling abholt.
"""

from __future__ import annotations

import itertools
import logging
import os
import threading
import time
from collections import deque

logger = logging.getLogger(__name__)


def _phrase(value: str) -> str:
    from speech.stt import _spoken_phrase
    return _spoken_phrase(value)


class VoiceController:
    """Steuert genau einen Sprach-Thread; alle öffentlichen Methoden sind threadsicher."""

    def __init__(self, agent, agent_lock: threading.Lock,
                 wake_word: str | None = None, sleep_word: str | None = None,
                 record_seconds: float = 12.0):
        self.agent = agent
        self.agent_lock = agent_lock
        self.wake_word = wake_word or os.getenv("JUDE_WAKE_PHRASE", "Jude angetreten")
        self.sleep_word = sleep_word or os.getenv("JUDE_SLEEP_PHRASE", "Jude Zapfenstreich")
        self.record_seconds = max(6.0, float(record_seconds))
        self._events: deque[dict] = deque(maxlen=200)
        self._ids = itertools.count(1)
        self._state = "aus"
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._guard = threading.Lock()
        self._tts_warned = False

    # ------------------------------------------------------------------ API

    def start(self) -> dict:
        with self._guard:
            if self._thread and self._thread.is_alive():
                return self.status()
            self._stop.clear()
            self._error = None
            self._thread = threading.Thread(target=self._run, name="jude-voice", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict:
        with self._guard:
            thread = self._thread
            self._stop.set()
        if thread and thread.is_alive():
            thread.join(timeout=20.0)
        self._set_state("aus")
        return self.status()

    def status(self) -> dict:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "state": self._state,
            "wake_word": self.wake_word,
            "sleep_word": self.sleep_word,
            "error": self._error,
        }

    def events(self, since: int = 0) -> dict:
        items = [e for e in self._events if e["id"] > since]
        last_id = items[-1]["id"] if items else since
        return {"events": items, "last_id": last_id, **self.status()}

    # ------------------------------------------------------------ intern

    def _emit(self, kind: str, text: str, **extra) -> None:
        self._events.append({"id": next(self._ids), "kind": kind, "text": text,
                             "ts": time.time(), **extra})

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self._emit("state", state)

    def _speak(self, text: str) -> None:
        from speech.tts import speak
        try:
            speak(text)
        except Exception as exc:
            if not self._tts_warned:
                self._tts_warned = True
                self._emit("warning", f"Sprachausgabe nicht verfügbar: {exc}")
            logger.warning("Sprachausgabe fehlgeschlagen: %s", exc)

    def _respond(self, text: str) -> None:
        self._set_state("denkt")
        self._emit("heard", text)
        with self.agent_lock:
            answer = self.agent.process_input(text)
        self._emit("answer", answer, model=self.agent.last_model)
        self._set_state("spricht")
        self._speak(answer)

    def _run(self) -> None:
        from speech.stt import (
            WakeWordListener,
            _ready_tone,
            record_until_silence,
            transcribe,
        )
        try:
            listener = WakeWordListener()
        except Exception as exc:
            self._error = str(exc)
            self._emit("error", f"Sprachsteuerung konnte nicht starten: {exc}")
            self._set_state("fehler")
            return
        try:
            while not self._stop.is_set():
                self._set_state("wartet")
                try:
                    # Begrenzte Wartefenster halten den Thread stoppbar; lang genug,
                    # damit die Erkennungspuffer nicht mitten in der Phrase zurückgesetzt werden.
                    listener.wait(timeout=10.0)
                except TimeoutError:
                    continue
                except Exception as exc:
                    self._error = str(exc)
                    self._emit("error", f"Wake-Word-Fehler: {exc}")
                    self._set_state("fehler")
                    return
                _ready_tone()
                self._set_state("aufnahme")
                try:
                    text = transcribe(record_until_silence(max_seconds=self.record_seconds))
                except TimeoutError as exc:
                    self._emit("warning", str(exc))
                    continue
                if not text:
                    self._emit("warning", "Whisper konnte keinen Sprachbefehl transkribieren.")
                    continue
                self._respond(text)
                # Dauerhaft aktiver Zustand bis Schlafwort oder Stille.
                while not self._stop.is_set():
                    self._set_state("aktiv")
                    try:
                        text = transcribe(record_until_silence(max_seconds=self.record_seconds,
                                                               start_timeout=30.0))
                    except TimeoutError:
                        self._emit("state", "schläft nach Stille")
                        break
                    if not text:
                        continue
                    if _phrase(text) == _phrase(self.sleep_word):
                        self._emit("heard", text)
                        self._emit("answer", "Schlafmodus aktiviert.")
                        self._speak("Schlafmodus aktiviert.")
                        break
                    self._respond(text)
        finally:
            listener.close()
            self._set_state("aus")
