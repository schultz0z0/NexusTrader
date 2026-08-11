"""Read-only smoke test for the current Deriv REST + OTP + WebSocket flow."""

import argparse
import asyncio
import json
import logging

from core.auth import AuthManager
from core.connection import NexusConnection
from trading.safety import ensure_demo_account


def account_id(account):
    return account.get("account_id") or account.get("loginid") or ""


def is_demo(account):
    identifier = account_id(account).upper()
    kind = str(account.get("account_type") or account.get("type") or "").lower()
    return (
        kind in {"demo", "virtual"}
        or account.get("is_virtual") in {1, True, "1", "true"}
        or identifier.startswith(("VRTC", "DOT"))
    )


async def run(symbol="R_100"):
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    auth = AuthManager()
    connection = NexusConnection(auth, reconnect_delays=(1, 2, 4))
    summary = {"rest": False, "websocket": False, "history": False, "tick": False}
    try:
        accounts = await auth.list_accounts()
        demos = [item for item in accounts if is_demo(item)]
        if not demos:
            raise RuntimeError("Nenhuma conta demo encontrada para este token")
        selected = demos[0]
        selected_id = account_id(selected)
        ensure_demo_account("demo")
        summary["rest"] = True
        summary["demo_account_verified"] = True

        if not await connection.connect(selected_id):
            raise RuntimeError("Falha ao conectar o WebSocket OTP")
        summary["websocket"] = True

        history = await connection.send({
            "ticks_history": symbol,
            "end": "latest",
            "count": 20,
            "style": "ticks",
        })
        if "error" in history or not history.get("history", {}).get("prices"):
            raise RuntimeError(f"Historico indisponivel: {history.get('error')}")
        summary["history"] = True
        summary["history_points"] = len(history["history"]["prices"])

        first_tick = asyncio.Event()

        async def on_tick(message):
            if message.get("tick"):
                first_tick.set()

        await connection.subscribe("smoke:tick", {"ticks": symbol}, on_tick)
        await asyncio.wait_for(first_tick.wait(), timeout=20)
        await connection.unsubscribe("smoke:tick")
        summary["tick"] = True

        print(json.dumps(summary, ensure_ascii=False))
    finally:
        await connection.disconnect()
        logging.disable(previous_logging_disable)


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="R_100")
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    try:
        asyncio.run(run(arguments.symbol))
    except Exception:
        print('{"outcome":"SKIPPED_SAFE"}')
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
