import asyncio
import tempfile
import time
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
from trading.ownership import OrderOwnershipReconciler


class FakeRepository:
    def __init__(self):
        self.intents = {}
        self.transitions = []
        self.submitting_started = None
        self.release_submitting = None

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
        if state == "submitting" and self.submitting_started is not None:
            self.submitting_started.set()
            await self.release_submitting.wait()
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

    async def commit_nexus_known_ownership(
        self, intent_id, contract, *, entry_intent, entry_delay_ms
    ):
        value = self.intents[intent_id]
        value.update(
            state="owned",
            contract_id=contract["contract_id"],
            transaction_id=contract.get("transaction_id"),
            entry_delay_ms=entry_delay_ms,
        )
        value["metadata"] = {
            **value.get("metadata", {}),
            "entry_intent": dict(entry_intent),
            **contract,
        }
        self.transitions.append((intent_id, "owned"))
        return dict(value)


class FailingFinalJournalRepository(FakeRepository):
    async def commit_nexus_known_ownership(self, intent_id, contract, **changes):
        await super().commit_nexus_known_ownership(
            intent_id, contract, **changes,
        )
        raise RuntimeError("final journal unavailable")


class FailingQuarantineRepository(FakeRepository):
    async def update_order_intent(self, intent_id, state, **changes):
        if state == "reconcile_pending":
            raise RuntimeError("quarantine journal unavailable")
        return await super().update_order_intent(intent_id, state, **changes)


class ControlledQuarantineRepository(FakeRepository):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.quarantine_started = asyncio.Event()
        self.release_quarantine = asyncio.Event()

    async def update_order_intent(self, intent_id, state, **changes):
        if state != "reconcile_pending":
            return await super().update_order_intent(intent_id, state, **changes)
        self.quarantine_started.set()
        if self.mode == "failing":
            raise RuntimeError("quarantine write failed")
        if self.mode == "cancelled":
            raise asyncio.CancelledError()
        if self.mode == "blocked":
            await self.release_quarantine.wait()
        return await super().update_order_intent(intent_id, state, **changes)


class StubbornQuarantineRepository(FakeRepository):
    def __init__(self):
        super().__init__()
        self.quarantine_started = asyncio.Event()
        self.cancel_ignored = asyncio.Event()
        self.cleanup = asyncio.Event()

    async def update_order_intent(self, intent_id, state, **changes):
        if state != "reconcile_pending":
            return await super().update_order_intent(intent_id, state, **changes)
        self.quarantine_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancel_ignored.set()
            await self.cleanup.wait()
        return await super().update_order_intent(intent_id, state, **changes)


class NoneContractExecutor:
    async def buy(self, proposal_id, price, passthrough=None):
        return None


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


class ReconciliationConnection:
    def __init__(self, candidates):
        self.candidates = candidates

    async def send(self, request):
        if "portfolio" in request:
            return {"portfolio": {"contracts": []}}
        if "statement" in request:
            return {"statement": {"transactions": list(self.candidates)}}
        raise AssertionError(f"unexpected request: {request}")


class BlockingBuyConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self.buy_started = asyncio.Event()
        self.release_buy = asyncio.Event()

    async def send(self, request):
        if "buy" not in request:
            return await super().send(request)
        self.buy_calls += 1
        self.buy_started.set()
        await self.release_buy.wait()
        return {"buy": {"contract_id": 444, "transaction_id": 1444}}


class BlockingProposalConnection(FakeConnection):
    def __init__(self):
        super().__init__()
        self.proposal_started = asyncio.Event()
        self.release_proposal = asyncio.Event()

    async def send(self, request):
        if "proposal" not in request:
            return await super().send(request)
        self.send_calls += 1
        self.proposal_started.set()
        await self.release_proposal.wait()
        return {"proposal": {"id": "late", "ask_price": 0.35}}


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

    def test_one_contract_id_cannot_be_restored_into_two_lanes(self):
        self.dispatcher.restore_position(Lane.CHAMPION, 9001)

        with self.assertRaisesRegex(ValueError, "already owned"):
            self.dispatcher.restore_position(Lane.TRIAL, 9001)

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

    async def test_cancel_during_buy_persists_quarantine_and_repropagates_cancel(self):
        connection = BlockingBuyConnection()
        dispatcher = SharedDemoDispatcher(
            connection,
            self.repository,
            account_id="DOT-DEMO",
            epoch_now=lambda: self.now,
        )
        submit = asyncio.create_task(
            dispatcher.submit(pending_intent("cancel-buy", Lane.TRIAL)),
        )
        await connection.buy_started.wait()

        submit.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await submit
        stored = self.repository.intents["nexus-cancel-buy"]
        self.assertEqual(stored["state"], "reconcile_pending")
        self.assertEqual(
            stored["metadata"]["correlation_id"], "nexus-cancel-buy",
        )
        self.assertEqual(connection.buy_calls, 1)

    async def test_cancel_before_buy_repropagates_without_ownership_quarantine(self):
        connection = BlockingProposalConnection()
        dispatcher = SharedDemoDispatcher(
            connection,
            self.repository,
            account_id="DOT-DEMO",
            epoch_now=lambda: self.now,
        )
        submit = asyncio.create_task(
            dispatcher.submit(pending_intent("cancel-proposal", Lane.CHAMPION)),
        )
        await connection.proposal_started.wait()

        submit.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await submit
        stored = self.repository.intents["nexus-cancel-proposal"]
        self.assertNotIn(stored["state"], {"reconcile_pending", "ambiguous"})
        self.assertEqual(connection.buy_calls, 0)

    async def test_cancel_during_buy_repropagates_even_if_quarantine_write_fails(self):
        repository = FailingQuarantineRepository()
        connection = BlockingBuyConnection()
        dispatcher = SharedDemoDispatcher(
            connection,
            repository,
            account_id="DOT-DEMO",
            epoch_now=lambda: self.now,
        )
        submit = asyncio.create_task(
            dispatcher.submit(pending_intent("cancel-write-fault", Lane.TRIAL)),
        )
        await connection.buy_started.wait()

        submit.cancel()

        with self.assertRaises(asyncio.CancelledError):
            await submit
        self.assertEqual(connection.buy_calls, 1)

    async def test_malformed_contract_id_is_quarantined_instead_of_coerced_or_retried(self):
        self.connection.buy_response = {"buy": {"contract_id": "123"}}

        with self.assertRaises(OwnershipQuarantineError) as raised:
            await self.dispatcher.submit(pending_intent("bad-contract", Lane.CHAMPION))

        self.assertEqual(raised.exception.intent.status, "OWNERSHIP_QUARANTINE")
        self.assertEqual(self.connection.buy_calls, 1)
        self.assertNotIn(Lane.CHAMPION.value, self.dispatcher.active_contracts)

    async def test_post_transport_quarantine_write_outcomes_never_release_lane(self):
        cases = (
            ("exception", "failing"),
            ("exception", "cancelled"),
            ("exception", "blocked"),
            ("malformed", "failing"),
            ("malformed", "cancelled"),
            ("malformed", "blocked"),
        )
        for index, (response_kind, write_mode) in enumerate(cases):
            with self.subTest(response_kind=response_kind, write_mode=write_mode):
                repository = ControlledQuarantineRepository(write_mode)
                connection = FakeConnection()
                if response_kind == "exception":
                    connection.buy_exception = ConnectionError("response lost")
                else:
                    connection.buy_response = {"buy": {"contract_id": "invalid"}}
                dispatcher = SharedDemoDispatcher(
                    connection,
                    repository,
                    account_id="DOT-DEMO",
                    epoch_now=lambda: self.now,
                )
                dispatcher._quarantine_write_timeout = 0.02
                dispatcher._quarantine_cancel_timeout = 0.02

                submit = asyncio.create_task(dispatcher.submit(
                    pending_intent(f"post-transport-{index}", Lane.TRIAL),
                ))
                done, _ = await asyncio.wait({submit}, timeout=0.15)
                if submit not in done:
                    repository.release_quarantine.set()
                    submit.cancel()
                    await asyncio.gather(submit, return_exceptions=True)
                    self.fail("post-transport quarantine persistence exceeded its bound")
                with self.assertRaises(OwnershipQuarantineError):
                    await submit
                with self.assertRaises(LanePositionActiveError):
                    await dispatcher.submit(
                        pending_intent(f"blocked-again-{index}", Lane.TRIAL),
                    )
                self.assertEqual(connection.buy_calls, 1)

    async def test_none_contract_with_failed_quarantine_write_keeps_lane_reserved(self):
        repository = ControlledQuarantineRepository("failing")
        connection = FakeConnection()
        dispatcher = SharedDemoDispatcher(
            connection,
            repository,
            account_id="DOT-DEMO",
            epoch_now=lambda: self.now,
            executor=NoneContractExecutor(),
        )

        with self.assertRaises(OwnershipQuarantineError):
            await dispatcher.submit(pending_intent("none-contract", Lane.CHAMPION))

        with self.assertRaises(LanePositionActiveError):
            await dispatcher.submit(pending_intent("none-contract-again", Lane.CHAMPION))

    async def test_quarantine_persistence_is_strictly_bounded_when_writer_ignores_cancel(self):
        repository = StubbornQuarantineRepository()
        connection = FakeConnection()
        connection.buy_exception = ConnectionError("response lost")
        dispatcher = SharedDemoDispatcher(
            connection,
            repository,
            account_id="DOT-DEMO",
            epoch_now=lambda: self.now,
        )
        dispatcher._quarantine_write_timeout = 0.02
        dispatcher._quarantine_cancel_timeout = 0.02
        started = time.perf_counter()
        submit = asyncio.create_task(
            dispatcher.submit(pending_intent("stubborn-write", Lane.TRIAL)),
        )

        done, _ = await asyncio.wait({submit}, timeout=0.15)
        elapsed = time.perf_counter() - started
        if submit not in done:
            repository.cleanup.set()
            submit.cancel()
            await asyncio.gather(submit, return_exceptions=True)
            self.fail("submit waited indefinitely for a cancellation-resistant writer")
        with self.assertRaises(OwnershipQuarantineError):
            await submit
        self.assertLess(elapsed, 0.15)
        self.assertTrue(repository.cancel_ignored.is_set())
        with self.assertRaises(LanePositionActiveError):
            await dispatcher.submit(
                pending_intent("stubborn-write-again", Lane.TRIAL),
            )

        repository.cleanup.set()
        for _ in range(20):
            if not dispatcher._quarantine_persistence_tasks:
                break
            await asyncio.sleep(0)
        self.assertEqual(dispatcher._quarantine_persistence_tasks, set())

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

    async def test_stop_while_submitting_journal_is_blocked_prevents_transport(self):
        self.repository.submitting_started = asyncio.Event()
        self.repository.release_submitting = asyncio.Event()
        submit = asyncio.create_task(
            self.dispatcher.submit(pending_intent("blocked-journal-stop", Lane.TRIAL)),
        )
        await self.repository.submitting_started.wait()

        self.dispatcher.set_emergency_stop(True)
        self.repository.release_submitting.set()

        with self.assertRaises(EmergencyStopError):
            await submit
        self.assertEqual(self.connection.buy_calls, 0)

    async def test_stale_while_submitting_journal_is_blocked_prevents_transport(self):
        self.repository.submitting_started = asyncio.Event()
        self.repository.release_submitting = asyncio.Event()
        submit = asyncio.create_task(
            self.dispatcher.submit(pending_intent("blocked-journal-stale", Lane.CHAMPION)),
        )
        await self.repository.submitting_started.wait()

        self.now = 62.001
        self.repository.release_submitting.set()

        with self.assertRaises(StaleIntentError):
            await submit
        self.assertEqual(self.connection.buy_calls, 0)

    async def test_reconciliation_never_claims_other_lane_candidate(self):
        confirmed_id = "nexus-confirmed-champion"
        candidate = {
            "contract_id": 7001,
            "action": "buy",
            "symbol": "R_100",
            "contract_type": "CALL",
            "buy_price": 0.35,
            "transaction_time": 60,
            "passthrough": {
                "order_intent_id": confirmed_id,
                "decision_id": "confirmed-champion",
                "lane": Lane.CHAMPION.value,
            },
        }
        lost_id = "nexus-lost-trial"
        self.repository.intents[lost_id] = {
            "id": lost_id,
            "decision_id": "lost-trial",
            "lane": Lane.TRIAL.value,
            "symbol": "R_100",
            "contract_type": "CALL",
            "price": 0.35,
            "signal_epoch": 60,
            "entry_delay_ms": None,
            "state": "reconcile_pending",
            "metadata": {
                "correlation_id": lost_id,
                "entry_intent": {"decision_id": "lost-trial"},
            },
        }
        reconciler = OrderOwnershipReconciler(
            ReconciliationConnection([candidate]),
            self.repository,
        )

        result = await reconciler.reconcile(self.repository.intents[lost_id])

        self.assertIsNone(result)
        self.assertEqual(self.repository.intents[lost_id]["state"], "reconcile_pending")
        self.assertIsNone(self.repository.intents[lost_id].get("contract_id"))

    async def test_reconciliation_requires_both_exact_correlation_fields(self):
        correlation_id = "nexus-lost-trial"
        intent = {
            "id": correlation_id,
            "decision_id": "lost-trial",
            "lane": Lane.TRIAL.value,
            "symbol": "R_100",
            "contract_type": "CALL",
            "price": 0.35,
            "signal_epoch": 60,
            "state": "reconcile_pending",
            "metadata": {
                "correlation_id": correlation_id,
                "entry_intent": {"decision_id": "lost-trial"},
            },
        }
        self.repository.intents[correlation_id] = intent
        missing_decision = {
            "contract_id": 7002,
            "action": "buy",
            "symbol": "R_100",
            "contract_type": "CALL",
            "buy_price": 0.35,
            "transaction_time": 60,
            "passthrough": {"order_intent_id": correlation_id},
        }
        reconciler = OrderOwnershipReconciler(
            ReconciliationConnection([missing_decision]), self.repository,
        )

        self.assertIsNone(await reconciler.reconcile(intent))

    async def test_reconciliation_rejects_non_exact_contract_id_types(self):
        correlation_id = "nexus-strict-contract"
        intent = {
            "id": correlation_id,
            "decision_id": "strict-contract",
            "lane": Lane.TRIAL.value,
            "symbol": "R_100",
            "contract_type": "CALL",
            "price": 0.35,
            "signal_epoch": 60,
            "entry_delay_ms": None,
            "state": "reconcile_pending",
            "metadata": {"correlation_id": correlation_id},
        }
        self.repository.intents[correlation_id] = intent
        for malformed in (True, "7002", 7002.0):
            with self.subTest(contract_id=malformed):
                candidate = {
                    "contract_id": malformed,
                    "action": "buy",
                    "symbol": "R_100",
                    "contract_type": "CALL",
                    "buy_price": 0.35,
                    "passthrough": {
                        "order_intent_id": correlation_id,
                        "decision_id": "strict-contract",
                    },
                }
                reconciler = OrderOwnershipReconciler(
                    ReconciliationConnection([candidate]), self.repository,
                )
                self.assertIsNone(await reconciler.reconcile(intent))

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
            restored = await repository.load_nexus_lane_states()
            self.assertEqual(
                restored[Lane.CHAMPION.value],
                {
                    **reserved,
                    "position_status": "ACTIVE",
                    "contract_id": receipt.contract_id,
                },
            )
            owners = await repository.load_nexus_lane_owners()
            self.assertEqual(owners[Lane.CHAMPION.value], {
                "account_id": "DOT-DEMO",
                "account_type": "demo",
                "management_active": False,
            })

    async def test_known_ownership_and_lane_active_rollback_together(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DatabaseRepository(str(Path(temp_dir) / "runtime.db"))
            await repository.init_db()
            snapshot = await repository.get_nexus_runtime_snapshot()
            version_id = snapshot["runtime"]["champion_version_id"]
            decision_id = "atomic-fault"
            reserved = {
                "position_status": "RESERVED",
                "owner_decision_id": decision_id,
                "contract_id": None,
            }
            await repository.record_nexus_decision(
                {
                    "id": decision_id,
                    "decision_id": decision_id,
                    "lane": Lane.CHAMPION.value,
                    "signal_epoch": 0,
                },
                nexus_version_id=version_id,
                state=reserved,
            )
            async with repository._connection() as db:
                await db.execute(
                    """
                    CREATE TRIGGER fail_nexus_lane_activation
                    BEFORE UPDATE ON nexus_decisions
                    BEGIN
                        SELECT RAISE(ABORT, 'fault after owned update');
                    END
                    """,
                )
                await db.commit()
            connection = FakeConnection()
            dispatcher = SharedDemoDispatcher(
                connection,
                repository,
                account_id="DOT-DEMO",
                epoch_now=lambda: 60.25,
            )
            dispatcher.set_lane_context(
                Lane.CHAMPION,
                nexus_version_id=version_id,
                campaign_id=None,
            )

            with self.assertRaisesRegex(Exception, "fault after owned update"):
                await dispatcher.submit(pending_intent(decision_id, Lane.CHAMPION))

            self.assertEqual(connection.buy_calls, 1)
            stored = await repository.get_order_intent(f"nexus-{decision_id}")
            self.assertEqual(stored["state"], "submitting")
            self.assertIsNone(stored["contract_id"])
            restored = await repository.load_nexus_lane_states()
            self.assertEqual(
                restored[Lane.CHAMPION.value]["position_status"], "RESERVED",
            )
            with self.assertRaises(LanePositionActiveError):
                await dispatcher.submit(
                    pending_intent("atomic-fault-duplicate", Lane.CHAMPION),
                )

    async def test_settlement_writes_latest_idle_snapshot_before_restart(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = str(Path(temp_dir) / "runtime.db")
            repository = DatabaseRepository(db_path)
            await repository.init_db()
            snapshot = await repository.get_nexus_runtime_snapshot()
            version_id = snapshot["runtime"]["champion_version_id"]
            decision_id = "settlement-latest"
            owner = {
                "account_id": "DOT-DEMO",
                "account_type": "demo",
                "management_active": False,
            }
            active_state = {
                "position_status": "RESERVED",
                "owner_decision_id": decision_id,
                "contract_id": None,
            }
            await repository.record_nexus_decision(
                {
                    "id": decision_id,
                    "decision_id": decision_id,
                    "lane": Lane.CHAMPION.value,
                    "signal_epoch": 0,
                },
                nexus_version_id=version_id,
                state=active_state,
                owner=owner,
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
            active_state = {
                "position_status": "ACTIVE",
                "owner_decision_id": decision_id,
                "contract_id": receipt.contract_id,
            }
            for index in range(2):
                later_id = f"zz-later-active-{index}"
                await repository.record_nexus_decision(
                    {
                        "id": later_id,
                        "decision_id": later_id,
                        "lane": Lane.CHAMPION.value,
                        "signal_epoch": 61 + index,
                    },
                    nexus_version_id=version_id,
                    state=active_state,
                    owner=owner,
                )

            await repository.settle_nexus_trade_and_lane(
                {
                    "bot_id": "nexus-trade",
                    "session_id": None,
                    "strategy_name": "nexus_trade",
                    "symbol": "R_100",
                    "contract_type": "CALL",
                    "contract_id": receipt.contract_id,
                    "stake": 0.35,
                    "payout": 0.67,
                    "profit": 0.32,
                    "result": "won",
                    "status": "closed",
                    "lane": Lane.CHAMPION.value,
                    "nexus_version_id": version_id,
                    "campaign_id": None,
                    "decision_id": decision_id,
                },
                lane_state={
                    "position_status": "IDLE",
                    "owner_decision_id": None,
                    "contract_id": None,
                },
                apply_risk=False,
                money_management="fixed",
                money_config={},
                risk_config={},
                initial_stake=0.35,
                settled_epoch=120.0,
                owner=owner,
            )

            restarted = DatabaseRepository(db_path)
            states = await restarted.load_nexus_lane_states()
            owners = await restarted.load_nexus_lane_owners()
            self.assertEqual(states[Lane.CHAMPION.value]["position_status"], "IDLE")
            self.assertIsNone(states[Lane.CHAMPION.value]["owner_decision_id"])
            self.assertIsNone(states[Lane.CHAMPION.value]["contract_id"])
            self.assertIsNone(owners[Lane.CHAMPION.value])

            next_decision = "after-settlement"
            await restarted.record_nexus_decision(
                    {
                        "id": next_decision,
                    "decision_id": next_decision,
                    "lane": Lane.CHAMPION.value,
                        "signal_epoch": 0,
                },
                nexus_version_id=version_id,
                state={
                    "position_status": "RESERVED",
                    "owner_decision_id": next_decision,
                    "contract_id": None,
                },
                owner=owner,
            )
            next_connection = FakeConnection()
            next_connection.next_contract_id = 200
            next_dispatcher = SharedDemoDispatcher(
                next_connection, restarted, account_id="DOT-DEMO",
                epoch_now=lambda: 60.25,
            )
            next_dispatcher.set_lane_context(
                Lane.CHAMPION,
                nexus_version_id=version_id,
                campaign_id=None,
            )

            next_receipt = await next_dispatcher.submit(
                pending_intent(next_decision, Lane.CHAMPION),
            )
            self.assertEqual(next_receipt.contract_id, 201)

    async def test_settlement_transaction_rolls_back_trade_risk_and_lane(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DatabaseRepository(str(Path(temp_dir) / "runtime.db"))
            await repository.init_db()
            snapshot = await repository.get_nexus_runtime_snapshot()
            version_id = snapshot["runtime"]["champion_version_id"]
            decision_id = "settlement-fault"
            reserved = {
                "position_status": "RESERVED",
                "owner_decision_id": decision_id,
                "contract_id": None,
            }
            await repository.record_nexus_decision(
                {
                    "id": decision_id,
                    "decision_id": decision_id,
                    "lane": Lane.CHAMPION.value,
                    "signal_epoch": 0,
                },
                nexus_version_id=version_id,
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
            owner = {
                "account_id": "DOT-DEMO",
                "account_type": "demo",
                "management_active": False,
            }
            for index in range(2):
                later_id = f"settlement-fault-later-{index}"
                await repository.record_nexus_decision(
                    {
                        "id": later_id,
                        "decision_id": later_id,
                        "lane": Lane.CHAMPION.value,
                        "signal_epoch": 61 + index,
                    },
                    nexus_version_id=version_id,
                    state={
                        "position_status": "ACTIVE",
                        "owner_decision_id": decision_id,
                        "contract_id": receipt.contract_id,
                    },
                    owner=owner,
                )
            async with repository._connection() as db:
                await db.execute(
                    """
                    CREATE TRIGGER fail_nexus_settlement
                    BEFORE INSERT ON nexus_decisions
                    WHEN NEW.id LIKE 'settlement:%'
                    BEGIN
                        SELECT RAISE(ABORT, 'settlement commit fault');
                    END
                    """,
                )
                await db.commit()

            with self.assertRaisesRegex(Exception, "settlement commit fault"):
                await repository.settle_nexus_trade_and_lane(
                    {
                        "bot_id": "nexus-trade",
                        "session_id": None,
                        "strategy_name": "nexus_trade",
                        "symbol": "R_100",
                        "contract_type": "CALL",
                        "contract_id": receipt.contract_id,
                        "stake": 0.35,
                        "payout": 0.0,
                        "profit": -0.35,
                        "result": "lost",
                        "status": "closed",
                        "lane": Lane.CHAMPION.value,
                        "nexus_version_id": version_id,
                        "campaign_id": None,
                        "decision_id": decision_id,
                    },
                    lane_state={"position_status": "IDLE"},
                    apply_risk=True,
                    money_management="martingale",
                    money_config={"multiplier": 2, "max_levels": 3},
                    risk_config={},
                    initial_stake=0.35,
                    settled_epoch=120.0,
                    owner=owner,
                )

            self.assertEqual(await repository.list_trades("nexus-trade"), [])
            restored = await repository.load_nexus_lane_states()
            self.assertEqual(
                restored[Lane.CHAMPION.value]["position_status"], "ACTIVE",
            )
            intent = await repository.get_order_intent(f"nexus-{decision_id}")
            self.assertEqual(intent["state"], "owned")
            owners = await repository.load_nexus_lane_owners()
            self.assertEqual(owners[Lane.CHAMPION.value], {
                "account_id": "DOT-DEMO",
                "account_type": "demo",
                "management_active": False,
            })

    async def test_ambiguous_metadata_merge_preserves_nexus_owner_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = DatabaseRepository(str(Path(temp_dir) / "runtime.db"))
            await repository.init_db()
            snapshot = await repository.get_nexus_runtime_snapshot()
            version_id = snapshot["runtime"]["champion_version_id"]
            await repository.record_nexus_decision(
                {
                    "id": "metadata-owner",
                    "decision_id": "metadata-owner",
                    "lane": Lane.CHAMPION.value,
                    "signal_epoch": 60,
                },
                nexus_version_id=version_id,
                state={
                    "position_status": "RESERVED",
                    "owner_decision_id": "metadata-owner",
                    "contract_id": None,
                },
            )
            connection = FakeConnection()
            connection.buy_exception = ConnectionError("response lost")
            dispatcher = AccountDispatcher(
                connection,
                repository,
                account_id="DEMO-A",
                account_type="demo",
                stake=0.35,
                management_active=True,
                epoch_now=lambda: 60.25,
            )
            dispatcher.set_lane_context(
                Lane.CHAMPION,
                nexus_version_id=version_id,
                campaign_id=None,
            )
            with self.assertRaises(OwnershipQuarantineError):
                await dispatcher.submit(
                    pending_intent("metadata-owner", Lane.CHAMPION),
                )

            await repository.update_order_intent(
                "nexus-metadata-owner",
                "ambiguous",
                metadata={
                    "candidate_contract_ids": [11, 12],
                    "correlation_id": "attacker-correlation",
                    "order_intent_id": "attacker-intent",
                    "decision_id": "attacker-decision",
                    "account_id": "ATTACKER",
                    "account_type": "real",
                    "management_active": False,
                    "entry_intent": {
                        "decision_id": "attacker-decision",
                        "lane": Lane.TRIAL.value,
                    },
                },
            )

            stored = await repository.get_order_intent("nexus-metadata-owner")
            metadata = stored["metadata"]
            self.assertEqual(metadata["correlation_id"], "nexus-metadata-owner")
            self.assertEqual(metadata["account_id"], "DEMO-A")
            self.assertEqual(metadata["account_type"], "demo")
            self.assertTrue(metadata["management_active"])
            self.assertEqual(
                metadata["entry_intent"]["decision_id"], "metadata-owner",
            )
            self.assertEqual(
                metadata["entry_intent"]["lane"], Lane.CHAMPION.value,
            )
            self.assertEqual(metadata["candidate_contract_ids"], [11, 12])


if __name__ == "__main__":
    unittest.main()
