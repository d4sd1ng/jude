from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from typing import Iterable

import numpy as np
import onnxruntime as ort

from core.paths import MODELS_DIR as _MODELS_DIR


class JudeWakeWordModel:
    """Streaming-Klassifikator für das lokale Kommando „Jude angetreten“."""

    DEFAULT_MODEL = _MODELS_DIR / "wakeword" / "jude_angetreten.onnx"

    def __init__(self, model_path: str | Path | None = None):
        self.path = Path(model_path or os.getenv("WAKE_WORD_MODEL", str(self.DEFAULT_MODEL))).resolve()
        metadata_path = self.path.with_suffix(".json")
        if not self.path.is_file() or not metadata_path.is_file():
            raise RuntimeError(f"Wake-Word-Modell für 'Jude angetreten' fehlt: {self.path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("phrase") != "Jude angetreten":
            raise RuntimeError("Das Wake-Word-Modell ist nicht für 'Jude angetreten' gekennzeichnet.")
        self.input_windows = int(metadata["input_windows"])
        self.feature_count = int(metadata["features"])
        configured_threshold = os.getenv("WAKE_WORD_THRESHOLD", "").strip()
        self.threshold = float(configured_threshold or metadata["threshold"])
        self.buffer: deque[np.ndarray] = deque(maxlen=self.input_windows)
        self.session = ort.InferenceSession(str(self.path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def reset(self) -> None:
        self.buffer.clear()

    def process_streaming(self, embeddings: np.ndarray) -> Iterable[float]:
        for row in embeddings.reshape(-1, self.feature_count):
            self.buffer.append(np.asarray(row, dtype=np.float32))
            if len(self.buffer) < self.input_windows:
                continue
            value = np.asarray(self.buffer, dtype=np.float32).reshape(1, -1)
            outputs = self.session.run(None, {self.input_name: value})
            probability = None
            for output in outputs:
                array = np.asarray(output)
                if array.ndim == 2 and array.shape[-1] == 2:
                    probability = float(array[0, 1])
                    break
                if array.size == 1 and np.issubdtype(array.dtype, np.floating):
                    probability = float(array.reshape(-1)[0])
            if probability is None:
                raise RuntimeError("Das Wake-Word-Modell lieferte keine Wahrscheinlichkeit.")
            yield probability

    def close(self) -> None:
        self.buffer.clear()
