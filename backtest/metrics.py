import math
import statistics


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = (len(ordered) - 1) * float(percentile)
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def calculate_metrics(trades):
    valid = [trade for trade in trades if trade.get("status") in {"won", "lost", "void"}]
    wins = [trade for trade in valid if trade["status"] == "won"]
    losses = [trade for trade in valid if trade["status"] == "lost"]
    voids = [trade for trade in valid if trade["status"] == "void"]
    invalid = [trade for trade in trades if trade.get("status") not in {"won", "lost", "void"}]
    profits = [float(trade.get("profit") or 0) for trade in valid]
    gross_profit = sum(value for value in profits if value > 0)
    gross_loss = abs(sum(value for value in profits if value < 0))

    equity = 0.0
    peak = 0.0
    peak_index = 0
    max_drawdown = 0.0
    max_drawdown_percent = 0.0
    max_drawdown_duration = 0
    curve = []
    loss_streak = 0
    max_loss_streak = 0
    for index, trade in enumerate(valid, start=1):
        equity += float(trade.get("profit") or 0)
        curve.append(round(equity, 8))
        if equity > peak:
            peak = equity
            peak_index = index
        drawdown = peak - equity
        max_drawdown = max(max_drawdown, drawdown)
        if peak > 0:
            max_drawdown_percent = max(max_drawdown_percent, drawdown / peak * 100)
        max_drawdown_duration = max(max_drawdown_duration, index - peak_index)
        if trade["status"] == "lost":
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0

    payouts = [float(trade.get("payout_ratio") or 0) for trade in valid]
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    exposure = sum(
        max(0, int(trade.get("exit_epoch") or 0) - int(trade.get("entry_epoch") or 0))
        for trade in valid
    )
    return {
        "trades": len(valid),
        "wins": len(wins),
        "losses": len(losses),
        "voids": len(voids),
        "invalid_trades": len(invalid),
        "net_profit": round(sum(profits), 8),
        "gross_profit": round(gross_profit, 8),
        "gross_loss": round(gross_loss, 8),
        "win_rate": len(wins) / len(valid) if valid else 0.0,
        "expectancy": sum(profits) / len(valid) if valid else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "payoff": avg_win / avg_loss if avg_loss else None,
        "max_drawdown_absolute": round(max_drawdown, 8),
        "max_drawdown_percent": round(max_drawdown_percent, 8),
        "max_drawdown_duration_trades": max_drawdown_duration,
        "max_loss_streak": max_loss_streak,
        "exposure_seconds": exposure,
        "payout": {
            "min": min(payouts) if payouts else None,
            "p5": _percentile(payouts, 0.05),
            "median": statistics.median(payouts) if payouts else None,
        },
        "equity_curve": curve,
    }
