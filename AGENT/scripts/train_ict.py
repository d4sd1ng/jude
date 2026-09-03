from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from services.ict import MT5MCPClient
from services.ict_training import ICTTrainingService


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward-Training des read-only ICT/SMC-Gates")
    parser.add_argument("--symbol", required=True, choices=["XAUUSD", "BTCUSD"])
    parser.add_argument("--csv", type=Path, help="M1-CSV mit time, open, high, low, close")
    parser.add_argument("--count", type=int, default=120_000, help="M1-Kerzen beim MT5-Abruf")
    args = parser.parse_args()
    if args.csv:
        rows = pd.read_csv(args.csv).to_dict("records")
    else:
        rows = MT5MCPClient().call("get_ohlcv", {"symbol": args.symbol, "timeframe": "M1", "count": args.count})
    result = ICTTrainingService().train(args.symbol, rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
