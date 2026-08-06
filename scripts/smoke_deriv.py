"""Smoke test for the current Deriv REST + OTP + WebSocket flow (demo only)."""

import argparse
import asyncio
import json

from core.auth import AuthManager
from core.connection import NexusConnection
from trading.executor import OrderExecutor
from trading.monitor import ContractMonitor
from trading.proposal import ProposalManager
from trading.safety import ensure_demo_account


def account_id(account):
    return account.get("account_id") or account.get("loginid") or ""


def is_demo(account):
    identifier = account_id(account).upper()
    kind = str(account.get("account_type") or account.get("type") or "").lower()
    return (
        kind in {"demo", "virtual"}
        or account.get("is_virtual") in {1, True, "1", "true"}
        or identifier.startswith("VRTC")
    )


async def run(execute_trade=False, symbol="R_100", stake=1.0):
    auth = AuthManager()
    connection = NexusConnection(auth, reconnect_delays=(1, 2, 4))
    summary = {"rest": False, "websocket": False, "history": False, "tick": False, "trade": None}
    try:
        accounts = await auth.list_accounts()
        demos = [item for item in accounts if is_demo(item)]
        if not demos:
            raise RuntimeError("Nenhuma conta demo encontrada para este token")
        selected = demos[0]
        selected_id = account_id(selected)
        ensure_demo_account("demo")
        summary["rest"] = True
        summary["demo_account"] = selected_id

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

        if execute_trade:
            proposal = await ProposalManager(connection).request_proposal(
                symbol=symbol,
                contract_type="CALL",
                stake=stake,
                duration=5,
                duration_unit="t",
            )
            if not proposal:
                raise RuntimeError("Proposal demo nao retornada")
            bought = await OrderExecutor(connection, account_type="demo").buy(
                proposal["id"], proposal["ask_price"]
            )
            if not bought:
                raise RuntimeError("Compra demo rejeitada")
            settled = asyncio.get_running_loop().create_future()

            async def on_settled(contract):
                if not settled.done():
                    settled.set_result(contract)

            await ContractMonitor(connection).monitor_contract(bought["contract_id"], on_settled)
            result = await asyncio.wait_for(settled, timeout=120)
            summary["trade"] = {
                "contract_id": result.get("contract_id"),
                "status": result.get("status"),
                "profit": result.get("profit"),
            }
        print(json.dumps(summary, ensure_ascii=False))
    finally:
        await connection.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trade", action="store_true", help="Compra um contrato CALL de 5 ticks na demo")
    parser.add_argument("--symbol", default="R_100")
    parser.add_argument("--stake", type=float, default=1.0)
    arguments = parser.parse_args()
    asyncio.run(run(arguments.trade, arguments.symbol, arguments.stake))
