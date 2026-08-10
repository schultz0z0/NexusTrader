import asyncio
import unittest

from config.settings import settings
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
        self.risk_settlements = []
        self.recovery_intents = []
        self.runtime_snapshot = None
        self.risk_state = None
        self.fail_atomic_settlement = False

    async def record_nexus_decision(self, decision, *, nexus_version_id, campaign_id, state):
        self.decisions.append({
            "decision": decision,
            "nexus_version_id": nexus_version_id,
            "campaign_id": campaign_id,
            "state": state,
        })

    async def save_nexus_lane_state(self, lane, state):
        self.lane_states[lane] = dict(state)

    async def touch_bot_heartbeat(self, bot_id):
        self.heartbeats += 1

    async def load_nexus_lane_states(self):
        return {lane: dict(state) for lane, state in self.restored_states.items()}

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


class FakeDispatcher:
    def __init__(
        self, contract_id=700, *, account_id="DOT-DEMO", account_type="demo"
    ):
        self.contract_id = contract_id
        self.account_id = account_id
        self.account_type = account_type
        self.intents = []
        self.emergency_stop = False
        self.released = []
        self.restored = []
        self.reconciliation_result = None

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
        return NexusTradeRuntime(
            self.repository,
            {"id": "nexus-trade", "strategy_id": "nexus_trade", "desired_state": "STOPPED"},
            shared_demo_dispatcher=self.shared,
            champion_dispatcher_factory=champion_factory,
            runtime_snapshot=self.snapshot,
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

        with self.assertRaisesRegex(ValueError, "safe boundary"):
            runtime.apply_champion_mode(on_snapshot)

        self.assertIs(runtime.dispatchers[Lane.CHAMPION], self.shared)
        self.assertIs(runtime.dispatchers[Lane.TRIAL], self.shared)

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

    async def test_run_remains_alive_when_desired_state_is_stopped(self):
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(cycle_source=source)

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        self.assertFalse(task.done())
        await runtime.request_stop()
        await asyncio.wait_for(task, timeout=1)

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
        source = WaitUntilStoppedCycleSource()
        runtime = self.runtime(cycle_source=source)

        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(source.started.wait(), timeout=1)

        self.assertEqual(runtime.strategies[Lane.CHAMPION].state.contract_id, 919)
        self.assertIn((Lane.CHAMPION.value, 919), self.shared.restored)
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
            "lane": Lane.CHAMPION.value,
            "decision_id": decision_id,
            "state": "owned",
            "contract_id": 921,
            "metadata": {
                "correlation_id": f"nexus-{decision_id}",
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
            "lane": Lane.TRIAL.value,
            "decision_id": decision_id,
            "state": "submitting",
            "contract_id": None,
            "metadata": {
                "correlation_id": f"nexus-{decision_id}",
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

    async def test_restart_reconciles_quarantine_only_with_persisted_correlation(self):
        quarantined = SetupState(
            position_status="QUARANTINED",
            owner_decision_id="lost-owner",
            quarantine_correlation_id="nexus-lost-owner",
        ).to_dict()
        self.repository.restored_states = {Lane.TRIAL.value: quarantined}
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
