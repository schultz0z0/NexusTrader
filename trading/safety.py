from config.settings import settings


class RealTradingDisabled(RuntimeError):
    pass


def ensure_demo_account(account) -> None:
    account_type = account.get("account_type") if isinstance(account, dict) else account
    if str(account_type).lower() != "demo" and not settings.ALLOW_REAL_TRADING:
        raise RealTradingDisabled("Execucao real desabilitada: use uma conta demo")
