import hashlib
import json

from backtest.metrics import calculate_metrics
from data.candles import CandleAggregator
from strategies.donchian_zigzag import DonchianZigZagStrategy


class ReplayEngine:
    VERSION = "2"

    def __init__(self, strategy_factory=DonchianZigZagStrategy):
        self.strategy_factory = strategy_factory

    def run(self, ticks):
        strategy = self.strategy_factory()
        params = strategy.get_contract_params()
        if params == {"duration": 2, "duration_unit": "m"}:
            duration_seconds = 120
            duration_ticks = None
            next_tick_entry = False
        elif params == {"duration": 5, "duration_unit": "t"}:
            duration_seconds = None
            duration_ticks = 5
            next_tick_entry = True
        else:
            raise ValueError("Replay aceita somente os contratos fixos 2m ou 5 ticks")

        normalized = [
            self._normalize_tick(item, sequence=index)
            for index, item in enumerate(ticks, start=1)
        ]
        for previous, current in zip(normalized, normalized[1:]):
            out_of_order = current["epoch"] < previous["epoch"]
            duplicate_legacy_epoch = (
                not next_tick_entry and current["epoch"] == previous["epoch"]
            )
            if out_of_order or duplicate_legacy_epoch:
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
            "duration_unit": params["duration_unit"],
            "contract_duration": params["duration"],
        }
        if next_tick_entry:
            manifest["duration_ticks"] = duration_ticks
            manifest["strategy"] = "nexus_speed"
            manifest["indicators"] = {"ema": 5, "adx": 10, "atr": 14}
            manifest["min_profit_ratio"] = float(
                getattr(strategy, "min_profit_ratio", 0.87)
            )
        else:
            manifest["duration_seconds"] = duration_seconds
            manifest["strategy"] = "donchian"
            manifest["donchian_period"] = 21
            manifest["zigzag"] = {"deviation": 1, "depth": 15, "backstep": 3}

        aggregator = CandleAggregator(60)
        tick_history = []
        candles = []
        active = None
        pending_signal = None
        trades = []
        rejected_signals = []

        for tick in normalized:
            active_expired = active and (
                tick["sequence"] >= active["expiry_sequence"]
                if next_tick_entry
                else tick["epoch"] >= active["expiry_epoch"]
            )
            if active_expired:
                settled = self._settle(active, tick)
                trades.append(settled)
                strategy.on_trade_result(settled)
                active = None

            if next_tick_entry and pending_signal is not None and active is None:
                payout_ratio = tick.get("payout_ratio")
                if payout_ratio is None:
                    trades.append({
                        "contract_type": pending_signal.action,
                        "entry_epoch": tick["epoch"],
                        "entry_spot": tick["quote"],
                        "stake": float(strategy.get_stake()),
                        "status": "invalid",
                        "profit": 0.0,
                        "exit_epoch": None,
                        "exit_spot": None,
                        "signal_reason": pending_signal.reason,
                        "invalid_reason": "missing_payout_ratio",
                    })
                elif float(payout_ratio) + 1e-12 < float(
                    getattr(strategy, "min_profit_ratio", 0.87)
                ):
                    rejected_signals.append({
                        "signal_epoch": pending_signal.timestamp,
                        "entry_epoch": tick["epoch"],
                        "reason": "profit_ratio_below_minimum",
                        "payout_ratio": float(payout_ratio),
                    })
                else:
                    active = {
                        "contract_type": pending_signal.action,
                        "entry_epoch": tick["epoch"],
                        "entry_sequence": tick["sequence"],
                        "expiry_sequence": tick["sequence"] + duration_ticks,
                        "entry_spot": tick["quote"],
                        "stake": float(strategy.get_stake()),
                        "payout_ratio": float(payout_ratio),
                        "signal_reason": pending_signal.reason,
                    }
                pending_signal = None

            candle = aggregator.update(tick["epoch"], tick["quote"])
            candle_data = candle.as_dict()
            if candles and candles[-1]["time"] == candle_data["time"]:
                candles[-1] = candle_data
            else:
                candles.append(candle_data)
            tick_history.append(dict(tick))

            if active is None:
                signal = strategy.analyze(tick_history, candles=list(candles))
                if signal:
                    if next_tick_entry:
                        pending_signal = signal
                    else:
                        active = {
                            "contract_type": signal.action,
                            "entry_epoch": tick["epoch"],
                            "expiry_epoch": tick["epoch"] + duration_seconds,
                            "entry_spot": tick["quote"],
                            "stake": float(strategy.get_stake()),
                            "payout_ratio": float(tick.get("payout_ratio", 0.8)),
                            "signal_reason": signal.reason,
                        }

        if pending_signal is not None:
            trades.append({
                "contract_type": pending_signal.action,
                "entry_epoch": None,
                "entry_spot": None,
                "stake": float(strategy.get_stake()),
                "status": "invalid",
                "profit": 0.0,
                "exit_epoch": None,
                "exit_spot": None,
                "signal_reason": pending_signal.reason,
                "invalid_reason": "missing_tick_after_signal",
            })

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
            "rejected_signals": rejected_signals,
            "metrics": metrics,
        }

    @staticmethod
    def _normalize_tick(tick, sequence):
        if "epoch" not in tick or ("quote" not in tick and "price" not in tick):
            raise ValueError("Cada tick exige epoch e quote")
        normalized = {
            "epoch": int(tick["epoch"]),
            "quote": float(tick.get("quote", tick.get("price"))),
            "sequence": int(sequence),
            "pip_size": (
                int(tick["pip_size"]) if tick.get("pip_size") is not None else 2
            ),
            "is_live": True,
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
