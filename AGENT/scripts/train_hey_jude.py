from __future__ import annotations

import argparse
import hashlib
import json
import wave
from pathlib import Path

import numpy as np
from pyopen_wakeword import OpenWakeWordFeatures
from scipy.signal import resample_poly
from sklearn.metrics import accuracy_score, confusion_matrix, recall_score
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType

SAMPLE_RATE = 16_000
BLOCK_SIZE = 1_280
INPUT_WINDOWS = 16
FEATURES = 96


def read_audio(path: Path) -> np.ndarray:
    with wave.open(str(path)) as wav:
        channels, sample_width, source_rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        if sample_width != 2:
            raise ValueError(f"Nur PCM16 wird unterstützt: {path}")
        audio = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if source_rate != SAMPLE_RATE:
        audio = resample_poly(audio.astype(np.float32), SAMPLE_RATE, source_rate)
    peak = max(1.0, float(np.max(np.abs(audio))))
    return np.clip(audio * (24_000.0 / peak), -32768, 32767).astype(np.int16)


def augment(audio: np.ndarray, seed: int) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    variants = [audio]
    speed = float(rng.uniform(0.92, 1.08))
    changed = resample_poly(audio.astype(np.float32), int(1000 * speed), 1000)
    changed *= float(rng.uniform(0.55, 0.95))
    noise = rng.normal(0, float(rng.uniform(120, 650)), changed.shape)
    variants.append(np.clip(changed + noise, -32768, 32767).astype(np.int16))
    return variants


def embeddings(features: OpenWakeWordFeatures, audio: np.ndarray) -> np.ndarray:
    features.reset()
    padded = np.concatenate((np.zeros(int(0.8 * SAMPLE_RATE), dtype=np.int16), audio,
                             np.zeros(int(0.8 * SAMPLE_RATE), dtype=np.int16)))
    rows = []
    for offset in range(0, len(padded), BLOCK_SIZE):
        block = padded[offset:offset + BLOCK_SIZE]
        if len(block) < BLOCK_SIZE:
            block = np.pad(block, (0, BLOCK_SIZE - len(block)))
        for value in features.process_streaming(block.tobytes()):
            rows.extend(value[0, 0])
    return np.asarray(rows, dtype=np.float32)


def windows(values: np.ndarray) -> np.ndarray:
    if len(values) < INPUT_WINDOWS:
        return np.empty((0, INPUT_WINDOWS * FEATURES), dtype=np.float32)
    return np.asarray([values[index:index + INPUT_WINDOWS].reshape(-1)
                       for index in range(len(values) - INPUT_WINDOWS + 1)], dtype=np.float32)


def select_examples(all_windows: np.ndarray, positive: bool) -> tuple[np.ndarray, np.ndarray]:
    count = len(all_windows)
    if not count:
        return np.empty((0, INPUT_WINDOWS * FEATURES), dtype=np.float32), np.empty(0, dtype=np.int64)
    if positive:
        positive_indices = np.unique(np.linspace(int(count * 0.80), max(int(count * 0.80), int(count * 0.97)), 8).astype(int))
        positive_indices = np.clip(positive_indices, 0, count - 1)
        negative_indices = np.unique(np.linspace(0, max(0, int(count * 0.55)), 4).astype(int))
        selected = np.concatenate((all_windows[positive_indices], all_windows[negative_indices]))
        labels = np.concatenate((np.ones(len(positive_indices), dtype=np.int64),
                                 np.zeros(len(negative_indices), dtype=np.int64)))
        return selected, labels
    # Bei dauerhaft lauschenden Systemen kann jedes einzelne Negativfenster
    # fehlaktivieren. Deshalb darf hier nichts ausgedünnt werden.
    return all_windows, np.zeros(count, dtype=np.int64)


def is_validation(path: Path) -> bool:
    return int(hashlib.sha256(str(path).encode()).hexdigest()[:8], 16) % 5 == 0


def build_dataset(root: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    features = OpenWakeWordFeatures.from_builtin()
    groups: dict[str, list[np.ndarray]] = {"train_x": [], "train_y": [], "val_x": [], "val_y": []}
    try:
        paths = sorted(root.glob("*/*.wav"))
        if not paths:
            raise RuntimeError(f"Keine WAV-Dateien unter {root}")
        for number, path in enumerate(paths, start=1):
            positive = path.parent.name.startswith("positive")
            seed = int(hashlib.sha256(str(path).encode()).hexdigest()[:8], 16)
            for variant in augment(read_audio(path), seed):
                x, y = select_examples(windows(embeddings(features, variant)), positive)
                prefix = "val" if is_validation(path) else "train"
                groups[f"{prefix}_x"].append(x)
                groups[f"{prefix}_y"].append(y)
            if number % 100 == 0:
                print(f"Features: {number}/{len(paths)}", flush=True)
    finally:
        features.close()
    return tuple(np.concatenate(groups[key]) for key in ("train_x", "train_y", "val_x", "val_y"))


def choose_threshold(labels: np.ndarray, probabilities: np.ndarray) -> tuple[float, dict]:
    candidates = []
    for threshold in np.linspace(0.05, 0.95, 181):
        predicted = probabilities >= threshold
        recall = recall_score(labels, predicted, zero_division=0)
        tn, fp, fn, tp = confusion_matrix(labels, predicted, labels=[0, 1]).ravel()
        fpr = fp / max(1, fp + tn)
        candidates.append((recall >= 0.90, -fpr, threshold, recall, fpr, int(tp), int(fp), int(fn), int(tn)))
    _, _, threshold, recall, fpr, tp, fp, fn, tn = max(candidates)
    return float(threshold), {"recall": recall, "false_positive_rate": fpr, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", default="jude_angetreten")
    parser.add_argument("--phrase", default="Jude angetreten")
    parser.add_argument("--cache", type=Path,
                        help="Optionaler NPZ-Cache der extrahierten OpenWakeWord-Merkmale")
    args = parser.parse_args()
    if args.cache and args.cache.is_file():
        cached = np.load(args.cache)
        train_x, train_y = cached["train_x"], cached["train_y"]
        val_x, val_y = cached["val_x"], cached["val_y"]
    else:
        train_x, train_y, val_x, val_y = build_dataset(args.data)
        if args.cache:
            args.cache.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(args.cache, train_x=train_x, train_y=train_y, val_x=val_x, val_y=val_y)
    print(f"Training: {train_x.shape}; Validierung: {val_x.shape}", flush=True)
    positive = np.flatnonzero(train_y == 1)
    negative = np.flatnonzero(train_y == 0)
    if not len(positive) or not len(negative):
        raise RuntimeError("Trainingsdaten enthalten nicht beide Klassen.")
    rng = np.random.default_rng(42)
    balanced_indices = np.concatenate((negative, rng.choice(positive, size=len(negative), replace=True)))
    rng.shuffle(balanced_indices)
    train_x, train_y = train_x[balanced_indices], train_y[balanced_indices]
    classifier = MLPClassifier(hidden_layer_sizes=(128, 32), activation="relu", batch_size=128,
                               max_iter=240, early_stopping=True, n_iter_no_change=20,
                               validation_fraction=0.12, random_state=42)
    pipeline = Pipeline((("scale", StandardScaler()), ("classifier", classifier)))
    pipeline.fit(train_x, train_y)
    probabilities = pipeline.predict_proba(val_x)[:, 1]
    threshold, metrics = choose_threshold(val_y, probabilities)
    metrics["accuracy_at_0_5"] = float(accuracy_score(val_y, probabilities >= 0.5))
    metrics["training_examples"] = int(len(train_y))
    metrics["validation_examples"] = int(len(val_y))
    args.output.mkdir(parents=True, exist_ok=True)
    onnx = convert_sklearn(pipeline, initial_types=[("input", FloatTensorType([None, INPUT_WINDOWS * FEATURES]))],
                           options={id(classifier): {"zipmap": False}}, target_opset=15)
    (args.output / f"{args.name}.onnx").write_bytes(onnx.SerializeToString())
    metadata = {"name": args.name, "phrase": args.phrase, "threshold": threshold,
                "input_windows": INPUT_WINDOWS, "features": FEATURES, "metrics": metrics,
                "training_source": "synthetic German Piper samples validated with local Whisper"}
    (args.output / f"{args.name}.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2), flush=True)


if __name__ == "__main__":
    main()
