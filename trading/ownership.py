"""Durable ownership for Deriv buys whose response may be lost in transport."""

from collections import deque

from database.repository import ActiveOrderIntentError
from trading.executor import AmbiguousBuyError, OrderExecutor
from utils.logger import setup_logger

logger = setup_logger("OrderOwnership")


class BuyTransactionTracker:
    """Keeps a bounded account transaction stream for response-loss recovery."""

    def __init__(self, connection, limit=200):
        self.connection = connection
        self._transactions = deque(maxlen=int(limit))

    async def start(self):
        if not hasattr(self.connection, "subscribe"):
            logger.warning("Conexao sem transaction stream; reconciliation usara backfill")
            return
        await self.connection.subscribe(
            "ownership:transactions",
            {"transaction": 1},
            self._on_message,
        )

    async def _on_message(self, response):
        transaction = response.get("transaction") or {}
        action = str(
            transaction.get("action_type") or transaction.get("action") or ""
        ).lower()
        if transaction.get("contract_id") is not None and action in {"buy", "purchase"}:
            self._transactions.append(dict(transaction))

    def snapshot(self):
        return list(self._transactions)


class OrderOwnershipReconciler:
    def __init__(
        self,
        connection,
        repository,
        time_tolerance_seconds=300,
        transaction_source=None,
    ):
        self.connection = connection
        self.repository = repository
        self.time_tolerance_seconds = int(time_tolerance_seconds)
        self.transaction_source = transaction_source or (lambda: [])

    async def reconcile(self, intent: dict):
        if not intent:
            return None
        portfolio_response = await self.connection.send({"portfolio": 1})
        statement_response = await self.connection.send({
            "statement": 1,
            "description": 1,
            "limit": 100,
        })
        candidates = []
        candidates.extend(self.transaction_source())
        candidates.extend(
            (portfolio_response.get("portfolio") or {}).get("contracts") or []
        )
        candidates.extend(
            (statement_response.get("statement") or {}).get("transactions") or []
        )
        matches = {}
        for candidate in candidates:
            if self._matches(intent, candidate):
                normalized = self._normalize(candidate, intent)
                matches[normalized["contract_id"]] = normalized

        if len(matches) == 1:
            contract = next(iter(matches.values()))
            if (
                intent.get("decision_id") is not None
                and intent.get("lane") is not None
                and hasattr(self.repository, "commit_nexus_known_ownership")
            ):
                metadata = intent.get("metadata") or {}
                await self.repository.commit_nexus_known_ownership(
                    intent["id"],
                    contract,
                    entry_intent=dict(metadata.get("entry_intent") or {}),
                    entry_delay_ms=int(intent["entry_delay_ms"] or 0),
                )
            else:
                await self.repository.mark_order_intent_owned(intent["id"], contract)
            logger.warning(
                "Ownership recuperado para intent %s / contrato %s",
                intent["id"],
                contract["contract_id"],
            )
            return contract
        if len(matches) > 1:
            await self.repository.update_order_intent(
                intent["id"],
                "ambiguous",
                error="Multiplos contratos correspondem a compra; revisao manual obrigatoria",
                metadata={"candidate_contract_ids": sorted(matches)},
            )
            return None
        await self.repository.update_order_intent(
            intent["id"],
            "reconcile_pending",
            error=intent.get("error") or "Compra ainda nao encontrada no portfolio/statement",
        )
        return None

    def _matches(self, intent, candidate):
        contract_id = candidate.get("contract_id")
        if type(contract_id) is not int or contract_id <= 0:
            return False
        # NexusTrade has concurrent lanes with deliberately identical orders.
        # Price/time/contract shape therefore cannot establish ownership. Its
        # candidates must carry both pieces of the passthrough identity.
        if intent.get("decision_id") is not None or intent.get("lane") is not None:
            evidence = candidate.get("passthrough")
            if not isinstance(evidence, dict):
                evidence = candidate.get("metadata")
            if not isinstance(evidence, dict):
                evidence = {}
            candidate_correlation = (
                candidate.get("correlation_id")
                or candidate.get("order_intent_id")
                or evidence.get("correlation_id")
                or evidence.get("order_intent_id")
            )
            candidate_decision = (
                candidate.get("decision_id") or evidence.get("decision_id")
            )
            if (
                candidate_correlation != intent.get("id")
                or candidate_decision != intent.get("decision_id")
            ):
                return False
        action = str(candidate.get("action_type") or candidate.get("action") or "buy").lower()
        if action not in {"buy", "purchase"}:
            return False
        candidate_symbol = candidate.get("symbol") or candidate.get("underlying")
        if candidate_symbol and candidate_symbol != intent["symbol"]:
            return False
        candidate_type = candidate.get("contract_type")
        if candidate_type and str(candidate_type).upper() != str(intent["contract_type"]).upper():
            return False
        candidate_time = (
            candidate.get("transaction_time")
            or candidate.get("purchase_time")
            or candidate.get("date_start")
        )
        expected_time = int(intent.get("signal_epoch") or 0)
        if candidate_time and expected_time:
            if abs(int(candidate_time) - expected_time) > self.time_tolerance_seconds:
                return False
        candidate_price = candidate.get("buy_price")
        if candidate_price is None and candidate.get("amount") is not None:
            candidate_price = abs(float(candidate["amount"]))
        if candidate_price is not None:
            expected_price = float(intent.get("price") or intent.get("stake") or 0)
            if abs(float(candidate_price) - expected_price) > 0.011:
                return False
        return True

    @staticmethod
    def _normalize(candidate, intent):
        return {
            **candidate,
            "contract_id": candidate["contract_id"],
            "transaction_id": candidate.get("transaction_id"),
            "contract_type": candidate.get("contract_type") or intent["contract_type"],
            "underlying": candidate.get("underlying") or candidate.get("symbol") or intent["symbol"],
            "buy_price": float(
                candidate.get("buy_price")
                if candidate.get("buy_price") is not None
                else intent["price"]
            ),
        }


class OrderOwnershipCoordinator:
    def __init__(self, connection, repository, account_type="demo"):
        self.connection = connection
        self.repository = repository
        self.executor = OrderExecutor(connection, account_type=account_type)
        self.transaction_tracker = BuyTransactionTracker(connection)
        self.reconciler = OrderOwnershipReconciler(
            connection,
            repository,
            transaction_source=self.transaction_tracker.snapshot,
        )

    async def start(self):
        await self.transaction_tracker.start()

    async def buy(self, intent_data: dict):
        intent = await self.repository.create_order_intent(intent_data)
        await self.repository.update_order_intent(intent["id"], "submitting")
        try:
            contract = await self.executor.buy(
                intent["proposal_id"],
                intent["price"],
                passthrough={
                    "order_intent_id": intent["id"],
                    "bot_id": intent["bot_id"],
                },
            )
        except AmbiguousBuyError as exc:
            pending = await self.repository.update_order_intent(
                intent["id"], "reconcile_pending", error=str(exc)
            )
            return await self.reconciler.reconcile(pending)
        if not contract or contract.get("contract_id") is None:
            await self.repository.update_order_intent(
                intent["id"], "rejected", error="Compra rejeitada pela Deriv"
            )
            return None
        await self.repository.mark_order_intent_owned(intent["id"], contract)
        return contract

    async def reconcile_pending(self, bot_id: str):
        resolved = []
        intents = await self.repository.list_unresolved_order_intents(bot_id=bot_id)
        for intent in intents:
            if intent["state"] == "prepared":
                await self.repository.update_order_intent(
                    intent["id"],
                    "cancelled",
                    error="Processo encerrou antes da transmissao da compra",
                )
                continue
            if intent["state"] == "ambiguous":
                continue
            contract = await self.reconciler.reconcile(intent)
            if contract:
                resolved.append((intent, contract))
        return resolved


__all__ = [
    "ActiveOrderIntentError",
    "BuyTransactionTracker",
    "OrderOwnershipCoordinator",
    "OrderOwnershipReconciler",
]
