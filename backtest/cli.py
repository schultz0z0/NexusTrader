import argparse
import json
from pathlib import Path

from backtest.engine import ReplayEngine


def main():
    parser = argparse.ArgumentParser(description="Replay deterministico NexusTrader")
    parser.add_argument("--input", required=True, help="Arquivo JSONL de ticks")
    args = parser.parse_args()
    path = Path(args.input)
    ticks = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(json.dumps(ReplayEngine().run(ticks), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
