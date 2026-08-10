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
    def __init__(self, contract_id=700):
        self.contract_id = contract_id
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
        return NexusTradeRuntime(
            self.repository,
            {"id": "nexus-trade", "strategy_id": "nexus_trade", "desired_state": "STOPPED"},
            shared_demo_dispatcher=self.shared,
            champion_dispatcher_factory=lambda config: self.separate,
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
        runtime = self.runtime(restored_lane_states={
            Lane.CHAMPION.value: SetupState(
                position_status="ACTIVE",
                owner_decision_id="champion-owner",
                contract_id=717,
            ).to_dict(),
        })
        runtime.apply_champion_mode({
            **self.snapshot,
            "runtime": {**self.snapshot["runtime"], "champion_enabled": 1},
        })

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


if __name__ == "__main__":
    unittest.main()
