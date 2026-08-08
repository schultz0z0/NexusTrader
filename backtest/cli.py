import argparse
import json
from pathlib import Path

from backtest.engine import ReplayEngine
from strategies.donchian_zigzag import DonchianZigZagStrategy
from strategies.nexus_speed import NexusSpeedStrategy


def strategy_factory(strategy_id):
    return {
        "donchian": DonchianZigZagStrategy,
        "nexus_speed": NexusSpeedStrategy,
    }[strategy_id]


def main():
    parser = argparse.ArgumentParser(description="Replay deterministico NexusTrader")
    parser.add_argument("--input", required=True, help="Arquivo JSONL de ticks")
    parser.add_argument(
        "--strategy",
        choices=("donchian", "nexus_speed"),
        default="donchian",
        help="Estrategia fixa usada no replay",
    )
    args = parser.parse_args()
    path = Path(args.input)
    ticks = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    engine = ReplayEngine(strategy_factory=strategy_factory(args.strategy))
    print(json.dumps(engine.run(ticks), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
