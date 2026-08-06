from config.settings import settings


class RealTradingDisabled(RuntimeError):
    pass


def ensure_account_allowed(account) -> None:
    account_type = account.get("account_type") if isinstance(account, dict) else account
    account_type = str(account_type).lower()
    if account_type not in {"demo", "real"}:
        raise ValueError("Tipo de conta deve ser demo ou real")
    if account_type == "real" and not settings.ALLOW_REAL_TRADING:
        raise RealTradingDisabled("Execucao real desabilitada no servidor")


def ensure_demo_account(account) -> None:
    """Backward-compatible name for callers/tests; now enforces the feature flag."""
    ensure_account_allowed(account)
