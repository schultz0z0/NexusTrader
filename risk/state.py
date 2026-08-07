"""Pure, serializable money-management and circuit-breaker transitions."""


def initial_risk_state(initial_stake=1.0):
    return {
        "current_stake": float(initial_stake),
        "current_level": 0,
        "consecutive_wins": 0,
        "consecutive_losses": 0,
        "circuit_consecutive_losses": 0,
        "circuit_tripped_at": 0.0,
    }


def advance_risk_state(
    state,
    *,
    is_win,
    profit,
    mode,
    initial_stake,
    money_config,
    risk_config,
    settled_epoch,
):
    next_state = {**initial_risk_state(initial_stake), **(state or {})}
    mode = str(mode or "fixed").lower()
    initial_stake = float(initial_stake)

    if is_win:
        next_state["consecutive_wins"] += 1
        next_state["consecutive_losses"] = 0
        if mode == "martingale":
            next_state["current_stake"] = initial_stake
            next_state["current_level"] = 0
        elif mode == "soros":
            levels = int(money_config.get("levels", 2))
            percent = float(money_config.get("percent", 0.5))
            if next_state["consecutive_wins"] <= levels:
                next_state["current_stake"] = initial_stake + float(profit) * percent
            else:
                next_state["current_stake"] = initial_stake
                next_state["consecutive_wins"] = 0
        else:
            next_state["current_stake"] = initial_stake
    else:
        next_state["consecutive_losses"] += 1
        next_state["consecutive_wins"] = 0
        if mode == "martingale":
            next_state["current_level"] += 1
            max_levels = int(money_config.get("max_levels", 3))
            if next_state["current_level"] <= max_levels:
                next_state["current_stake"] *= float(
                    money_config.get("multiplier", 2.0)
                )
            else:
                next_state["current_stake"] = initial_stake
                next_state["current_level"] = 0
        else:
            next_state["current_stake"] = initial_stake

    if is_win:
        next_state["circuit_consecutive_losses"] = 0
        next_state["circuit_tripped_at"] = 0.0
    else:
        next_state["circuit_consecutive_losses"] += 1
        maximum = int(risk_config.get("max_consecutive_losses", 3))
        if next_state["circuit_consecutive_losses"] >= maximum:
            next_state["circuit_tripped_at"] = float(settled_epoch)

    next_state["current_stake"] = round(float(next_state["current_stake"]), 2)
    return next_state
