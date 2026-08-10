import asyncio
import unittest

from config.settings import settings
from api.live_store import LiveStore
from nexus_trade.clock import CausalCycleResult, DispatchReceipt, EntryIntent
from nexus_trade.domain import Lane
from nexus_trade.runtime import NexusTradeRuntime
from nexus_trade.strategy import (
    Decision,
    NexusTradeStrategy,
    OwnershipReconciliation,
    SetupState,
)
from trading.safety import RealTradingDisabled


class FakeRepository:
    def __init__(self):
        self.decisions = []
        self.lane_states = {}
        self.heartbeats = 0
        self.trades = []
        self.restored_states = {}
        self.restored_owners = {}
        self.risk_settlements = []
        self.recovery_intents = []
        self.runtime_snapshot = None
        self.risk_state = None
        self.fail_atomic_settlement = False

    async def record_nexus_decision(
        self, decision, *, nexus_version_id, campaign_id, state, owner=None,
    ):
        self.decisions.append({
            "decision": decision,
            "nexus_version_id": nexus_version_id,
            "campaign_id": campaign_id,
            "state": state,
            "owner": dict(owner) if owner else None,
        })

    async def save_nexus_lane_state(self, lane, state, owner=None):
        self.lane_states[lane] = dict(state)
        self.restored_owners[lane] = dict(owner) if owner else None

    async def touch_bot_heartbeat(self, bot_id):
        self.heartbeats += 1

    async def load_nexus_lane_states(self):
        return {lane: dict(state) for lane, state in self.restored_states.items()}

    async def load_nexus_lane_owners(self):
        return {
            lane: dict(owner) if owner else None
            for lane, owner in self.restored_owners.items()
        }

    async def list_nexus_recovery_intents(self, bot_id):
        return [dict(intent) for intent in self.recovery_intents]

    async def get_nexus_runtime_snapshot(self):
        return self.runtime_snapshot

    async def get_risk_state(self, bot_id, initial_stake=1.0):
        return self.risk_state or {
            "current_stake": initial_stake,
            "current_level": 0,
            "consecutive_wins": 0,
            "consecutive_losses": 0,
            "circuit_consecutive_losses": 0,
            "circuit_tripped_at": 0.0,
        }

    async def settle_nexus_trade_and_lane(self, trade, *, lane_state, **configuration):
        if self.fail_atomic_settlement:
            raise RuntimeError("atomic settlement fault")
        self.trades.append(dict(trade))
        self.lane_states[trade["lane"]] = dict(lane_state)
        self.restored_owners[trade["lane"]] = None
        if configuration.get("apply_risk"):
            return await self.settle_trade_and_risk(trade, **configuration)
        return {"applied": False, "state": None}

    async def upsert_trade(self, trade):
        self.trades.append(dict(trade))

    async def settle_trade_and_risk(self, trade, **configuration):
        self.risk_settlements.append((dict(trade), dict(configuration)))
        return {
            "applied": True,
            "state": {
                "current_stake": 0.7,
                "current_level": 1,
                "consecutive_wins": 0,
                "consecutive_losses": 1,
            },
        }


class LiveStorePublisher:
    def __init__(self, repository):
        self.repository = repository
        self.store = LiveStore()
        self.events = []
        self.persistence_observations = []

    async def publish(self, event):
        self.events.append(dict(event))
        self.persistence_observations.append({
            "type": event["type"],
            "decisions": len(self.repository.decisions),
            "trades": len(self.repository.trades),
        })
        return self.store.apply(event)


class PausingSettlementRepository(FakeRepository):
    """Expose the post-commit/pre-runtime-close settlement interleaving."""

    def __init__(self):
        super().__init__()
        self.settlement_committed = asyncio.Event()
        self.allow_settlement_return = asyncio.Event()
        self.lane_save_attempted = asyncio.Event()

    async def save_nexus_lane_state(self, lane, state, owner=None):
        self.lane_save_attempted.set()
        await super().save_nexus_lane_state(lane, state, owner=owner)

    async def settle_nexus_trade_and_lane(self, trade, *, lane_state, **configuration):
        result = await super().settle_nexus_trade_and_lane(
            trade,
            lane_state=lane_state,
            **configuration,
        )
        self.settlement_committed.set()
        await self.allow_settlement_return.wait()
        return result


class FakeDispatcher:
    def __init__(
        self, contract_id=700, *, account_id="DOT-DEMO", account_type="demo",
        management_active=False, monitor=None,
    ):
        self.contract_id = contract_id
        self.account_id = account_id
        self.account_type = account_type
        self.management_active = management_active
        self.monitor = monitor
        self.intents = []
        self.emergency_stop = False
        self.released = []
        self.restored = []
        self.reconciliation_result = None
        self.reconciliation_calls = []

    async def submit(self, intent):
        self.intents.append(intent)
        sent = intent.mark_dispatched(60.25)
        return DispatchReceipt(intent.decision_id, self.contract_id, 60.25, 60.4)

    def set_emergency_stop(self, enabled):
        self.emergency_stop = enabled

    def restore_position(self, lane, contract_id):
        self.restored.append((Lane(lane).value, contract_id))

    def restore_quarantine(self, lane):
        self.restored.append((Lane(lane).value, "QUARANTINED"))

    async def reconcile_quarantine(self, correlation_id, decision_id):
        self.reconciliation_calls.append((correlation_id, decision_id))
        return self.reconciliation_result

    def release_position(self, lane, contract_id):
        self.released.append((Lane(lane).value, contract_id))


class WaitUntilStoppedCycleSource:
    def __init__(self):
        self.started = asyncio.Event()

    async def __call__(self, runtime):
        self.started.set()
        await runtime.stop_event.wait()
        return None


class DelayedCycleSource:
    def __init__(self, cycle):
        self.cycle = cycle
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, runtime):
        self.started.set()
        await self.release.wait()
        return self.cycle


class FakeAuth:
    async def list_accounts(self):
        return [
            {
                "account_id": "DOT-DEMO",
                "account_type": "demo",
                "status": "active",
                "balance": "1000",
            },
            {
                "account_id": "ROT-REAL",
                "account_type": "real",
                "status": "active",
                "balance": "10",
            },
        ]

    async def close(self):
        pass


class FakeRuntimeConnection:
    def __init__(self, auth):
        self.auth = auth
        self.connected_accounts = []
        self.disconnected = False

    async def connect(self, account_id):
        self.connected_accounts.append(account_id)
        return True

    async def disconnect(self):
        self.disconnected = True


class FakeMarketData:
    def __init__(self, connection, **kwargs):
        self.connection = connection
        self.kwargs = kwargs
        self.starts = []
        self.closed = False

    async def start(self, symbol, timeframe_seconds):
        self.starts.append((symbol, timeframe_seconds))

    async def close(self):
        self.closed = True


class FakeMonitor:
    def __init__(self):
        self.contracts = []
        self.callbacks = {}

    async def monitor_contract(self, contract_id, callback, on_update_callback=None):
        self.contracts.append(contract_id)
        self.callbacks[contract_id] = callback

    async def close(self):
        pass


def decision_and_intent(lane):
    decision = Decision(
        decision_id=f"decision-{lane.value}",
        contract_type="CALL",
        reason_codes=("center_cross_up",),
        signal_epoch=0,
        target_epoch=60,
        adx=20.0,
        blocked_reason=None,
        lane=lane.value,
    )
    intent = EntryIntent(
        decision_id=decision.decision_id,
        contract_type="CALL",
        reason_codes=decision.reason_codes,
        signal_epoch=0,
        target_epoch=60,
        adx=20.0,
        prepared_epoch=60.0,
        pre_dispatch_epoch=None,
        dispatch_epoch=None,
        accepted_epoch=None,
        entry_delay_ms=None,
        status="PENDING",
        error_code=None,
        contract_id=None,
        lane=lane.value,
    )
    return decision, intent


class NexusTradeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_allow_real = settings.ALLOW_REAL_TRADING
        settings.ALLOW_REAL_TRADING = False
        self.repository = FakeRepository()
        self.shared = FakeDispatcher(701)
        self.separate = FakeDispatcher(801)
        self.snapshot = {
            "runtime": {
                "champion_enabled": 0,
                "champion_account_id": "DOT-DEMO",
                "champion_account_type": "demo",
                "champion_version_id": "champion-v1",
                "trial_version_id": "trial-v1",
            },
            "active_campaigns": [{"id": "campaign-1", "lane": Lane.TRIAL.value}],
            "lanes": [
                {"lane": Lane.CHAMPION.value, "version": {"id": "champion-v1"}},
                {"lane": Lane.TRIAL.value, "version": {"id": "trial-v1"}},
            ],
        }

    def tearDown(self):
        settings.ALLOW_REAL_TRADING = self.previous_allow_real

    def runtime(self, **kwargs):
        champion_factory = kwargs.pop(
            "champion_dispatcher_factory", lambda config: self.separate,
        )
        publisher = kwargs.pop("publisher", LiveStorePublisher(self.repository))
        return NexusTradeRuntime(
            self.repository,
            {"id": "nexus-trade", "strategy_id": "nexus_trade", "desired_state": "STOPPED"},
            shared_demo_dispatcher=self.shared,
            champion_dispatcher_factory=champion_factory,
            runtime_snapshot=self.snapshot,
            publisher=publisher,
            **kwargs,
        )

    async def test_champion_off_and_trial_share_demo_dispatcher_at_exact_stake(self):
        runtime = self.runtime()
        runtime.apply_champion_mode(self.snapshot)
        champion_decision, champion_intent = decision_and_intent(Lane.CHAMPION)
        trial_decision, trial_intent = decision_and_intent(Lane.TRIAL)
        cycle = CausalCycleResult(
            60, object(), object(),
            (champion_decision, trial_decision),
            (champion_intent, trial_intent),
        )

        await runtime.process_cycle(cycle)

        self.assertEqual([item.lane for item in self.shared.intents], [
            Lane.CHAMPION.value, Lane.TRIAL.value,
        ])
        self.assertEqual(self.separate.intents, [])
        self.assertEqual(runtime.strategies[Lane.CHAMPION].state.contract_id, 701)
        self.assertEqual(runtime.strategies[Lane.TRIAL].state.contract_id, 701)

    def test_champion_on_real_fails_closed_and_never_moves_trial(self):
        runtime = self.runtime()
        real_snapshot = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "ROT-REAL",
                "champion_account_type": "real",
            },
        }

        with self.assertRaises(RealTradingDisabled):
            runtime.apply_champion_mode(real_snapshot)

        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)
        self.assertEqual(self.separate.intents, [])

    def test_champion_on_demo_uses_configured_executor_but_trial_stays_shared(self):
        runtime = self.runtime()
        on_snapshot = {
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "champion_enabled": 1},
        }

        runtime.apply_champion_mode(on_snapshot)

        self.assertIs(runtime.dispatchers[Lane.CHAMPION], self.separate)
        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)

    def test_champion_switch_waits_for_idle_boundary(self):
        runtime = self.runtime(restored_lane_states={
            Lane.CHAMPION.value: SetupState(
                position_status="RESERVED",
                owner_decision_id="in-flight",
            ).to_dict(),
        })
        on_snapshot = {
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "champion_enabled": 1},
        }

        applied = runtime.apply_champion_mode(on_snapshot)

        self.assertFalse(applied)
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], self.shared)
        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)

    def test_same_identity_new_dispatcher_waits_for_idle_for_all_owned_states(self):
        for position_status in ("RESERVED", "ACTIVE", "QUARANTINED"):
            with self.subTest(position_status=position_status):
                current = FakeDispatcher(
                    811, account_id="DEMO-A", account_type="demo",
                    management_active=True,
                )
                replacement = FakeDispatcher(
                    812, account_id="DEMO-A", account_type="demo",
                    management_active=True,
                )
                state = SetupState(
                    position_status=position_status,
                    owner_decision_id="owned",
                    contract_id=811 if position_status == "ACTIVE" else None,
                    quarantine_correlation_id=(
                        "nexus-owned" if position_status == "QUARANTINED" else None
                    ),
                )
                runtime = self.runtime(
                    restored_lane_states={Lane.CHAMPION.value: state.to_dict()},
                    champion_dispatcher_factory=lambda config: replacement,
                )
                runtime._champion_enabled = True
                runtime.dispatchers[Lane.CHAMPION] = current
                snapshot = {
                    **self.snapshot,
                    "runtime": {
                        **self.snapshot["runtime"],
                        "champion_enabled": 1,
                        "champion_account_id": "DEMO-A",
                    },
                }

                self.assertFalse(runtime.apply_champion_mode(snapshot))
                self.assertIs(runtime.dispatchers[Lane.CHAMPION], current)

    def test_same_identity_new_dispatcher_swaps_when_lane_is_idle(self):
        current = FakeDispatcher(
            811, account_id="DEMO-A", account_type="demo",
            management_active=True,
        )
        replacement = FakeDispatcher(
            812, account_id="DEMO-A", account_type="demo",
            management_active=True,
        )
        runtime = self.runtime(
            champion_dispatcher_factory=lambda config: replacement,
        )
        runtime._champion_enabled = True
        runtime.dispatchers[Lane.CHAMPION] = current

        applied = runtime.apply_champion_mode({
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "DEMO-A",
            },
        })

        self.assertTrue(applied)
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], replacement)

    def test_champion_dispatcher_factory_is_cached_by_exact_identity(self):
        created = []

        def factory(config):
            dispatcher = FakeDispatcher(
                820 + len(created),
                account_id=config["account_id"],
                account_type=config["account_type"],
                management_active=True,
            )
            created.append(dispatcher)
            return dispatcher

        runtime = self.runtime(champion_dispatcher_factory=factory)
        snapshot = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "DEMO-A",
            },
        }

        runtime.apply_champion_mode(snapshot)
        runtime.apply_champion_mode(snapshot)

        self.assertEqual(len(created), 1)
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], created[0])

    def test_champion_account_change_uses_current_exact_config(self):
        account_a = FakeDispatcher(801, account_id="DEMO-A", account_type="demo")
        account_b = FakeDispatcher(802, account_id="DEMO-B", account_type="demo")
        requested = []

        def factory(config):
            requested.append((config["account_id"], config["account_type"]))
            return {
                ("DEMO-A", "demo"): account_a,
                ("DEMO-B", "demo"): account_b,
            }[(config["account_id"], config["account_type"])]

        runtime = self.runtime(champion_dispatcher_factory=factory)
        for account_id in ("DEMO-A", "DEMO-B"):
            runtime.apply_champion_mode({
                **self.snapshot,
                "runtime": {
                    **self.snapshot["runtime"],
                    "champion_enabled": 1,
                    "champion_account_id": account_id,
                    "champion_account_type": "demo",
                },
            })

        self.assertEqual(requested, [("DEMO-A", "demo"), ("DEMO-B", "demo")])
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], account_b)
        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)

    def test_factory_identity_mismatch_fails_closed(self):
        wrong = FakeDispatcher(802, account_id="STALE-DEMO", account_type="demo")
        runtime = self.runtime(champion_dispatcher_factory=lambda config: wrong)
        snapshot = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "CURRENT-DEMO",
                "champion_account_type": "demo",
            },
        }

        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            runtime.apply_champion_mode(snapshot)

        self.assertIs(runtime.dispatchers[Lane.CHAMPION], self.shared)
        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)

    def test_real_to_demo_switch_uses_no_real_factory_when_real_is_disabled(self):
        demo = FakeDispatcher(803, account_id="NEW-DEMO", account_type="demo")
        real = FakeDispatcher(804, account_id="OLD-REAL", account_type="real")
        requested = []

        def factory(config):
            requested.append((config["account_id"], config["account_type"]))
            return demo

        runtime = self.runtime(champion_dispatcher_factory=factory)
        runtime._champion_enabled = True
        runtime.dispatchers[Lane.CHAMPION] = real

        runtime.apply_champion_mode({
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "NEW-DEMO",
                "champion_account_type": "demo",
            },
        })

        self.assertFalse(settings.ALLOW_REAL_TRADING)
        self.assertEqual(requested, [("NEW-DEMO", "demo")])
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], demo)
        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)

    async def test_runtime_refresh_applies_new_snapshot_at_boundary(self):
        updated = FakeDispatcher(805, account_id="UPDATED-DEMO", account_type="demo")
        runtime = self.runtime(
            champion_dispatcher_factory=lambda config: updated,
        )
        runtime.apply_champion_mode(self.snapshot)
        self.repository.runtime_snapshot = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "UPDATED-DEMO",
                "champion_account_type": "demo",
            },
        }

        await runtime._refresh_runtime_snapshot()

        self.assertIs(runtime.dispatchers[Lane.CHAMPION], updated)
        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)

    async def test_runtime_refresh_applies_emergency_before_deferring_active_champion(self):
        runtime = self.runtime(restored_lane_states={
            Lane.CHAMPION.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="active-owner",
                contract_id=801,
            ).to_dict(),
        })
        runtime.apply_champion_mode({
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "champion_enabled": 1},
        })
        self.repository.runtime_snapshot = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 0,
                "emergency_stop": 1,
            },
        }
        decision, intent = decision_and_intent(Lane.TRIAL)

        await runtime._refresh_runtime_snapshot()
        await runtime.process_cycle(CausalCycleResult(
            60, object(), object(), (decision,), (intent,),
        ))

        self.assertTrue(self.shared.emergency_stop)
        self.assertTrue(self.separate.emergency_stop)
        self.assertIs(runtime._pending_runtime_snapshot, self.repository.runtime_snapshot)
        self.assertEqual(self.shared.intents, [])
        self.assertEqual(self.separate.intents, [])

    async def test_runtime_refresh_reapplies_emergency_from_equal_restart_snapshot(self):
        stopped_snapshot = {
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "emergency_stop": 1},
        }
        runtime = self.runtime()
        runtime._runtime_snapshot = stopped_snapshot
        self.repository.runtime_snapshot = stopped_snapshot

        await runtime._refresh_runtime_snapshot()

        self.assertTrue(self.shared.emergency_stop)

    async def test_decision_and_active_lifecycle_are_persisted_for_restart(self):
        monitor = FakeMonitor()
        runtime = self.runtime(monitors={Lane.CHAMPION: monitor, Lane.TRIAL: monitor})
        runtime.apply_champion_mode(self.snapshot)
        decision, intent = decision_and_intent(Lane.CHAMPION)
        cycle = CausalCycleResult(60, object(), object(), (decision,), (intent,))

        await runtime.process_cycle(cycle)

        self.assertEqual(self.repository.decisions[0]["decision"]["id"], decision.decision_id)
        state = self.repository.lane_states[Lane.CHAMPION.value]
        self.assertEqual(state["position_status"], "ACTIVE")
        self.assertEqual(state["contract_id"], 701)
        self.assertEqual(monitor.contracts, [701])

        restarted = self.runtime(
            restored_lane_states={Lane.CHAMPION.value: state},
        )
        self.assertEqual(
            restarted.strategies[Lane.CHAMPION].state,
            SetupState.from_dict(state),
        )

    async def test_persisted_decision_and_trade_are_published_in_causal_order(self):
        publisher = LiveStorePublisher(self.repository)
        runtime = self.runtime(publisher=publisher)
        runtime.apply_champion_mode(self.snapshot)
        decision, intent = decision_and_intent(Lane.CHAMPION)

        await runtime.process_cycle(CausalCycleResult(
            60, object(), object(), (decision,), (intent,),
        ))
        await runtime.settle_contract(Lane.CHAMPION, decision.decision_id, {
            "contract_id": 701,
            "contract_type": "CALL",
            "status": "won",
            "buy_price": 0.35,
            "payout": 0.67,
            "profit": 0.32,
        })

        self.assertEqual(
            [event["type"] for event in publisher.events],
            ["nexus.decision", "nexus.trade"],
        )
        for event in publisher.events:
            self.assertIsInstance(event["event_id"], str)
            self.assertEqual(event["schema_version"], 1)
            self.assertGreaterEqual(event["snapshot_version"], 1)
        self.assertEqual(publisher.persistence_observations, [
            {"type": "nexus.decision", "decisions": 1, "trades": 0},
            {"type": "nexus.trade", "decisions": 1, "trades": 1},
        ])
        live = publisher.store.snapshot("nexus-trade")
        self.assertEqual(live["decisions"][0]["decision_id"], decision.decision_id)
        self.assertEqual(live["trades"][0]["contract_id"], 701)

    async def test_runtime_refresh_publishes_existing_snapshot_transitions_only(self):
        publisher = LiveStorePublisher(self.repository)
        runtime = self.runtime(publisher=publisher)
        runtime.apply_champion_mode(self.snapshot)
        self.repository.runtime_snapshot = {
            **self.snapshot,
            "bot": {"config_revision": 2},
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
            },
            "lanes": [
                self.snapshot["lanes"][0],
                {"lane": Lane.TRIAL.value, "version": {"id": "trial-v2"}},
            ],
            "active_campaigns": [
                {"id": "campaign-2", "lane": Lane.TRIAL.value, "status": "ACTIVE"},
            ],
        }

        await runtime._refresh_runtime_snapshot()

        self.assertEqual(
            [event["type"] for event in publisher.events],
            [
                "nexus.runtime",
                "nexus.campaign",
                "nexus.version_changed",
                "nexus.trial_changed",
            ],
        )
        self.assertNotIn(
            "nexus.report",
            {event["type"] for event in publisher.events},
        )
        self.assertNotIn(
            "nexus.proposal",
            {event["type"] for event in publisher.events},
        )

    async def test_run_remains_alive_when_desired_state_is_stopped(self):
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(cycle_source=source)

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        self.assertFalse(task.done())
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_request_stop_before_run_finishes_without_scheduling_cycle(self):
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(cycle_source=source)

        await runtime.request_stop()
        await asyncio.wait_for(runtime.run(), timeout=1)

        self.assertFalse(source.started.is_set())
        self.assertEqual(self.shared.intents, [])

    async def test_stop_blocks_dispatchers_immediately_and_skips_returned_cycle(self):
        decision, intent = decision_and_intent(Lane.TRIAL)
        cycle = CausalCycleResult(60, object(), object(), (decision,), (intent,))
        source = DelayedCycleSource(cycle)
        runtime = self.runtime(cycle_source=source)
        runtime._runtime_snapshot = {
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "champion_enabled": 1},
        }

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)
        await runtime.request_stop()

        self.assertTrue(self.shared.emergency_stop)
        self.assertTrue(self.separate.emergency_stop)
        source.release.set()
        await asyncio.wait_for(task, timeout=1)
        self.assertEqual(self.shared.intents, [])
        self.assertEqual(self.separate.intents, [])

    async def test_run_restores_active_lane_before_waiting_for_the_next_cycle(self):
        champion_state = SetupState(
            position_status="ACTIVE",
            owner_decision_id="restored-decision",
            contract_id=919,
        ).to_dict()
        self.repository.restored_states = {Lane.CHAMPION.value: champion_state}
        self.repository.restored_owners = {Lane.CHAMPION.value: {
            "account_id": "DOT-DEMO",
            "account_type": "demo",
            "management_active": False,
        }}
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(cycle_source=source)

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        self.assertEqual(runtime.strategies[Lane.CHAMPION].state.contract_id, 919)
        self.assertIn((Lane.CHAMPION.value, 919), self.shared.restored)
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_restart_fails_closed_when_non_idle_owner_identity_is_missing(self):
        self.repository.restored_states = {
            Lane.CHAMPION.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="missing-owner",
                contract_id=918,
            ).to_dict(),
        }
        runtime = self.runtime(cycle_source=WaitUntilStoppedCycleSource())

        with self.assertRaisesRegex(ValueError, "missing its durable owner"):
            await runtime.run()

        self.assertEqual(self.shared.restored, [])

    async def test_restart_keeps_active_owner_until_settlement_then_applies_account_change(self):
        owner_monitor = FakeMonitor()
        owner_a = FakeDispatcher(
            919, account_id="DEMO-A", account_type="demo",
            management_active=True, monitor=owner_monitor,
        )
        desired_b = FakeDispatcher(
            920, account_id="DEMO-B", account_type="demo",
            management_active=True,
        )
        owner = {
            "account_id": "DEMO-A",
            "account_type": "demo",
            "management_active": True,
        }
        self.repository.restored_states = {
            Lane.CHAMPION.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="owner-a-active",
                contract_id=919,
            ).to_dict(),
        }
        self.repository.restored_owners = {Lane.CHAMPION.value: owner}
        desired = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "DEMO-B",
            },
        }
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(
            cycle_source=source,
            champion_dispatcher_factory=lambda config: {
                "DEMO-A": owner_a,
                "DEMO-B": desired_b,
            }[config["account_id"]],
        )
        runtime._runtime_snapshot = desired

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        self.assertIs(runtime.dispatchers[Lane.CHAMPION], owner_a)
        self.assertEqual(owner_monitor.contracts, [919])
        await runtime.settle_contract(Lane.CHAMPION, "owner-a-active", {
            "contract_id": 919,
            "status": "lost",
            "buy_price": 0.35,
            "profit": -0.35,
        })
        self.assertEqual(owner_a.released, [(Lane.CHAMPION.value, 919)])
        self.assertEqual(len(self.repository.risk_settlements), 1)
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], desired_b)
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_restart_uses_original_management_after_desired_turns_off(self):
        owner_a = FakeDispatcher(
            930, account_id="DEMO-A", account_type="demo",
            management_active=True, monitor=FakeMonitor(),
        )
        self.repository.restored_states = {
            Lane.CHAMPION.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="owner-on-active",
                contract_id=930,
            ).to_dict(),
        }
        self.repository.restored_owners = {Lane.CHAMPION.value: {
            "account_id": "DEMO-A",
            "account_type": "demo",
            "management_active": True,
        }}
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(
            cycle_source=source,
            champion_dispatcher_factory=lambda config: owner_a,
        )

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], owner_a)

        await runtime.settle_contract(Lane.CHAMPION, "owner-on-active", {
            "contract_id": 930,
            "status": "won",
            "buy_price": 0.35,
            "profit": 0.32,
        })

        self.assertEqual(len(self.repository.risk_settlements), 1)
        self.assertEqual(owner_a.released, [(Lane.CHAMPION.value, 930)])
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], self.shared)
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_restart_real_owner_is_monitored_with_fake_before_desired_demo(self):
        real_monitor = FakeMonitor()
        real_owner = FakeDispatcher(
            940, account_id="REAL-A", account_type="real",
            management_active=True, monitor=real_monitor,
        )
        desired_demo = FakeDispatcher(
            941, account_id="DEMO-B", account_type="demo",
            management_active=True,
        )
        self.repository.restored_states = {
            Lane.CHAMPION.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="real-owner-active",
                contract_id=940,
            ).to_dict(),
        }
        self.repository.restored_owners = {Lane.CHAMPION.value: {
            "account_id": "REAL-A",
            "account_type": "real",
            "management_active": True,
        }}
        desired = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "DEMO-B",
            },
        }
        requested = []

        def factory(config):
            requested.append((config["account_id"], config["account_type"]))
            return real_owner if config["account_type"] == "real" else desired_demo

        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(
            cycle_source=source, champion_dispatcher_factory=factory,
        )
        runtime._runtime_snapshot = desired

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        self.assertFalse(settings.ALLOW_REAL_TRADING)
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], real_owner)
        self.assertEqual(real_monitor.contracts, [940])
        self.assertEqual(requested[0], ("REAL-A", "real"))
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_restart_intermediate_journals_reconcile_only_on_persisted_owner(self):
        cases = (
            ("submitting", "DEMO-A", "demo", True, "DEMO-B", "demo", True),
            ("reconcile_pending", "DEMO-A", "demo", True, "DOT-DEMO", "demo", False),
            ("reconcile_pending", "REAL-A", "real", True, "DEMO-B", "demo", True),
        )
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                journal_state, owner_id, owner_type, managed, desired_id, desired_type, desired_on = case
                repository = FakeRepository()
                decision_id = f"intermediate-{index}"
                repository.restored_states = {
                    Lane.CHAMPION.value: SetupState(
                        position_status="RESERVED",
                        owner_decision_id=decision_id,
                    ).to_dict(),
                }
                repository.recovery_intents = [{
                    "id": f"nexus-{decision_id}",
                    "bot_id": "nexus-trade",
                    "account_id": owner_id,
                    "lane": Lane.CHAMPION.value,
                    "decision_id": decision_id,
                    "state": journal_state,
                    "contract_id": None,
                    "metadata": {
                        "correlation_id": f"nexus-{decision_id}",
                        "order_intent_id": f"nexus-{decision_id}",
                        "decision_id": decision_id,
                        "account_id": owner_id,
                        "account_type": owner_type,
                        "management_active": managed,
                        "entry_intent": {"decision_id": decision_id},
                    },
                }]
                owner_dispatcher = FakeDispatcher(
                    950 + index, account_id=owner_id, account_type=owner_type,
                    management_active=managed,
                )
                desired_dispatcher = FakeDispatcher(
                    960 + index, account_id=desired_id, account_type=desired_type,
                    management_active=desired_on,
                )
                desired = {
                    **self.snapshot,
                    "runtime": {
                        **self.snapshot["runtime"],
                        "champion_enabled": int(desired_on),
                        "champion_account_id": desired_id,
                        "champion_account_type": desired_type,
                    },
                }
                source = WaitUntilStoppedCycleSource()

                def factory(config):
                    if (config["account_id"], config["account_type"]) == (owner_id, owner_type):
                        return owner_dispatcher
                    return desired_dispatcher

                runtime = NexusTradeRuntime(
                    repository,
                    {"id": "nexus-trade", "strategy_id": "nexus_trade", "desired_state": "STOPPED"},
                    shared_demo_dispatcher=self.shared,
                    champion_dispatcher_factory=factory,
                    runtime_snapshot=desired,
                    cycle_source=source,
                    publisher=LiveStorePublisher(repository),
                )
                task = asyncio.create_task(runtime.run())
                await asyncio.wait_for(source.started.wait(), timeout=1)

                self.assertEqual(
                    owner_dispatcher.reconciliation_calls,
                    [(f"nexus-{decision_id}", decision_id)],
                )
                self.assertEqual(desired_dispatcher.reconciliation_calls, [])
                self.assertIs(runtime.dispatchers[Lane.CHAMPION], owner_dispatcher)
                await runtime.request_stop()
                await asyncio.wait_for(task, timeout=1)

    async def test_restart_promotes_exact_owned_reserved_journal_to_active(self):
        decision_id = "owned-after-crash"
        self.repository.restored_states = {
            Lane.CHAMPION.value: SetupState(
                position_status="RESERVED",
                owner_decision_id=decision_id,
            ).to_dict(),
        }
        self.repository.recovery_intents = [{
            "id": f"nexus-{decision_id}",
            "bot_id": "nexus-trade",
            "account_id": "DOT-DEMO",
            "lane": Lane.CHAMPION.value,
            "decision_id": decision_id,
            "state": "owned",
            "contract_id": 921,
            "metadata": {
                "correlation_id": f"nexus-{decision_id}",
                "order_intent_id": f"nexus-{decision_id}",
                "decision_id": decision_id,
                "account_id": "DOT-DEMO",
                "account_type": "demo",
                "management_active": False,
                "entry_intent": {"decision_id": decision_id},
            },
        }]
        monitor = FakeMonitor()
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(
            cycle_source=source,
            monitors={Lane.CHAMPION: monitor, Lane.TRIAL: monitor},
        )

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        state = runtime.strategies[Lane.CHAMPION].state
        self.assertEqual(state.position_status, "ACTIVE")
        self.assertEqual(state.contract_id, 921)
        self.assertIn((Lane.CHAMPION.value, 921), self.shared.restored)
        self.assertEqual(monitor.contracts, [921])
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_restart_quarantines_reserved_submitting_journal(self):
        decision_id = "ambiguous-after-crash"
        self.repository.restored_states = {
            Lane.TRIAL.value: SetupState(
                position_status="RESERVED",
                owner_decision_id=decision_id,
            ).to_dict(),
        }
        self.repository.recovery_intents = [{
            "id": f"nexus-{decision_id}",
            "bot_id": "nexus-trade",
            "account_id": "DOT-DEMO",
            "lane": Lane.TRIAL.value,
            "decision_id": decision_id,
            "state": "submitting",
            "contract_id": None,
            "metadata": {
                "correlation_id": f"nexus-{decision_id}",
                "order_intent_id": f"nexus-{decision_id}",
                "decision_id": decision_id,
                "account_id": "DOT-DEMO",
                "account_type": "demo",
                "management_active": False,
                "entry_intent": {"decision_id": decision_id},
            },
        }]
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(cycle_source=source)

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        state = runtime.strategies[Lane.TRIAL].state
        self.assertEqual(state.position_status, "QUARANTINED")
        self.assertEqual(
            state.quarantine_correlation_id, f"nexus-{decision_id}",
        )
        self.assertIn((Lane.TRIAL.value, "QUARANTINED"), self.shared.restored)
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_restart_releases_reserved_lane_when_no_transport_journal_exists(self):
        decision_id = "crash-before-intent"
        self.repository.restored_states = {
            Lane.TRIAL.value: SetupState(
                position_status="RESERVED",
                owner_decision_id=decision_id,
            ).to_dict(),
        }
        self.repository.restored_owners = {Lane.TRIAL.value: {
            "account_id": "DOT-DEMO",
            "account_type": "demo",
            "management_active": False,
        }}
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(cycle_source=source)

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        self.assertEqual(
            runtime.strategies[Lane.TRIAL].state.position_status, "IDLE",
        )
        self.assertNotIn((Lane.TRIAL.value, "QUARANTINED"), self.shared.restored)
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_settlement_persists_lane_trade_and_releases_only_its_owner(self):
        runtime = self.runtime(restored_lane_states={
            Lane.TRIAL.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="trial-owner",
                contract_id=818,
            ).to_dict(),
        })
        runtime.apply_champion_mode(self.snapshot)

        await runtime.settle_contract(Lane.TRIAL, "trial-owner", {
            "contract_id": 818,
            "status": "won",
            "buy_price": 0.35,
            "payout": 0.67,
            "profit": 0.32,
            "date_start": 100,
            "date_expiry": 158,
        })

        self.assertEqual(runtime.strategies[Lane.TRIAL].state.position_status, "IDLE")
        self.assertEqual(self.shared.released, [(Lane.TRIAL.value, 818)])
        self.assertEqual(self.repository.trades[0]["lane"], Lane.TRIAL.value)
        self.assertEqual(self.repository.trades[0]["stake"], 0.35)
        self.assertEqual(self.repository.trades[0]["decision_id"], "trial-owner")
        self.assertEqual(self.repository.risk_settlements, [])

    async def test_champion_on_settlement_applies_configured_management(self):
        runtime = self.runtime()
        runtime.apply_champion_mode({
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "champion_enabled": 1},
        })
        runtime.strategies[Lane.CHAMPION] = NexusTradeStrategy(
            lane=Lane.CHAMPION,
            state=SetupState(
                position_status="ACTIVE",
                owner_decision_id="champion-owner",
                contract_id=717,
            ),
        )

        await runtime.settle_contract(Lane.CHAMPION, "champion-owner", {
            "contract_id": 717,
            "status": "lost",
            "buy_price": 0.35,
            "profit": -0.35,
        })

        self.assertEqual(len(self.repository.risk_settlements), 1)
        configuration = self.repository.risk_settlements[0][1]
        self.assertEqual(configuration["money_management"], "fixed")
        self.assertEqual(configuration["initial_stake"], 0.35)

    async def test_settlement_failure_keeps_lane_and_dispatcher_owned(self):
        self.repository.fail_atomic_settlement = True
        runtime = self.runtime(restored_lane_states={
            Lane.TRIAL.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="trial-fault",
                contract_id=828,
            ).to_dict(),
        })
        runtime.apply_champion_mode(self.snapshot)

        with self.assertRaisesRegex(RuntimeError, "atomic settlement fault"):
            await runtime.settle_contract(Lane.TRIAL, "trial-fault", {
                "contract_id": 828,
                "status": "won",
                "buy_price": 0.35,
                "profit": 0.32,
            })

        self.assertEqual(
            runtime.strategies[Lane.TRIAL].state.position_status, "ACTIVE",
        )
        self.assertEqual(self.shared.released, [])
        self.assertEqual(self.repository.trades, [])

    async def test_settlement_serializes_cycle_save_until_in_memory_close(self):
        self.repository = PausingSettlementRepository()
        active = SetupState(
            position_status="ACTIVE",
            owner_decision_id="race-owner",
            contract_id=838,
        ).to_dict()
        runtime = self.runtime(restored_lane_states={
            Lane.TRIAL.value: active,
        })
        runtime.apply_champion_mode(self.snapshot)
        decision, pending = decision_and_intent(Lane.TRIAL)
        stale = pending.mark_dispatched(63.0)
        cycle = CausalCycleResult(
            60,
            object(),
            object(),
            (decision,),
            (stale,),
        )

        settlement = asyncio.create_task(runtime.settle_contract(
            Lane.TRIAL,
            "race-owner",
            {
                "contract_id": 838,
                "status": "won",
                "buy_price": 0.35,
                "profit": 0.32,
            },
        ))
        await asyncio.wait_for(
            self.repository.settlement_committed.wait(),
            timeout=1,
        )
        cycle_task = asyncio.create_task(runtime.process_cycle(cycle))
        try:
            await asyncio.sleep(0)
            self.assertFalse(self.repository.lane_save_attempted.is_set())
            self.assertFalse(cycle_task.done())
        finally:
            self.repository.allow_settlement_return.set()
            task_results = await asyncio.gather(
                settlement,
                cycle_task,
                return_exceptions=True,
            )
        self.assertEqual(task_results, [None, None])

        restored = self.repository.lane_states[Lane.TRIAL.value]
        self.assertEqual(restored["position_status"], "IDLE")
        self.assertIsNone(restored["owner_decision_id"])
        self.assertIsNone(restored["contract_id"])
        self.repository.restored_states = {Lane.TRIAL.value: restored}
        self.repository.restored_owners = {Lane.TRIAL.value: None}

        restarted_dispatcher = FakeDispatcher(839)
        restarted_monitor = FakeMonitor()
        restarted_source = WaitUntilStoppedCycleSource()
        restarted = NexusTradeRuntime(
            self.repository,
            {
                "id": "nexus-trade",
                "strategy_id": "nexus_trade",
                "desired_state": "STOPPED",
            },
            shared_demo_dispatcher=restarted_dispatcher,
            champion_dispatcher_factory=lambda config: self.separate,
            runtime_snapshot=self.snapshot,
            cycle_source=restarted_source,
            monitors={Lane.TRIAL: restarted_monitor},
            publisher=LiveStorePublisher(self.repository),
        )
        restarted_task = asyncio.create_task(restarted.run())
        await asyncio.wait_for(restarted_source.started.wait(), timeout=1)
        self.assertEqual(
            restarted.strategies[Lane.TRIAL].state.position_status,
            "IDLE",
        )
        self.assertEqual(restarted_dispatcher.restored, [])
        self.assertEqual(restarted_monitor.contracts, [])

        next_decision, next_intent = decision_and_intent(Lane.TRIAL)
        next_decision = Decision(
            **{
                **next_decision.to_dict(),
                "decision_id": "after-race",
            },
        )
        next_intent = EntryIntent(
            **{
                **next_intent.to_dict(),
                "decision_id": "after-race",
            },
        )
        await restarted.process_cycle(CausalCycleResult(
            60,
            object(),
            object(),
            (next_decision,),
            (next_intent,),
        ))
        self.assertEqual(
            restarted.strategies[Lane.TRIAL].state.contract_id,
            839,
        )
        await restarted.request_stop()
        await asyncio.wait_for(restarted_task, timeout=1)

    async def test_cancelling_cycle_waiter_does_not_poison_lane_serialization(self):
        self.repository = PausingSettlementRepository()
        runtime = self.runtime(restored_lane_states={
            Lane.TRIAL.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="cancel-race-owner",
                contract_id=848,
            ).to_dict(),
        })
        runtime.apply_champion_mode(self.snapshot)
        decision, pending = decision_and_intent(Lane.TRIAL)
        stale = pending.mark_dispatched(63.0)
        cycle = CausalCycleResult(
            60,
            object(),
            object(),
            (decision,),
            (stale,),
        )
        settlement = asyncio.create_task(runtime.settle_contract(
            Lane.TRIAL,
            "cancel-race-owner",
            {
                "contract_id": 848,
                "status": "won",
                "buy_price": 0.35,
                "profit": 0.32,
            },
        ))
        await asyncio.wait_for(
            self.repository.settlement_committed.wait(),
            timeout=1,
        )
        waiter = asyncio.create_task(runtime.process_cycle(cycle))
        try:
            await asyncio.sleep(0)
            self.assertFalse(waiter.done())
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(waiter, timeout=1)
        finally:
            self.repository.allow_settlement_return.set()
            await asyncio.gather(settlement, waiter, return_exceptions=True)

        self.assertEqual(
            runtime.strategies[Lane.TRIAL].state.position_status,
            "IDLE",
        )
        follow_up = asyncio.create_task(runtime.process_cycle(cycle))
        await asyncio.wait_for(follow_up, timeout=1)

    async def test_cancel_after_settlement_commit_finishes_in_memory_close(self):
        self.repository = PausingSettlementRepository()
        runtime = self.runtime(restored_lane_states={
            Lane.TRIAL.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="cancel-settlement-owner",
                contract_id=858,
            ).to_dict(),
        })
        runtime.apply_champion_mode(self.snapshot)
        settlement = asyncio.create_task(runtime.settle_contract(
            Lane.TRIAL,
            "cancel-settlement-owner",
            {
                "contract_id": 858,
                "status": "won",
                "buy_price": 0.35,
                "profit": 0.32,
            },
        ))
        await asyncio.wait_for(
            self.repository.settlement_committed.wait(),
            timeout=1,
        )
        settlement.cancel()
        try:
            await asyncio.sleep(0)
            self.assertFalse(settlement.done())
        finally:
            self.repository.allow_settlement_return.set()

        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(settlement, timeout=1)
        self.assertEqual(
            runtime.strategies[Lane.TRIAL].state.position_status,
            "IDLE",
        )
        self.assertEqual(
            self.shared.released,
            [(Lane.TRIAL.value, 858)],
        )

    async def test_restart_reconciles_quarantine_only_with_persisted_correlation(self):
        quarantined = SetupState(
            position_status="QUARANTINED",
            owner_decision_id="lost-owner",
            quarantine_correlation_id="nexus-lost-owner",
        ).to_dict()
        self.repository.restored_states = {Lane.TRIAL.value: quarantined}
        self.repository.restored_owners = {Lane.TRIAL.value: {
            "account_id": "DOT-DEMO",
            "account_type": "demo",
            "management_active": False,
        }}
        self.shared.reconciliation_result = OwnershipReconciliation(
            correlation_id="nexus-lost-owner",
            decision_id="lost-owner",
            outcome="CONTRACT_FOUND",
            contract_id=929,
        )
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(cycle_source=source)

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        state = runtime.strategies[Lane.TRIAL].state
        self.assertEqual(state.position_status, "ACTIVE")
        self.assertEqual(state.contract_id, 929)
        self.assertIn((Lane.TRIAL.value, "QUARANTINED"), self.shared.restored)
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_emergency_stop_is_forwarded_to_both_dispatchers(self):
        runtime = self.runtime()
        runtime.apply_champion_mode({
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "champion_enabled": 1},
        })

        runtime.set_emergency_stop(True)

        self.assertTrue(self.shared.emergency_stop)
        self.assertTrue(self.separate.emergency_stop)

    def test_persisted_emergency_stop_is_applied_before_a_mode_change_can_defer(self):
        runtime = self.runtime(restored_lane_states={
            Lane.CHAMPION.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="in-flight",
                contract_id=701,
            ).to_dict(),
        })
        stopped_snapshot = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "emergency_stop": 1,
            },
        }

        applied = runtime.apply_champion_mode(stopped_snapshot)

        self.assertFalse(applied)
        self.assertTrue(self.shared.emergency_stop)
        self.assertTrue(self.separate.emergency_stop)

    async def test_default_bootstrap_uses_one_r100_m1_stream_and_demo_dispatcher(self):
        connections = []
        markets = []

        def connection_factory(auth):
            connection = FakeRuntimeConnection(auth)
            connections.append(connection)
            return connection

        def market_factory(connection, **kwargs):
            market = FakeMarketData(connection, **kwargs)
            markets.append(market)
            return market

        runtime = NexusTradeRuntime(
            self.repository,
            {"id": "nexus-trade", "strategy_id": "nexus_trade", "desired_state": "STOPPED"},
            runtime_snapshot=self.snapshot,
            auth_factory=FakeAuth,
            connection_factory=connection_factory,
            market_data_factory=market_factory,
            shared_dispatcher_factory=lambda connection, repository, **kwargs: self.shared,
        )

        await runtime.bootstrap()

        self.assertEqual(len(connections), 1)
        self.assertEqual(connections[0].connected_accounts, ["DOT-DEMO"])
        self.assertEqual(markets[0].starts, [("R_100", 60)])
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], self.shared)
        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)
        await runtime.close()

    async def test_default_bootstrap_connects_durable_demo_owner_before_desired_account(self):
        class TwoDemoAuth(FakeAuth):
            async def list_accounts(self):
                return [
                    {"account_id": "DEMO-A", "account_type": "demo", "status": "active"},
                    {"account_id": "DEMO-B", "account_type": "demo", "status": "active"},
                ]

        connections = []

        def connection_factory(auth):
            connection = FakeRuntimeConnection(auth)
            connections.append(connection)
            return connection

        owner_dispatcher = FakeDispatcher(
            970, account_id="DEMO-A", account_type="demo",
            management_active=False,
        )
        self.repository.restored_states = {
            Lane.CHAMPION.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="shared-owner-a",
                contract_id=970,
            ).to_dict(),
        }
        self.repository.restored_owners = {Lane.CHAMPION.value: {
            "account_id": "DEMO-A",
            "account_type": "demo",
            "management_active": False,
        }}
        desired = {
            **self.snapshot,
            "runtime": {
                **self.snapshot["runtime"],
                "champion_enabled": 1,
                "champion_account_id": "DEMO-B",
            },
        }
        source = WaitUntilStoppedCycleSource()
        runtime = NexusTradeRuntime(
            self.repository,
            {"id": "nexus-trade", "strategy_id": "nexus_trade", "desired_state": "STOPPED"},
            runtime_snapshot=desired,
            auth_factory=TwoDemoAuth,
            connection_factory=connection_factory,
            market_data_factory=FakeMarketData,
            shared_dispatcher_factory=(
                lambda connection, repository, **kwargs: owner_dispatcher
            ),
            account_dispatcher_factory=lambda *args, **kwargs: self.fail(
                "desired account must remain pending while owner is ACTIVE"
            ),
            monitor_factory=lambda connection: FakeMonitor(),
            cycle_source=source,
            publisher=LiveStorePublisher(self.repository),
        )

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        self.assertEqual(
            [connection.connected_accounts for connection in connections],
            [["DEMO-A"]],
        )
        self.assertIs(runtime.dispatchers[Lane.CHAMPION], owner_dispatcher)
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

    async def test_bootstrap_restores_accumulated_champion_stake_before_factory(self):
        self.repository.risk_state = {
            "current_stake": 1.4,
            "current_level": 0,
            "consecutive_wins": 1,
            "consecutive_losses": 0,
            "circuit_consecutive_losses": 0,
            "circuit_tripped_at": 0.0,
        }
        on_snapshot = {
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "champion_enabled": 1},
        }
        observed_stakes = []
        champion = FakeDispatcher(806)

        def account_dispatcher_factory(connection, repository, **kwargs):
            observed_stakes.append(kwargs["stake_provider"]())
            return champion

        runtime = NexusTradeRuntime(
            self.repository,
            {
                "id": "nexus-trade",
                "strategy_id": "nexus_trade",
                "desired_state": "STOPPED",
                "initial_stake": 0.35,
                "money_management": "soros",
                "money_config": {"levels": 2, "percent": 0.5},
            },
            runtime_snapshot=on_snapshot,
            auth_factory=FakeAuth,
            connection_factory=FakeRuntimeConnection,
            market_data_factory=FakeMarketData,
            shared_dispatcher_factory=(
                lambda connection, repository, **kwargs: self.shared
            ),
            account_dispatcher_factory=account_dispatcher_factory,
            monitor_factory=lambda connection: FakeMonitor(),
        )

        await runtime.bootstrap()

        self.assertEqual(observed_stakes, [1.4])
        await runtime.close()


if __name__ == "__main__":
    unittest.main()
