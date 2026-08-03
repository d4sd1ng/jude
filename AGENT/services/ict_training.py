from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from core.paths import DATA_DIR
MODEL_DIR = DATA_DIR / "ict_models"
FEATURE_NAMES = [
    "h4_return_4", "h4_ema_gap", "h4_range_position", "h4_atr_ratio",
    "h1_return_8", "h1_ema_gap", "h1_range_position", "h1_atr_ratio",
    "h1_sweep_high", "h1_sweep_low", "h1_bull_fvg", "h1_bear_fvg",
    "m1_return_15", "m1_ema_gap", "m1_range_position", "m1_atr_ratio",
    "m1_sweep_high", "m1_sweep_low", "m1_bull_fvg", "m1_bear_fvg", "m1_displacement",
    "direction",
]


def _frame(rows: Iterable[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows))
    if frame.empty:
        raise ValueError("OHLCV-Daten fehlen.")
    required = {"time", "open", "high", "low", "close"}
    if not required <= set(frame.columns):
        raise ValueError("OHLCV benötigt time, open, high, low und close.")
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.sort_values("time").drop_duplicates("time").set_index("time")
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame


def _resample(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    return frame.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()


def _atr(frame: pd.DataFrame, period: int = 14) -> float:
    values = pd.concat(
        ((frame.high - frame.low), (frame.high - frame.close.shift()).abs(),
         (frame.low - frame.close.shift()).abs()), axis=1
    ).max(axis=1)
    return float(values.tail(period).mean())


def _features(frame: pd.DataFrame, return_bars: int) -> list[float]:
    if len(frame) < 24:
        raise ValueError("Zu wenige Kerzen für ICT-Merkmale.")
    close = float(frame.close.iloc[-1])
    atr = max(_atr(frame), abs(close) * 1e-8)
    ema_fast = float(frame.close.ewm(span=8, adjust=False).mean().iloc[-1])
    ema_slow = float(frame.close.ewm(span=21, adjust=False).mean().iloc[-1])
    high, low = float(frame.high.tail(20).max()), float(frame.low.tail(20).min())
    position = (close - low) / max(high - low, atr)
    previous = frame.iloc[-21:-1]
    last = frame.iloc[-1]
    sweep_high = float(last.high > previous.high.max() and last.close < previous.high.max())
    sweep_low = float(last.low < previous.low.min() and last.close > previous.low.min())
    bull_fvg = float(frame.low.iloc[-1] > frame.high.iloc[-3])
    bear_fvg = float(frame.high.iloc[-1] < frame.low.iloc[-3])
    return [
        float(close / frame.close.iloc[-1 - return_bars] - 1),
        (ema_fast - ema_slow) / atr,
        position,
        atr / max(abs(close), 1e-8),
        sweep_high, sweep_low, bull_fvg, bear_fvg,
    ]


def features_from_frames(h4_rows: Iterable[dict], h1_rows: Iterable[dict], m1_rows: Iterable[dict]) -> np.ndarray:
    h4, h1, m1 = _frame(h4_rows), _frame(h1_rows), _frame(m1_rows)
    h4_values = _features(h4, 4)[:4]
    h1_values = _features(h1, 8)
    m1_values = _features(m1, 15)
    m1_atr = max(_atr(m1), abs(float(m1.close.iloc[-1])) * 1e-8)
    displacement = abs(float(m1.close.iloc[-1] - m1.open.iloc[-1])) / m1_atr
    direction = 1.0 if h4_values[0] > 0 and h4_values[1] > 0 else -1.0 if h4_values[0] < 0 and h4_values[1] < 0 else 0.0
    return np.asarray(h4_values + h1_values + m1_values + [displacement, direction], dtype=np.float64)


def _in_kill_zone(moment: pd.Timestamp) -> bool:
    local = moment.tz_convert("America/New_York")
    minute = local.hour * 60 + local.minute
    return minute >= 20 * 60 or minute < 0 or 2 * 60 <= minute < 5 * 60 or 8 * 60 + 30 <= minute < 12 * 60


@dataclass
class TrainingSet:
    x: np.ndarray
    y: np.ndarray
    timestamps: list[str]


def build_training_set(m1_rows: Iterable[dict], horizon: int = 90, rr: float = 2.0) -> TrainingSet:
    m1 = _frame(m1_rows)
    h1, h4 = _resample(m1, "1h"), _resample(m1, "4h")
    x, y, timestamps = [], [], []
    for index in range(240, len(m1) - horizon, 5):
        moment = m1.index[index]
        if not _in_kill_zone(moment):
            continue
        m1_slice = m1.iloc[:index + 1]
        h1_slice, h4_slice = h1.loc[:moment], h4.loc[:moment]
        if len(h1_slice) < 30 or len(h4_slice) < 24:
            continue
        values = features_from_frames(h4_slice.tail(100).reset_index().to_dict("records"),
                                      h1_slice.tail(160).reset_index().to_dict("records"),
                                      m1_slice.tail(200).reset_index().to_dict("records"))
        direction = int(values[-1])
        if direction == 0:
            continue
        entry = float(m1.close.iloc[index])
        swing = float(m1.low.iloc[index - 20:index].min()) if direction > 0 else float(m1.high.iloc[index - 20:index].max())
        risk = abs(entry - swing)
        if risk <= 0:
            continue
        target = entry + direction * rr * risk
        stop = swing
        outcome = None
        for _, candle in m1.iloc[index + 1:index + 1 + horizon].iterrows():
            hit_target = candle.high >= target if direction > 0 else candle.low <= target
            hit_stop = candle.low <= stop if direction > 0 else candle.high >= stop
            if hit_target and hit_stop:
                outcome = None
                break
            if hit_target:
                outcome = 1
                break
            if hit_stop:
                outcome = 0
                break
        if outcome is not None:
            x.append(values)
            y.append(outcome)
            timestamps.append(moment.isoformat())
    if len(x) < 200 or len(set(y)) < 2:
        raise RuntimeError("Mindestens 200 gelabelte Setups mit Gewinn- und Verlustfällen werden benötigt.")
    return TrainingSet(np.asarray(x), np.asarray(y, dtype=np.int64), timestamps)


class ICTTrainingService:
    def model_path(self, symbol: str) -> Path:
        if symbol not in {"XAUUSD", "BTCUSD"}:
            raise ValueError("Training unterstützt nur XAUUSD und BTCUSD.")
        return MODEL_DIR / f"{symbol.lower()}_ict.joblib"

    def train(self, symbol: str, m1_rows: Iterable[dict]) -> dict:
        from joblib import dump
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import (
            brier_score_loss,
            precision_recall_fscore_support,
            roc_auc_score,
        )

        dataset = build_training_set(m1_rows)
        split = int(len(dataset.y) * 0.8)
        train_x, test_x = dataset.x[:split], dataset.x[split:]
        train_y, test_y = dataset.y[:split], dataset.y[split:]
        if len(set(test_y)) < 2:
            raise RuntimeError("Walk-forward-Test enthält nicht beide Ergebnisgruppen.")
        model = HistGradientBoostingClassifier(max_iter=220, max_leaf_nodes=15, learning_rate=0.06,
                                               l2_regularization=1.0, random_state=42)
        model.fit(train_x, train_y)
        probabilities = model.predict_proba(test_x)[:, 1]
        thresholds = []
        for threshold in np.linspace(0.5, 0.9, 41):
            predicted = probabilities >= threshold
            precision, recall, f1, _ = precision_recall_fscore_support(
                test_y, predicted, average="binary", zero_division=0
            )
            thresholds.append((precision >= 0.60 and predicted.sum() >= 10, f1, precision, recall, threshold, int(predicted.sum())))
        accepted, f1, precision, recall, threshold, signals = max(thresholds)
        metrics = {
            "samples": int(len(dataset.y)), "train_samples": int(len(train_y)), "test_samples": int(len(test_y)),
            "positive_rate": float(dataset.y.mean()), "roc_auc": float(roc_auc_score(test_y, probabilities)),
            "brier": float(brier_score_loss(test_y, probabilities)), "precision": float(precision),
            "recall": float(recall), "f1": float(f1), "test_signals": signals,
            "walk_forward_start": dataset.timestamps[split], "walk_forward_end": dataset.timestamps[-1],
        }
        if not accepted or metrics["roc_auc"] < 0.55:
            raise RuntimeError("ICT-Modell verfehlt die Mindestgüte: " + json.dumps(metrics, ensure_ascii=False))
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"model": model, "features": FEATURE_NAMES, "threshold": float(threshold),
                   "symbol": symbol, "trained_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics}
        dump(payload, self.model_path(symbol))
        self.model_path(symbol).with_suffix(".json").write_text(
            json.dumps({key: value for key, value in payload.items() if key != "model"}, indent=2), encoding="utf-8"
        )
        return {key: value for key, value in payload.items() if key != "model"}

    def score(self, symbol: str, h4_rows: Iterable[dict], h1_rows: Iterable[dict], m1_rows: Iterable[dict]) -> dict:
        from joblib import load

        path = self.model_path(symbol)
        if not path.is_file():
            return {"ready": False, "reason": "Kein erfolgreich walk-forward-validiertes ICT-Modell vorhanden."}
        payload = load(path)
        values = features_from_frames(h4_rows, h1_rows, m1_rows)
        probability = float(payload["model"].predict_proba(values.reshape(1, -1))[0, 1])
        return {"ready": True, "probability": probability, "threshold": float(payload["threshold"]),
                "passed": probability >= float(payload["threshold"]), "trained_at": payload["trained_at"],
                "metrics": payload["metrics"], "features": dict(zip(FEATURE_NAMES, values.tolist()))}

    def status(self) -> dict:
        result = {}
        for symbol in ("XAUUSD", "BTCUSD"):
            path = self.model_path(symbol).with_suffix(".json")
            result[symbol] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"ready": False}
        return result
