import argparse
import json
from pathlib import Path

from backtest.engine import ReplayEngine
from strategies.donchian_zigzag import DonchianZigZagStrategy
from strategies.nexus_speed import NexusSpeedStrategy


def strategy_factory(strategy_id: str, adx_threshold: int = 30):
    return {
        "donchian": DonchianZigZagStrategy,
        "nexus_speed": lambda: NexusSpeedStrategy(adx_threshold=adx_threshold),
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
    parser.add_argument(
        "--adx-threshold",
        type=int,
        choices=(20, 25, 30),
        default=30,
        help="ADX mínimo do Nexus Speed",
    )
    args = parser.parse_args()
    path = Path(args.input)
    ticks = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    engine = ReplayEngine(
        strategy_factory=strategy_factory(args.strategy, args.adx_threshold)
    )
    print(json.dumps(engine.run(ticks), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
