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


import re

def _phrase(value: str) -> str:
    from speech.stt import _spoken_phrase
    return _spoken_phrase(value)


_FEEDBACK_POSITIVE = re.compile(
    r"\b(das war (sehr )?gut|gut gemacht|perfekt|passt( genau)?|sehr gut|richtig gut|"
    r"das war (richtig|hilfreich|super|klasse|top)|top|danke das (war|hat) geholfen)\b",
    re.IGNORECASE,
)
_FEEDBACK_NEGATIVE = re.compile(
    r"\b(das war (schlecht|falsch|nicht gut|unbrauchbar|mist|daneben|nutzlos)|"
    r"unzureichend|das stimmt (so )?nicht|das war (kompletter )?quatsch)\b",
    re.IGNORECASE,
)


class VoiceController:
    """Steuert genau einen Sprach-Thread; alle öffentlichen Methoden sind threadsicher."""

    def __init__(self, agent, agent_lock: threading.Lock,
                 wake_word: str | None = None, sleep_word: str | None = None,
                 record_seconds: float = 12.0, briefing=None):
        self.agent = agent
        self.agent_lock = agent_lock
        self.briefing = briefing
        self.briefing_enabled = os.getenv("JUDE_BRIEFING", "true").strip().lower() in {
            "1", "true", "an", "on", "ja"}
        self.wake_word = wake_word or os.getenv("JUDE_WAKE_PHRASE", "Jude angetreten")
        self.sleep_word = sleep_word or os.getenv("JUDE_SLEEP_PHRASE", "Jude Zapfenstreich")
        self.record_seconds = max(6.0, float(record_seconds))
        # Passend zu Weck- und Schlafwort ("angetreten" / "Zapfenstreich"):
        # knappe Meldung statt Small Talk. Beides in der GUI überschreibbar.
        self.greeting = os.getenv("JUDE_GREETING", "Zu Befehl.")
        self.farewell = os.getenv("JUDE_FAREWELL", "Zapfenstreich.")
        self._events: deque[dict] = deque(maxlen=200)
        self._ids = itertools.count(1)
        self._state = "aus"
        self._error: str | None = None
        self._stop = threading.Event()
        self._skip = threading.Event()        # aktuellen Abschnitt überspringen
        self._skip_all = threading.Event()    # restliches Briefing überspringen
        self._thread: threading.Thread | None = None
        self._guard = threading.Lock()
        self._tts_warned = False
        from core.paths import DATA_DIR
        self._brief_state_path = DATA_DIR / "voice_briefing.json"

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

    def set_phrases(self, wake_word: str | None = None, sleep_word: str | None = None,
                    greeting: str | None = None, farewell: str | None = None) -> dict:
        """Aktivierungs- und Schlafwort zur Laufzeit ändern (GUI-Feld).

        Mit der Whisper-Erkennung genügt der reine Text – kein Training nötig.
        Der Sprach-Thread wird neu gestartet, damit er die neue Phrase hört.
        """
        if wake_word and wake_word.strip():
            self.wake_word = wake_word.strip()
            os.environ["JUDE_WAKE_PHRASE"] = self.wake_word
        if sleep_word and sleep_word.strip():
            self.sleep_word = sleep_word.strip()
            os.environ["JUDE_SLEEP_PHRASE"] = self.sleep_word
        if greeting is not None and greeting.strip():
            self.greeting = greeting.strip()
            os.environ["JUDE_GREETING"] = self.greeting
        if farewell is not None and farewell.strip():
            self.farewell = farewell.strip()
            os.environ["JUDE_FAREWELL"] = self.farewell
        if self._thread and self._thread.is_alive():
            self.stop()
            self.start()
        return self.status()

    def status(self) -> dict:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "state": self._state,
            "wake_word": self.wake_word,
            "sleep_word": self.sleep_word,
            "greeting": self.greeting,
            "farewell": self.farewell,
            "error": self._error,
        }

    def events(self, since: int = 0) -> dict:
        snapshot = list(self._events)
        items = [e for e in snapshot if e["id"] > since]
        last_id = items[-1]["id"] if items else since
        return {"events": items, "last_id": last_id, **self.status()}

    def skip(self) -> dict:
        """Überspringt den gerade gesprochenen Abschnitt und geht zum nächsten Thema."""
        self._skip.set()
        return self.status()

    def skip_all(self) -> dict:
        """Bricht das restliche Briefing ab."""
        self._skip_all.set()
        self._skip.set()
        return self.status()

    # ------------------------------------------------------------ intern

    def _emit(self, kind: str, text: str, **extra) -> None:
        self._events.append({"id": next(self._ids), "kind": kind, "text": text,
                             "ts": time.time(), **extra})

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self._emit("state", state)

    def _speak(self, text: str, interruptible: bool = False) -> None:
        from speech.tts import speak
        should_stop = None
        if interruptible:
            should_stop = lambda: self._skip.is_set() or self._skip_all.is_set() or self._stop.is_set()
        try:
            speak(text, should_stop=should_stop)
        except Exception as exc:
            if not self._tts_warned:
                self._tts_warned = True
                self._emit("warning", f"Sprachausgabe nicht verfügbar: {exc}")
            logger.warning("Sprachausgabe fehlgeschlagen: %s", exc)

    # -- Einmal-täglich-Zustand des vollständigen Briefings -----------------

    def _today(self) -> str:
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d")

    def _full_brief_done_today(self) -> bool:
        import json
        try:
            state = json.loads(self._brief_state_path.read_text(encoding="utf-8"))
            return state.get("last_full_brief") == self._today()
        except Exception:
            return False

    def _mark_full_brief(self) -> None:
        import json
        try:
            self._brief_state_path.parent.mkdir(parents=True, exist_ok=True)
            self._brief_state_path.write_text(json.dumps({"last_full_brief": self._today()}), encoding="utf-8")
        except Exception as exc:
            logger.warning("Briefing-Zustand nicht speicherbar: %s", exc)

    def _speak_briefing(self) -> None:
        if not self.briefing_enabled:
            return
        full = not self._full_brief_done_today()
        try:
            if self.briefing is None:
                from services.briefing import BriefingService
                self.briefing = BriefingService()
            segments = self.briefing.segments(full=full)
        except Exception as exc:
            self._emit("warning", f"Briefing nicht verfügbar: {exc}")
            return
        if not segments:
            return
        self._skip.clear()
        self._skip_all.clear()
        for segment in segments:
            if self._stop.is_set() or self._skip_all.is_set():
                break
            self._skip.clear()
            self._emit("answer", segment["text"], kind_detail="briefing", topic=segment.get("topic"))
            self._set_state("spricht")
            self._speak(segment["text"], interruptible=True)
        self._skip.clear()
        self._skip_all.clear()
        if full:
            self._mark_full_brief()

    def _maybe_feedback(self, text: str) -> bool:
        """Erkennt gesprochenes Lob/Kritik und bewertet die letzte Routing-Entscheidung."""
        positive = bool(_FEEDBACK_POSITIVE.search(text))
        negative = bool(_FEEDBACK_NEGATIVE.search(text))
        if not (positive or negative) or (positive and negative):
            return False
        route_id = getattr(self.agent, "last_route_id", None)
        self._emit("heard", text)
        if not route_id:
            self._speak("Dazu gibt es noch keine Antwort zum Bewerten.")
            return True
        value = 1 if positive else -1
        try:
            self.agent.router.feedback(route_id, value)
        except Exception as exc:
            logger.warning("Sprach-Feedback fehlgeschlagen: %s", exc)
            self._speak("Das Feedback konnte ich nicht speichern.")
            return True
        ack = "Danke, ich habe es als gut vermerkt." if positive else "Verstanden, ich vermerke es als unzureichend."
        self._emit("answer", ack, feedback=value, route_id=route_id)
        self._speak(ack)
        return True

    def _respond(self, text: str) -> None:
        self._set_state("denkt")
        self._emit("heard", text)
        try:
            with self.agent_lock:
                answer = self.agent.process_input(text)
        except Exception as exc:
            logger.warning("Verarbeitung des Sprachbefehls fehlgeschlagen: %s", exc)
            self._emit("error", f"Antwort konnte nicht erzeugt werden: {exc}")
            self._set_state("aktiv")
            return
        self._emit("answer", answer, model=self.agent.last_model)
        self._set_state("spricht")
        # Auch normale Antworten müssen abbrechbar sein (Ansage Tino) – vorher
        # ließ sich nur das Briefing überspringen, lange Antworten liefen durch.
        self._skip.clear()
        self._speak(answer, interruptible=True)
        self._skip.clear()

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
                # Freundliche Begrüßung beim Aufwachen statt das Wake-Wort als Befehl zu deuten.
                self._emit("answer", self.greeting)
                self._set_state("spricht")
                self._speak(self.greeting)
                self._speak_briefing()
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
                        self._emit("answer", self.farewell)
                        self._speak(self.farewell)
                        break
                    if self._maybe_feedback(text):
                        continue
                    self._respond(text)
        finally:
            listener.close()
            self._set_state("aus")
