#!/usr/bin/env python3
"""Zerlegt eine lange Aufnahme in einzelne Clips für das Wake-Word-Training.

Zwei Modi:
- ``silence`` (Standard): schneidet an Sprechpausen – für Positiv-Aufnahmen, bei
  denen die Phrase mehrfach mit ~1 s Pause gesprochen wurde.
- ``fixed``: feste Fensterlänge – praktisch, um allgemeine Sprache (Negatives)
  in gleichmäßige Häppchen zu zerlegen.

Ausgabe: 16 kHz Mono PCM16 WAV-Dateien ``clip_001.wav …`` im Zielordner.
Nur numpy + Standardbibliothek, keine Zusatzabhängigkeiten.
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def read_wav_16k_mono(path: Path) -> np.ndarray:
    with wave.open(str(path)) as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        if width != 2:
            raise SystemExit("Bitte 16-bit-PCM-WAV aufnehmen (pw-record ... --channels 1).")
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != SAMPLE_RATE:
        # einfache lineare Resampling-Näherung (Aufnahme sollte bereits 16 kHz sein)
        target_len = int(len(data) * SAMPLE_RATE / rate)
        data = np.interp(np.linspace(0, len(data), target_len, endpoint=False),
                         np.arange(len(data)), data).astype(np.int16)
    return data


def write_clip(path: Path, samples: np.ndarray) -> None:
    with wave.open(str(path), "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.astype(np.int16).tobytes())


def split_silence(audio: np.ndarray, min_silence: float, min_clip: float,
                  max_clip: float, pad: float, floor_mult: float) -> list[np.ndarray]:
    block = 320  # 20 ms
    blocks = len(audio) // block
    energy = np.array([float(np.sqrt(np.mean(audio[i * block:(i + 1) * block].astype(np.float64) ** 2)))
                       for i in range(blocks)])
    noise_floor = np.median(energy) if energy.size else 0.0
    threshold = max(150.0, noise_floor * floor_mult)
    voiced = energy > threshold

    clips: list[np.ndarray] = []
    silence_blocks = max(1, int(min_silence * SAMPLE_RATE / block))
    pad_samples = int(pad * SAMPLE_RATE)
    start = None
    gap = 0
    for i, is_voiced in enumerate(voiced):
        if is_voiced:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= silence_blocks:
                end = i - gap
                s = max(0, start * block - pad_samples)
                e = min(len(audio), end * block + pad_samples)
                clip = audio[s:e]
                if min_clip * SAMPLE_RATE <= len(clip) <= max_clip * SAMPLE_RATE:
                    clips.append(clip)
                start = None
                gap = 0
    if start is not None:
        clip = audio[max(0, start * block - pad_samples):]
        if len(clip) >= min_clip * SAMPLE_RATE:
            clips.append(clip[:int(max_clip * SAMPLE_RATE)])
    return clips


def split_fixed(audio: np.ndarray, chunk: float) -> list[np.ndarray]:
    size = int(chunk * SAMPLE_RATE)
    return [audio[i:i + size] for i in range(0, len(audio) - size + 1, size)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Wake-Word-Aufnahme in Clips zerlegen")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["silence", "fixed"], default="silence")
    parser.add_argument("--min-silence", type=float, default=0.35)
    parser.add_argument("--min-clip", type=float, default=0.35)
    parser.add_argument("--max-clip", type=float, default=2.5)
    parser.add_argument("--pad", type=float, default=0.15)
    parser.add_argument("--floor-mult", type=float, default=3.0)
    parser.add_argument("--chunk", type=float, default=1.5)
    parser.add_argument("--prefix", default="clip")
    args = parser.parse_args()

    audio = read_wav_16k_mono(args.input)
    if args.mode == "silence":
        clips = split_silence(audio, args.min_silence, args.min_clip, args.max_clip, args.pad, args.floor_mult)
    else:
        clips = split_fixed(audio, args.chunk)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for index, clip in enumerate(clips, start=1):
        write_clip(args.output_dir / f"{args.prefix}_{index:03d}.wav", clip)
    print(f"{len(clips)} Clips geschrieben nach {args.output_dir}")
    if args.mode == "silence" and len(clips) < 15:
        print("Hinweis: wenige Clips – ggf. lauter/deutlicher sprechen oder --floor-mult senken (z.B. 2.0).")


if __name__ == "__main__":
    main()
