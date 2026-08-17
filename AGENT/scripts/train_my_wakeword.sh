#!/usr/bin/env bash
# Trainiert das Wake-Word "Jude angetreten" auf deine Stimme.
#
# Aufruf:
#   scripts/train_my_wakeword.sh POSITIVE_RAW.wav NEGATIVE_RAW.wav
#     POSITIVE_RAW.wav  = eine Aufnahme mit ~40x "Jude angetreten" (Pausen dazwischen)
#     NEGATIVE_RAW.wav  = deine ~5 min allgemeines Reden (als Negativbeispiele)
#
# Ablauf: Clips schneiden -> positive/negative-Ordner bauen -> trainieren (~5 min)
# -> altes Modell sichern -> neues jude_angetreten.onnx einsetzen.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # AGENT/
ROOT="$(cd "$HERE/.." && pwd)"                     # Projekt/
PY="$ROOT/.venv/bin/python"
POS_RAW="${1:?Positiv-Aufnahme (WAV) angeben}"
NEG_RAW="${2:?Negativ-Aufnahme (WAV) angeben}"

MODELS_DIR="${JUDE_DATA_ROOT:-/media/d4sd1ng/AI-Data}/Jude/models/wakeword"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/jude-wake.XXXXXX")"
DATA="$WORK/data"
mkdir -p "$DATA/positive" "$DATA/negative"

echo "== 1/4  Positive Clips schneiden (Sprechpausen)"
"$PY" "$HERE/scripts/split_wakeword.py" --input "$POS_RAW" --output-dir "$DATA/positive" --mode silence --prefix jude

echo "== 2/4  Negative Clips schneiden (feste Fenster)"
"$PY" "$HERE/scripts/split_wakeword.py" --input "$NEG_RAW" --output-dir "$DATA/negative" --mode fixed --chunk 1.5 --prefix neg

POS=$(find "$DATA/positive" -name '*.wav' | wc -l)
NEG=$(find "$DATA/negative" -name '*.wav' | wc -l)
echo "   Positive: $POS  |  Negative: $NEG"
[ "$POS" -ge 15 ] || { echo "Zu wenige positive Clips ($POS). Bitte länger/deutlicher aufnehmen."; exit 1; }
[ "$NEG" -ge 30 ] || { echo "Zu wenige negative Clips ($NEG). Längere Negativ-Aufnahme nutzen."; exit 1; }

echo "== 3/4  Training (kann ein paar Minuten dauern)"
OUT="$WORK/out"
"$PY" "$HERE/scripts/train_wakeword.py" --data "$DATA" --output "$OUT" \
      --name jude_angetreten --phrase "Jude angetreten" --cache "$WORK/features.npz"

echo "== 4/4  Modell einsetzen"
mkdir -p "$MODELS_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
for ext in onnx json; do
  [ -f "$MODELS_DIR/jude_angetreten.$ext" ] && cp "$MODELS_DIR/jude_angetreten.$ext" "$MODELS_DIR/jude_angetreten.$ext.bak-$STAMP"
  cp "$OUT/jude_angetreten.$ext" "$MODELS_DIR/jude_angetreten.$ext"
done
echo "Fertig. Neues Modell aktiv: $MODELS_DIR/jude_angetreten.onnx (Backup: *.bak-$STAMP)"
echo "Zum Testen: Jude neu starten und 'Jude angetreten' sagen."
