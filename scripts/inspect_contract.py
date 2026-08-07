"""Read-only, allowlisted Deriv contract inspection for settlement incidents."""

import argparse
import asyncio
import json

from core.auth import AuthManager
from core.connection import NexusConnection
from database.repository import DatabaseRepository


SAFE_FIELDS = {
    "contract_id", "contract_type", "underlying", "status", "is_sold",
    "is_expired", "is_settleable", "profit", "payout", "buy_price",
    "entry_spot", "exit_spot", "current_spot", "purchase_time",
    "date_expiry", "date_settlement",
}


async def inspect(account_id, contract_id, database=None, apply_closed=False):
    auth = AuthManager()
    connection = NexusConnection(auth)
    try:
        if not await connection.connect(account_id):
            raise RuntimeError("Nao foi possivel abrir a conexao autenticada")
        response = await connection.send({
            "proposal_open_contract": 1,
            "contract_id": int(contract_id),
        })
        if response.get("error"):
            raise RuntimeError(response["error"].get("message", str(response["error"])))
        contract = response.get("proposal_open_contract") or {}
        sanitized = {key: contract.get(key) for key in SAFE_FIELDS if key in contract}
        print(json.dumps(sanitized, sort_keys=True))
        if apply_closed:
            if not database:
                raise RuntimeError("--database e obrigatorio com --apply-closed")
            if contract.get("is_sold") != 1:
                raise RuntimeError("A Deriv ainda nao confirmou is_sold=1")
            repository = DatabaseRepository(database)
            await repository.init_db()
            matches = []
            for bot in await repository.list_bots():
                if bot.get("account_id") != account_id:
                    continue
                for trade in await repository.list_trades(bot["id"], limit=1000):
                    if int(trade.get("contract_id") or 0) == int(contract_id):
                        matches.append((bot, trade))
            if len(matches) != 1:
                raise RuntimeError("Contrato nao possui ownership local unico")
            bot, stored = matches[0]
            if bot.get("account_type") != "demo":
                raise RuntimeError("Aplicacao manual permitida somente em conta DEMO")
            if bot.get("desired_state") != "STOPPED":
                raise RuntimeError("Pare o bot antes de aplicar reconciliacao manual")
            trade = {
                **stored,
                "strategy_name": stored.get("strategy_name") or "DonchianZigZag(21, 1.0)",
                "symbol": contract.get("underlying") or stored.get("symbol"),
                "contract_type": contract.get("contract_type") or stored.get("contract_type"),
                "stake": float(contract.get("buy_price") or stored.get("stake") or 0),
                "payout": float(contract.get("payout") or 0),
                "profit": float(contract.get("profit") or 0),
                "result": contract.get("status") or "closed",
                "status": "closed",
                "entry_spot": contract.get("entry_spot"),
                "exit_spot": contract.get("exit_spot") or contract.get("current_spot"),
                "purchase_time": contract.get("purchase_time"),
                "expiry_time": contract.get("date_expiry"),
            }
            result = await repository.settle_trade_and_risk(
                trade,
                money_management=bot.get("money_management", "fixed"),
                money_config=bot.get("money_config") or {},
                risk_config=bot.get("risk_config") or {},
                initial_stake=float(bot.get("initial_stake", 1.0)),
                settled_epoch=float(contract.get("date_settlement") or contract.get("date_expiry") or 0),
            )
            print(json.dumps({"local_reconciliation": result}, sort_keys=True))
    finally:
        await connection.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--contract-id", required=True, type=int)
    parser.add_argument("--database")
    parser.add_argument("--apply-closed", action="store_true")
    args = parser.parse_args()
    asyncio.run(inspect(
        args.account_id,
        args.contract_id,
        database=args.database,
        apply_closed=args.apply_closed,
    ))
