from trading.safety import ensure_account_allowed


def normalize_account(account: dict) -> dict:
    account_id = str(account.get("account_id") or account.get("loginid") or "").strip()
    raw_type = str(account.get("account_type") or account.get("type") or "").lower()
    if raw_type in {"demo", "virtual"} or account_id.upper().startswith(("DOT", "VRTC")):
        account_type = "demo"
    elif raw_type == "real" or account_id.upper().startswith(("ROT", "CR")):
        account_type = "real"
    else:
        raise ValueError(f"Tipo de conta Deriv desconhecido para {account_id or 'conta sem ID'}")
    try:
        balance = float(account.get("balance") or 0)
    except (TypeError, ValueError):
        balance = 0.0
    return {
        "account_id": account_id,
        "account_type": account_type,
        "balance": balance,
        "currency": account.get("currency", "USD"),
        "status": account.get("status", "active"),
        "group": account.get("group"),
    }


def validate_selected_account(bot: dict, account: dict) -> dict:
    normalized = normalize_account(account)
    if normalized["account_id"] != str(bot.get("account_id") or ""):
        raise ValueError("A conta retornada pela Deriv nao corresponde a conta configurada")
    configured_type = str(bot.get("account_type") or "").lower()
    if configured_type != normalized["account_type"]:
        raise ValueError("O tipo da conta configurada nao corresponde ao tipo informado pela Deriv")
    if str(normalized.get("status", "active")).lower() != "active":
        raise ValueError("A conta Deriv selecionada nao esta ativa")
    ensure_account_allowed(normalized)
    return normalized

