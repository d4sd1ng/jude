"""Jude-Kommandozeileneinstieg."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

from core.agent import Agent
from core.model_router import ModelRouter
from core.tool_creator import ToolCreator
from core.tool_registry import ToolRegistry
from dotenv import load_dotenv
from services.confirmations import ConfirmationQueue
from speech.stt import WakeWordListener, listen_active_command, listen_after_wake_word
from speech.tts import speak
from tools import load_all_tools


def build_application() -> tuple[Agent, ToolCreator]:
    load_dotenv(Path(__file__).with_name(".env"))
    registry = ToolRegistry()
    router = ModelRouter()
    confirmations = ConfirmationQueue()
    load_all_tools(registry, router=router, confirmations=confirmations)
    creator = ToolCreator(router, registry)
    registry.register(creator.as_tool())
    return Agent(router, registry), creator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lokaler, modularer KI-Assistent")
    parser.add_argument("--once", metavar="TEXT", help="Eine Eingabe verarbeiten und beenden")
    parser.add_argument("--voice", action="store_true", help="Mikrofon statt Texteingabe verwenden")
    parser.add_argument("--record-seconds", type=float, default=12.0,
                        help="Maximale Befehlsdauer nach dem Aktivierungswort")
    parser.add_argument("--wake-word", default=os.getenv("JUDE_WAKE_PHRASE", "Jude angetreten"),
                        help="Lokales Aktivierungskommando des eigenen Jude-Modells")
    parser.add_argument("--sleep-word", default=os.getenv("JUDE_SLEEP_PHRASE", "Jude Zapfenstreich"),
                        help="Kommando zum Beenden des dauerhaft aktiven Sprachzustands")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--gui", action="store_true", help="Web-GUI starten")
    parser.add_argument("--desktop", action="store_true", help="Desktop-WebView starten")
    parser.add_argument("--host", default=os.getenv("JUDE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("JUDE_PORT", "8765")))
    return parser.parse_args()


def answer(agent: Agent, text: str, voice: bool = False) -> None:
    response = agent.process_input(text)
    label = f"Jude [{agent.last_model}]" if agent.last_model else "Jude"
    print(f"{label}: {response}")
    if voice:
        speak(response)


def _spoken_phrase(value: str) -> str:
    """Vergleicht Sprachkommandos unabhängig von Großschreibung und Satzzeichen."""
    return " ".join(re.findall(r"[\wäöüß]+", value.casefold(), flags=re.UNICODE))


def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"))
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s: %(message)s")
    try:
        if args.desktop or args.gui:
            # Sprachsteuerung läuft im GUI-Betrieb als Hintergrund-Thread der App.
            if args.voice:
                os.environ["JUDE_VOICE"] = "1"
            os.environ["JUDE_WAKE_PHRASE"] = args.wake_word
            os.environ["JUDE_SLEEP_PHRASE"] = args.sleep_word
        if args.desktop:
            from web.desktop import run_desktop
            run_desktop(args.host, args.port)
            return 0
        if args.gui:
            import uvicorn
            uvicorn.run("web.app:app", host=args.host, port=args.port, reload=False)
            return 0
        agent, creator = build_application()
        if args.once:
            answer(agent, args.once, args.voice)
            return 0
        print("Jude bereit. Befehle: /status, /show-tool TOKEN, /approve-tool TOKEN, /quit")
        wake_listener = WakeWordListener() if args.voice else None
        if wake_listener:
            print(f"Sprachsteuerung bereit. Aktivierung: '{args.wake_word}'. Schlafmodus: '{args.sleep_word}'.")
        try:
            while True:
                if args.voice:
                    print(f"Warte auf '{args.wake_word}' …")
                    try:
                        text, confidence = listen_after_wake_word(
                            wake_listener, max_command_seconds=max(6.0, args.record_seconds)
                        )
                        print(f"Erkannt ({confidence:.2f}): {text}")
                    except TimeoutError as exc:
                        logging.warning("%s", exc)
                        continue
                    print(f"Jude ist dauerhaft aktiv. Zum Schlafen: '{args.sleep_word}'.")
                    while True:
                        if _spoken_phrase(text) == _spoken_phrase(args.sleep_word):
                            print("Sprachsteuerung im Schlafmodus.")
                            speak("Schlafmodus aktiviert.")
                            break
                        answer(agent, text, True)
                        try:
                            text = listen_active_command(
                                max_command_seconds=max(6.0, args.record_seconds), start_timeout=30.0
                            )
                            print(f"Erkannt: {text}")
                        except TimeoutError:
                            continue
                    continue
                text = input("Du: ").strip()
                if text in {"/quit", "/exit"}:
                    return 0
                if text == "/status":
                    print(agent.router.status())
                    continue
                if text.startswith("/approve-tool "):
                    print(creator.approve(text.split(maxsplit=1)[1]))
                    continue
                if text.startswith("/show-tool "):
                    print(creator.show(text.split(maxsplit=1)[1]))
                    continue
                answer(agent, text, args.voice)
        finally:
            if wake_listener:
                wake_listener.close()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    except Exception as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
