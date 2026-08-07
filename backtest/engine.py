import hashlib
import json

from backtest.metrics import calculate_metrics
from data.candles import CandleAggregator
from strategies.donchian_zigzag import DonchianZigZagStrategy


class ReplayEngine:
    VERSION = "1"

    def __init__(self, strategy_factory=DonchianZigZagStrategy):
        self.strategy_factory = strategy_factory

    def run(self, ticks):
        normalized = [self._normalize_tick(item) for item in ticks]
        for previous, current in zip(normalized, normalized[1:]):
            if current["epoch"] <= previous["epoch"]:
                raise ValueError("Dataset de ticks fora de ordem estrita")

        canonical = "\n".join(
            json.dumps(item, sort_keys=True, separators=(",", ":"))
            for item in normalized
        ).encode("utf-8")
        manifest = {
            "engine_version": self.VERSION,
            "sha256": hashlib.sha256(canonical).hexdigest(),
            "tick_count": len(normalized),
            "timeframe_seconds": 60,
            "duration_seconds": 120,
            "donchian_period": 21,
            "zigzag": {"deviation": 1, "depth": 15, "backstep": 3},
        }

        strategy = self.strategy_factory()
        aggregator = CandleAggregator(60)
        tick_history = []
        candles = []
        active = None
        trades = []

        for tick in normalized:
            if active and tick["epoch"] >= active["expiry_epoch"]:
                settled = self._settle(active, tick)
                trades.append(settled)
                strategy.on_trade_result(settled)
                active = None

            candle = aggregator.update(tick["epoch"], tick["quote"])
            candle_data = candle.as_dict()
            if candles and candles[-1]["time"] == candle_data["time"]:
                candles[-1] = candle_data
            else:
                candles.append(candle_data)
            tick_history.append({"epoch": tick["epoch"], "quote": tick["quote"]})

            if active is None:
                signal = strategy.analyze(tick_history, candles=list(candles))
                if signal:
                    params = strategy.get_contract_params()
                    if params != {"duration": 2, "duration_unit": "m"}:
                        raise ValueError("Replay final aceita somente o contrato fixo de 2 minutos")
                    active = {
                        "contract_type": signal.action,
                        "entry_epoch": tick["epoch"],
                        "expiry_epoch": tick["epoch"] + 120,
                        "entry_spot": tick["quote"],
                        "stake": float(strategy.get_stake()),
                        "payout_ratio": float(tick.get("payout_ratio", 0.8)),
                        "signal_reason": signal.reason,
                    }

        if active:
            trades.append({
                **active,
                "status": "invalid",
                "profit": 0.0,
                "exit_epoch": None,
                "exit_spot": None,
                "invalid_reason": "missing_tick_at_or_after_expiry",
            })

        metrics = calculate_metrics(trades)
        return {
            "status": "INCOMPLETE" if metrics["invalid_trades"] else "COMPLETE",
            "manifest": manifest,
            "trades": trades,
            "metrics": metrics,
        }

    @staticmethod
    def _normalize_tick(tick):
        if "epoch" not in tick or ("quote" not in tick and "price" not in tick):
            raise ValueError("Cada tick exige epoch e quote")
        normalized = {
            "epoch": int(tick["epoch"]),
            "quote": float(tick.get("quote", tick.get("price"))),
        }
        if tick.get("payout_ratio") is not None:
            normalized["payout_ratio"] = float(tick["payout_ratio"])
        return normalized

    @staticmethod
    def _settle(active, tick):
        direction = active["contract_type"]
        entry = float(active["entry_spot"])
        exit_spot = float(tick["quote"])
        won = (direction == "CALL" and exit_spot > entry) or (
            direction == "PUT" and exit_spot < entry
        )
        tied = exit_spot == entry
        status = "void" if tied else ("won" if won else "lost")
        profit = 0.0 if tied else (
            active["stake"] * active["payout_ratio"] if won else -active["stake"]
        )
        return {
            **active,
            "status": status,
            "profit": round(profit, 8),
            "exit_epoch": tick["epoch"],
            "exit_spot": exit_spot,
        }
