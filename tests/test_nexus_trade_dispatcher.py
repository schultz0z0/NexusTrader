import asyncio
import tempfile
import unittest
from pathlib import Path

from database.repository import DatabaseRepository
from nexus_trade.clock import EntryIntent
from nexus_trade.dispatcher import (
    AccountDispatcher,
    EmergencyStopError,
    LanePositionActiveError,
    OwnershipQuarantineError,
    SharedDemoDispatcher,
    StaleIntentError,
)
from nexus_trade.domain import Lane


class FakeRepository:
    def __init__(self):
        self.intents = {}
        self.transitions = []

    async def create_order_intent(self, data):
        value = {
            **data,
            "state": "prepared",
            "metadata": dict(data.get("metadata") or {}),
        }
        self.intents[value["id"]] = value
        self.transitions.append((value["id"], "prepared"))
        return dict(value)

    async def prepare_nexus_order_intent(self, intent_id, **changes):
        self.intents[intent_id].update(changes)
        return dict(self.intents[intent_id])

    async def update_order_intent(self, intent_id, state, *, error=None, metadata=None):
        value = self.intents[intent_id]
        value["state"] = state
        value["error"] = error
        if metadata is not None:
            value["metadata"] = dict(metadata)
        self.transitions.append((intent_id, state))
        return dict(value)

    async def mark_order_intent_owned(self, intent_id, contract):
        value = self.intents[intent_id]
        value.update(state="owned", contract_id=contract["contract_id"])
        self.transitions.append((intent_id, "owned"))
        return dict(value)


class FailingFinalJournalRepository(FakeRepository):
    async def finalize_nexus_order_intent(self, intent_id, **changes):
        raise RuntimeError("final journal unavailable")


class FakeConnection:
    def __init__(self):
        self.parallel_buy_calls = 0
        self.max_parallel_buy_calls = 0
        self.buy_calls = 0
        self.send_calls = 0
        self.next_contract_id = 100
        self.buy_response = None
        self.buy_exception = None
        self.on_proposal = None
        self.proposal_amounts = []

    async def send(self, request):
        self.send_calls += 1
        if "proposal" in request:
            self.proposal_amounts.append(request["amount"])
            if self.on_proposal is not None:
                self.on_proposal()
            return {
                "proposal": {
                    "id": f"proposal-{request['contract_type']}",
                    "ask_price": request["amount"],
                    "payout": 0.7,
                }
            }
        if "buy" not in request:
            raise AssertionError(f"unexpected request: {request}")
        self.buy_calls += 1
        self.parallel_buy_calls += 1
        self.max_parallel_buy_calls = max(
            self.max_parallel_buy_calls, self.parallel_buy_calls,
        )
        try:
            await asyncio.sleep(0.01)
            if self.buy_exception is not None:
                raise self.buy_exception
            if self.buy_response is not None:
                return self.buy_response
            self.next_contract_id += 1
            return {
                "buy": {
                    "contract_id": self.next_contract_id,
                    "transaction_id": self.next_contract_id + 1000,
                }
            }
        finally:
            self.parallel_buy_calls -= 1


def pending_intent(decision_id, lane, *, prepared_epoch=60.0):
    return EntryIntent(
        decision_id=decision_id,
        contract_type="CALL",
        reason_codes=("center_cross_up",),
        signal_epoch=0,
        target_epoch=60,
        adx=20.0,
        prepared_epoch=prepared_epoch,
        pre_dispatch_epoch=None,
        dispatch_epoch=None,
        accepted_epoch=None,
        entry_delay_ms=None,
        status="PENDING",
        error_code=None,
        contract_id=None,
        lane=lane.value,
    )


class SharedDemoDispatcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repository = FakeRepository()
        self.connection = FakeConnection()
        self.now = 60.25
        self.dispatcher = SharedDemoDispatcher(
            self.connection,
            self.repository,
            account_id="DOT-DEMO",
            epoch_now=lambda: self.now,
        )

    async def test_buys_are_serialized_while_lanes_keep_independent_positions(self):
        champion, trial = await asyncio.gather(
            self.dispatcher.submit(pending_intent("champion-1", Lane.CHAMPION)),
            self.dispatcher.submit(pending_intent("trial-1", Lane.TRIAL)),
        )

        self.assertNotEqual(champion.contract_id, trial.contract_id)
        self.assertEqual(self.connection.max_parallel_buy_calls, 1)
        self.assertEqual(
            self.dispatcher.active_contracts,
            {
                Lane.CHAMPION.value: champion.contract_id,
                Lane.TRIAL.value: trial.contract_id,
            },
        )

    async def test_a_second_position_in_the_same_lane_is_blocked_before_buy(self):
        await self.dispatcher.submit(pending_intent("champion-1", Lane.CHAMPION))

        with self.assertRaises(LanePositionActiveError):
            await self.dispatcher.submit(pending_intent("champion-2", Lane.CHAMPION))

        self.assertEqual(self.connection.buy_calls, 1)

    async def test_stale_intent_is_persisted_without_any_buy(self):
        self.now = 62.001

        with self.assertRaises(StaleIntentError) as raised:
            await self.dispatcher.submit(pending_intent("trial-stale", Lane.TRIAL))

        self.assertEqual(raised.exception.intent.status, "STALE_BEFORE_DISPATCH")
        self.assertEqual(self.connection.buy_calls, 0)
        self.assertEqual(self.connection.send_calls, 0)
        self.assertIn(("nexus-trial-stale", "cancelled"), self.repository.transitions)

    async def test_post_send_exception_enters_correlated_quarantine_without_retry(self):
        self.connection.buy_exception = ConnectionError("response lost")

        with self.assertRaises(OwnershipQuarantineError) as raised:
            await self.dispatcher.submit(pending_intent("trial-lost", Lane.TRIAL))

        self.assertEqual(self.connection.buy_calls, 1)
        self.assertEqual(raised.exception.intent.status, "OWNERSHIP_QUARANTINE")
        self.assertEqual(raised.exception.correlation_id, "nexus-trial-lost")
        stored = self.repository.intents["nexus-trial-lost"]
        self.assertEqual(stored["state"], "reconcile_pending")
        self.assertEqual(stored["metadata"]["correlation_id"], "nexus-trial-lost")

    async def test_malformed_contract_id_is_quarantined_instead_of_coerced_or_retried(self):
        self.connection.buy_response = {"buy": {"contract_id": "123"}}

        with self.assertRaises(OwnershipQuarantineError) as raised:
            await self.dispatcher.submit(pending_intent("bad-contract", Lane.CHAMPION))

        self.assertEqual(raised.exception.intent.status, "OWNERSHIP_QUARANTINE")
        self.assertEqual(self.connection.buy_calls, 1)
        self.assertNotIn(Lane.CHAMPION.value, self.dispatcher.active_contracts)

    async def test_known_contract_keeps_lane_owned_when_final_metadata_write_fails(self):
        repository = FailingFinalJournalRepository()
        dispatcher = SharedDemoDispatcher(
            self.connection,
            repository,
            account_id="DOT-DEMO",
            epoch_now=lambda: self.now,
        )

        with self.assertRaisesRegex(RuntimeError, "final journal"):
            await dispatcher.submit(pending_intent("owned-journal-failure", Lane.CHAMPION))

        with self.assertRaises(LanePositionActiveError):
            await dispatcher.submit(pending_intent("must-stay-blocked", Lane.CHAMPION))
        self.assertEqual(self.connection.buy_calls, 1)

    async def test_emergency_stop_blocks_both_lanes_before_buy(self):
        self.dispatcher.set_emergency_stop(True)

        for lane in Lane:
            with self.assertRaises(EmergencyStopError):
                await self.dispatcher.submit(pending_intent(f"blocked-{lane.value}", lane))

        self.assertEqual(self.connection.buy_calls, 0)

    async def test_emergency_stop_raised_during_quote_blocks_the_following_buy(self):
        self.connection.on_proposal = lambda: self.dispatcher.set_emergency_stop(True)

        with self.assertRaises(EmergencyStopError):
            await self.dispatcher.submit(pending_intent("quote-race", Lane.CHAMPION))

        self.assertEqual(self.connection.buy_calls, 0)

    def test_shared_dispatcher_rejects_non_demo_accounts_and_non_exact_stake(self):
        with self.assertRaises(ValueError):
            SharedDemoDispatcher(
                self.connection, self.repository, account_id="ROT-REAL",
                account_type="real",
            )
        with self.assertRaises(ValueError):
            SharedDemoDispatcher(
                self.connection, self.repository, account_id="DOT-DEMO", stake=0.36,
            )

    async def test_configured_champion_dispatcher_reads_managed_stake_per_entry(self):
        managed = {"stake": 1.0}
        dispatcher = AccountDispatcher(
            self.connection,
            self.repository,
            account_id="DOT-DEMO",
            account_type="demo",
            stake=1.0,
            stake_provider=lambda: managed["stake"],
            epoch_now=lambda: self.now,
        )
        first = await dispatcher.submit(pending_intent("managed-1", Lane.CHAMPION))
        dispatcher.release_position(Lane.CHAMPION, first.contract_id)
        managed["stake"] = 2.0

        await dispatcher.submit(pending_intent("managed-2", Lane.CHAMPION))

        self.assertEqual(self.connection.proposal_amounts, [1.0, 2.0])

    async def test_real_sqlite_journals_preserve_lane_intent_and_restart_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DatabaseRepository(str(Path(temp_dir) / "runtime.db"))
            await repository.init_db()
            snapshot = await repository.get_nexus_runtime_snapshot()
            version_id = snapshot["runtime"]["champion_version_id"]
            decision_id = "sqlite-champion"
            reserved = {
                "upper_break_epoch": None,
                "lower_break_epoch": None,
                "previous_upper": None,
                "previous_lower": None,
                "last_candle_epoch": None,
                "position_status": "RESERVED",
                "owner_decision_id": decision_id,
                "contract_id": None,
                "quarantine_correlation_id": None,
                "reconciliation_id": None,
                "reconciliation_decision_id": None,
                "reconciliation_outcome": None,
            }
            await repository.record_nexus_decision(
                {
                    "id": decision_id,
                    "decision_id": decision_id,
                    "lane": Lane.CHAMPION.value,
                    "signal_epoch": 0,
                },
                nexus_version_id=version_id,
                campaign_id=None,
                state=reserved,
            )
            dispatcher = SharedDemoDispatcher(
                FakeConnection(), repository, account_id="DOT-DEMO",
                epoch_now=lambda: 60.25,
            )
            dispatcher.set_lane_context(
                Lane.CHAMPION,
                nexus_version_id=version_id,
                campaign_id=None,
            )

            receipt = await dispatcher.submit(
                pending_intent(decision_id, Lane.CHAMPION),
            )

            stored = await repository.get_order_intent(f"nexus-{decision_id}")
            self.assertEqual(stored["state"], "owned")
            self.assertEqual(stored["contract_id"], receipt.contract_id)
            self.assertEqual(stored["lane"], Lane.CHAMPION.value)
            self.assertEqual(stored["nexus_version_id"], version_id)
            self.assertEqual(stored["decision_id"], decision_id)
            self.assertEqual(stored["entry_delay_ms"], 250)
            active = {**reserved, "position_status": "ACTIVE", "contract_id": receipt.contract_id}
            await repository.save_nexus_lane_state(Lane.CHAMPION.value, active)
            restored = await repository.load_nexus_lane_states()
            self.assertEqual(restored[Lane.CHAMPION.value], active)


if __name__ == "__main__":
    unittest.main()
