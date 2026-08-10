"""Continuous isolated runtime for the two NexusTrade lanes."""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import replace

from core.accounts import normalize_account
from nexus_trade.clock import CausalCycleResult, EntryClock
from nexus_trade.constants import (
    NEXUS_DEMO_STAKE,
    NEXUS_SYMBOL,
    NEXUS_TIMEFRAME_SECONDS,
    NEXUS_TRADE_BOT_ID,
)
from nexus_trade.dispatcher import (
    AccountDispatcher,
    BuyRejectedError,
    DispatchBlockedError,
    OwnershipQuarantineError,
    StaleIntentError,
    SharedDemoDispatcher,
)
from nexus_trade.domain import Lane
from nexus_trade.features import FeatureBuilder
from nexus_trade.strategy import NexusTradeStrategy
from strategies.base import MoneyManager
from trading.safety import ensure_account_allowed


class NexusTradeRuntime:
    """Owns one market cycle, two strategy states, and account-specific dispatchers."""

    def __init__(
        self,
        repository,
        bot,
        *,
        shared_demo_dispatcher=None,
        champion_dispatcher_factory=None,
        runtime_snapshot=None,
        restored_lane_states=None,
        cycle_source=None,
        auth_factory=None,
        connection_factory=None,
        market_data_factory=None,
        shared_dispatcher_factory=None,
        account_dispatcher_factory=None,
        clock=None,
        feature_builder=None,
        monitors=None,
        monitor_factory=None,
    ):
        self.repository = repository
        self.bot = dict(bot)
        if self.bot.get("id") != NEXUS_TRADE_BOT_ID or self.bot.get("strategy_id") != "nexus_trade":
            raise ValueError("NexusTradeRuntime requires the protected nexus-trade singleton")
        self.bot_id = NEXUS_TRADE_BOT_ID
        self._runtime_snapshot = runtime_snapshot
        self._shared_demo_dispatcher = shared_demo_dispatcher
        self._champion_dispatcher_factory = champion_dispatcher_factory
        self._cycle_source = cycle_source
        self._auth_factory = auth_factory
        self._connection_factory = connection_factory
        self._market_data_factory = market_data_factory
        self._shared_dispatcher_factory = shared_dispatcher_factory
        self._account_dispatcher_factory = account_dispatcher_factory
        self._clock = clock
        self._feature_builder = feature_builder
        self._market_data = None
        self._monitor_factory = monitor_factory
        self._shared_demo_monitor = None
        self._champion_monitor = None
        self.monitors = dict(monitors or {})
        self._connections = []
        self._dispatchers_started = set()
        self._champion_dispatchers = {}
        self._champion_monitors = {}
        self._managed_champion_factory = False
        self._available_accounts = []
        self._shared_connection = None
        self._shared_buy_lock = None
        self._last_boundary = None
        self._bootstrapped = shared_demo_dispatcher is not None
        self._stop_event = asyncio.Event()
        self._emergency_stop = False
        self._champion_enabled = False
        money_config = dict(self.bot.get("money_config") or {})
        self._champion_money_manager = MoneyManager(
            mode=self.bot.get("money_management", "fixed"),
            initial_stake=float(self.bot.get("initial_stake", NEXUS_DEMO_STAKE)),
            martingale_multiplier=float(money_config.get("multiplier", 2.0)),
            max_martingale_levels=int(money_config.get("max_levels", 3)),
            soros_levels=int(money_config.get("levels", 2)),
            soros_percent=float(money_config.get("percent", 0.5)),
        )
        restored = restored_lane_states or {}
        self.strategies = {
            lane: NexusTradeStrategy(
                lane=lane,
                state=restored.get(lane.value),
            )
            for lane in Lane
        }
        self.dispatchers = {
            Lane.CHAMPION: shared_demo_dispatcher,
            Lane.TRIAL: shared_demo_dispatcher,
        }
        self._versions = {lane: None for lane in Lane}
        self._campaigns = {lane: None for lane in Lane}

    @property
    def stop_event(self):
        return self._stop_event

    async def request_stop(self):
        # Block both account queues synchronously before waking/cancelling the
        # current boundary wait.
        self.set_emergency_stop(True)
        self._stop_event.set()

    async def bootstrap(self) -> None:
        if self._bootstrapped:
            if self._runtime_snapshot is not None:
                self.apply_champion_mode(self._runtime_snapshot)
            return
        if self._runtime_snapshot is None:
            snapshot_getter = getattr(self.repository, "get_nexus_runtime_snapshot", None)
            if not callable(snapshot_getter):
                raise ValueError("NexusTrade runtime snapshot is unavailable")
            self._runtime_snapshot = await snapshot_getter()

        risk_loader = getattr(self.repository, "get_risk_state", None)
        if callable(risk_loader):
            restored_risk = await risk_loader(
                self.bot_id,
                float(self.bot.get("initial_stake", NEXUS_DEMO_STAKE)),
            )
            self._restore_champion_risk_state(restored_risk)

        if self._auth_factory is None:
            from core.auth import AuthManager
            self._auth_factory = AuthManager
        if self._connection_factory is None:
            from core.connection import NexusConnection
            self._connection_factory = NexusConnection
        if self._market_data_factory is None:
            from data.market_data import MarketDataHandler
            self._market_data_factory = MarketDataHandler
        if self._shared_dispatcher_factory is None:
            self._shared_dispatcher_factory = SharedDemoDispatcher
        if self._account_dispatcher_factory is None:
            self._account_dispatcher_factory = AccountDispatcher
        if self._monitor_factory is None:
            from trading.monitor import ContractMonitor
            self._monitor_factory = ContractMonitor

        auth = self._auth_factory()
        raw_accounts = await auth.list_accounts()
        accounts = []
        for raw in raw_accounts:
            try:
                account = normalize_account(raw)
            except ValueError:
                continue
            if str(account.get("status", "active")).lower() == "active":
                accounts.append(account)
        self._available_accounts = list(accounts)
        demos = [account for account in accounts if account["account_type"] == "demo"]
        if not demos:
            await auth.close()
            raise ValueError("NexusTrade requires an active DEMO account for Trial")
        runtime = self._runtime_snapshot.get("runtime") or {}
        preferred_demo = str(runtime.get("champion_account_id") or "")
        demo = next(
            (account for account in demos if account["account_id"] == preferred_demo),
            demos[0],
        )
        connection = self._connection_factory(auth)
        if not await connection.connect(demo["account_id"]):
            await auth.close()
            raise ConnectionError("unable to connect the NexusTrade DEMO account")
        self._connections.append(connection)
        shared_buy_lock = asyncio.Lock()
        self._shared_connection = connection
        self._shared_buy_lock = shared_buy_lock
        self._shared_demo_dispatcher = self._shared_dispatcher_factory(
            connection,
            self.repository,
            account_id=demo["account_id"],
            account_type="demo",
            stake=NEXUS_DEMO_STAKE,
            buy_lock=shared_buy_lock,
        )
        starter = getattr(self._shared_demo_dispatcher, "start", None)
        if callable(starter):
            await starter()
            self._dispatchers_started.add(id(self._shared_demo_dispatcher))
        self._shared_demo_monitor = self._monitor_factory(connection)

        champion_enabled = bool(runtime.get("champion_enabled", 0))
        champion_dispatcher = None
        if champion_enabled:
            account_id = str(runtime.get("champion_account_id") or "")
            account_type = str(runtime.get("champion_account_type") or "").lower()
            ensure_account_allowed(account_type)
            selected = next(
                (
                    account for account in accounts
                    if account["account_id"] == account_id
                    and account["account_type"] == account_type
                ),
                None,
            )
            if selected is None:
                raise ValueError("selected Champion account was not returned by Deriv")
            champion_connection = connection
            champion_lock = shared_buy_lock
            if selected["account_id"] != demo["account_id"]:
                champion_auth = self._auth_factory()
                champion_connection = self._connection_factory(champion_auth)
                if not await champion_connection.connect(selected["account_id"]):
                    await champion_auth.close()
                    raise ConnectionError("unable to connect the selected Champion account")
                self._connections.append(champion_connection)
                champion_lock = asyncio.Lock()
            champion_dispatcher = self._account_dispatcher_factory(
                champion_connection,
                self.repository,
                account_id=selected["account_id"],
                account_type=selected["account_type"],
                stake=float(self.bot.get("initial_stake", NEXUS_DEMO_STAKE)),
                stake_provider=self._champion_money_manager.get_stake,
                buy_lock=champion_lock,
            )
            starter = getattr(champion_dispatcher, "start", None)
            if callable(starter):
                await starter()
                self._dispatchers_started.add(id(champion_dispatcher))
            self._champion_monitor = (
                self._shared_demo_monitor
                if champion_connection is connection
                else self._monitor_factory(champion_connection)
            )
            self._champion_dispatchers[
                (selected["account_id"], selected["account_type"])
            ] = champion_dispatcher
            self._champion_monitors[
                (selected["account_id"], selected["account_type"])
            ] = self._champion_monitor
        if self._champion_dispatcher_factory is None:
            self._managed_champion_factory = True
            def configured_dispatcher(config):
                identity = (config["account_id"], config["account_type"])
                dispatcher = self._champion_dispatchers.get(identity)
                if dispatcher is None:
                    raise ValueError(
                        "Champion account dispatcher is not provisioned for current config",
                    )
                return dispatcher

            self._champion_dispatcher_factory = configured_dispatcher
        self._market_data = self._market_data_factory(
            connection,
            bot_id=self.bot_id,
            bollinger_period=20,
            bollinger_std_dev=2.0,
        )
        await self._market_data.start(NEXUS_SYMBOL, NEXUS_TIMEFRAME_SECONDS)
        self._clock = self._clock or EntryClock()
        self._feature_builder = self._feature_builder or FeatureBuilder()
        if self._cycle_source is None:
            self._cycle_source = self._next_causal_cycle
        self._bootstrapped = True
        self.apply_champion_mode(self._runtime_snapshot)

    async def close(self) -> None:
        if self._market_data is not None:
            await self._market_data.close()
            self._market_data = None
        seen_monitors = set()
        for monitor in [*self.monitors.values(), *self._champion_monitors.values()]:
            if monitor is None or id(monitor) in seen_monitors:
                continue
            await monitor.close()
            seen_monitors.add(id(monitor))
        seen = set()
        for dispatcher in [
            *self.dispatchers.values(), *self._champion_dispatchers.values(),
        ]:
            if dispatcher is None or id(dispatcher) in seen:
                continue
            closer = getattr(dispatcher, "close", None)
            if callable(closer):
                result = closer()
                if inspect.isawaitable(result):
                    await result
            seen.add(id(dispatcher))
        for connection in reversed(self._connections):
            await connection.disconnect()
        self._connections.clear()

    async def _next_causal_cycle(self, runtime):
        target = self._clock.next_boundary_epoch(after_epoch=self._last_boundary)
        group = _LaneStrategyGroup(self.strategies)

        def finalize_candle(boundary):
            target_open = boundary - NEXUS_TIMEFRAME_SECONDS
            candles = self._market_data.get_candle_history(NEXUS_SYMBOL)
            candle = next(
                (
                    dict(item) for item in reversed(candles)
                    if int(item.get("open_epoch", item.get("time", -1))) == target_open
                ),
                None,
            )
            if candle is None:
                raise ValueError(f"closed R_100/M1 candle {target_open} is unavailable")
            candle["open_epoch"] = target_open
            candle["time"] = target_open
            candle["close_epoch"] = boundary
            candle["is_closed"] = True
            return candle

        def calculate_indicators(closed_candle):
            candles = [dict(item) for item in self._market_data.get_candle_history(NEXUS_SYMBOL)]
            frames = self._feature_builder.build(
                candles,
                decision_epoch=closed_candle["close_epoch"],
                active_candle_time=closed_candle["close_epoch"],
            )
            frame = next(
                (item for item in reversed(frames) if item.epoch == closed_candle["open_epoch"]),
                None,
            )
            if frame is None:
                raise ValueError("causal NexusTrade indicator frame is unavailable")
            return frame

        cycle = await self._clock.await_and_prepare(
            target,
            finalize_candle=finalize_candle,
            calculate_indicators=calculate_indicators,
            strategy=group,
        )
        self._last_boundary = target
        return cycle

    def apply_champion_mode(self, snapshot: dict) -> None:
        runtime = snapshot.get("runtime") or {}
        lanes = snapshot.get("lanes") or []
        next_versions = {
            Lane(item["lane"]): (item.get("version") or {}).get("id")
            for item in lanes
        }
        active_campaigns = snapshot.get("active_campaigns") or []
        next_trial_campaign = next(
            (
                item.get("id")
                for item in active_campaigns
                if item.get("lane") == Lane.TRIAL.value
            ),
            None,
        )
        if self._shared_demo_dispatcher is None:
            raise ValueError("the shared DEMO dispatcher is not configured")
        shared_identity = self._dispatcher_identity(self._shared_demo_dispatcher)
        if shared_identity[1] != "demo":
            raise ValueError("Trial dispatcher must remain on a DEMO account")
        enabled = runtime.get("champion_enabled", 0)
        if enabled not in {0, 1, False, True}:
            raise ValueError("champion_enabled must be boolean")
        desired_enabled = bool(enabled)
        current_dispatcher = self.dispatchers.get(Lane.CHAMPION)
        current_identity = (
            self._dispatcher_identity(current_dispatcher)
            if current_dispatcher is not None
            else (None, None)
        )
        if not desired_enabled:
            desired_identity = shared_identity
            desired_dispatcher = self._shared_demo_dispatcher
        else:
            account_type = str(runtime.get("champion_account_type") or "").lower()
            account_id = str(runtime.get("champion_account_id") or "").strip()
            ensure_account_allowed(account_type)
            if not account_id:
                raise ValueError("Champion ON requires a selected account")
            if self._champion_dispatcher_factory is None:
                raise ValueError("Champion ON dispatcher is not configured")
            desired_identity = (account_id, account_type)
        route_changes = (
            desired_enabled != self._champion_enabled
            or current_identity != desired_identity
        )
        if (
            route_changes
            and self.strategies[Lane.CHAMPION].state.position_status != "IDLE"
        ):
            raise ValueError(
                "Champion dispatcher can only switch at a safe boundary with an IDLE lane",
            )
        if desired_enabled:
            config = {
                "account_id": desired_identity[0],
                "account_type": desired_identity[1],
                "stake": float(self.bot.get("initial_stake", NEXUS_DEMO_STAKE)),
                "money_management": self.bot.get("money_management", "fixed"),
                "money_config": dict(self.bot.get("money_config") or {}),
                "risk_config": dict(self.bot.get("risk_config") or {}),
            }
            desired_dispatcher = self._champion_dispatcher_factory(config)
            if self._dispatcher_identity(desired_dispatcher) != desired_identity:
                raise ValueError(
                    "Champion dispatcher identity mismatch for current configuration",
                )

        # Commit the route only after all validation/factory work succeeds.
        self.dispatchers[Lane.TRIAL] = self._shared_demo_dispatcher
        self.dispatchers[Lane.CHAMPION] = desired_dispatcher
        self._champion_enabled = desired_enabled
        self._versions.update(next_versions)
        self._campaigns[Lane.TRIAL] = next_trial_campaign
        if not desired_enabled:
            if self._shared_demo_monitor is not None:
                self.monitors[Lane.CHAMPION] = self._shared_demo_monitor
        else:
            champion_monitor = self._champion_monitors.get(desired_identity)
            if champion_monitor is not None:
                self.monitors[Lane.CHAMPION] = champion_monitor
            elif self._champion_monitor is not None:
                self.monitors[Lane.CHAMPION] = self._champion_monitor
        if self._shared_demo_monitor is not None:
            self.monitors[Lane.TRIAL] = self._shared_demo_monitor
        for lane, dispatcher in self.dispatchers.items():
            if dispatcher is None:
                continue
            if hasattr(dispatcher, "set_lane_context"):
                dispatcher.set_lane_context(
                    lane,
                    nexus_version_id=self._versions[lane],
                    campaign_id=self._campaigns[lane],
                )
            dispatcher.set_emergency_stop(self._emergency_stop)

    @staticmethod
    def _dispatcher_identity(dispatcher) -> tuple[str, str]:
        account_id = str(getattr(dispatcher, "account_id", "") or "").strip()
        account_type = str(
            getattr(dispatcher, "account_type", "") or "",
        ).lower()
        if not account_id or account_type not in {"demo", "real"}:
            raise ValueError("dispatcher must expose an exact account identity")
        return account_id, account_type

    def set_emergency_stop(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise TypeError("emergency_stop must be boolean")
        self._emergency_stop = enabled
        seen = set()
        for dispatcher in self.dispatchers.values():
            if dispatcher is not None and id(dispatcher) not in seen:
                dispatcher.set_emergency_stop(enabled)
                seen.add(id(dispatcher))

    def _restore_champion_risk_state(self, state: dict) -> None:
        self._champion_money_manager.restore_state(state)
        # MoneyManager's legacy restore resets level-zero state to the initial
        # stake, but a level-zero Soros cycle can have a durable accumulated stake.
        if state.get("current_stake") is not None:
            self._champion_money_manager.current_stake = float(
                state["current_stake"],
            )

    async def process_cycle(self, cycle: CausalCycleResult) -> None:
        if type(cycle) is not CausalCycleResult:
            raise TypeError("process_cycle requires CausalCycleResult")
        if self._stop_event.is_set() or self._emergency_stop:
            return
        decisions = {decision.decision_id: decision for decision in cycle.decisions}
        for intent in cycle.intents:
            decision = decisions[intent.decision_id]
            lane = Lane(intent.lane)
            strategy = self.strategies[lane]
            if intent.status == "PENDING" and strategy.state.position_status == "IDLE":
                # This also supports restoration from a fully persisted causal result.
                strategy.state = replace(
                    strategy.state,
                    position_status="RESERVED",
                    owner_decision_id=intent.decision_id,
                )
            await self._record_decision(decision, strategy)
            if intent.status != "PENDING":
                await self._save_lane_state(lane)
                continue
            dispatcher = self.dispatchers.get(lane)
            if dispatcher is None:
                raise ValueError(f"dispatcher for {lane.value} is not configured")
            try:
                receipt = await dispatcher.submit(intent)
            except OwnershipQuarantineError as exc:
                strategy.mark_position_quarantined(
                    intent.decision_id, exc.correlation_id,
                )
            except (StaleIntentError, BuyRejectedError, DispatchBlockedError):
                if (
                    strategy.state.position_status == "RESERVED"
                    and strategy.state.owner_decision_id == intent.decision_id
                ):
                    strategy.release_reservation(intent.decision_id)
            else:
                strategy.mark_position_active(
                    intent.decision_id, receipt.contract_id,
                )
            await self._save_lane_state(lane)
            if strategy.state.position_status == "ACTIVE":
                await self._start_monitor(
                    lane,
                    intent.decision_id,
                    receipt.contract_id,
                    entry_delay_ms=int(round(
                        (receipt.accepted_epoch - intent.target_epoch) * 1000.0,
                    )),
                )

    async def _record_decision(self, decision, strategy) -> None:
        recorder = getattr(self.repository, "record_nexus_decision", None)
        if not callable(recorder):
            return
        payload = decision.to_dict()
        payload["id"] = payload["decision_id"]
        await recorder(
            payload,
            nexus_version_id=self._versions[Lane(decision.lane)],
            campaign_id=self._campaigns[Lane(decision.lane)],
            state=strategy.snapshot(),
        )

    async def _save_lane_state(self, lane: Lane) -> None:
        saver = getattr(self.repository, "save_nexus_lane_state", None)
        if callable(saver):
            await saver(lane.value, self.strategies[lane].snapshot())

    async def _start_monitor(
        self,
        lane: Lane,
        owner_decision_id: str,
        contract_id: int,
        *,
        entry_delay_ms=None,
    ) -> None:
        monitor = self.monitors.get(lane)
        if monitor is None:
            return

        async def on_settled(contract):
            payload = dict(contract)
            payload.setdefault("contract_id", contract_id)
            if entry_delay_ms is not None:
                payload.setdefault("entry_delay_ms", entry_delay_ms)
            await self.settle_contract(lane, owner_decision_id, payload)

        await monitor.monitor_contract(contract_id, on_settled)

    async def settle_contract(
        self,
        lane: Lane | str,
        owner_decision_id: str,
        contract: dict,
    ) -> None:
        lane = Lane(lane)
        contract_id = contract.get("contract_id") if isinstance(contract, dict) else None
        if isinstance(contract_id, bool) or type(contract_id) is not int or contract_id <= 0:
            raise ValueError("settlement requires a numeric contract_id")
        strategy = self.strategies[lane]
        if (
            strategy.state.position_status != "ACTIVE"
            or strategy.state.owner_decision_id != owner_decision_id
            or strategy.state.contract_id != contract_id
        ):
            raise ValueError("settlement does not own the active lane")
        stake = (
            NEXUS_DEMO_STAKE
            if lane is Lane.TRIAL
            else float(contract.get("buy_price", NEXUS_DEMO_STAKE))
        )
        trade = {
            "bot_id": self.bot_id,
            "session_id": None,
            "strategy_name": "nexus_trade",
            "symbol": "R_100",
            "contract_type": contract.get("contract_type"),
            "contract_id": contract_id,
            "stake": stake,
            "payout": contract.get("payout"),
            "profit": contract.get("profit"),
            "result": contract.get("status"),
            "status": "closed",
            "entry_spot": contract.get("entry_spot"),
            "exit_spot": contract.get("exit_spot"),
            "purchase_time": contract.get("date_start") or contract.get("purchase_time"),
            "expiry_time": contract.get("date_expiry") or contract.get("expiry_time"),
            "lane": lane.value,
            "nexus_version_id": self._versions[lane],
            "campaign_id": self._campaigns[lane],
            "decision_id": owner_decision_id,
            "entry_delay_ms": contract.get("entry_delay_ms"),
        }
        settled_state = replace(
            strategy.state,
            position_status="IDLE",
            owner_decision_id=None,
            contract_id=None,
        ).to_dict()
        apply_risk = lane is Lane.CHAMPION and self._champion_enabled
        atomic_settler = getattr(
            self.repository, "settle_nexus_trade_and_lane", None,
        )
        if callable(atomic_settler):
            result = await atomic_settler(
                trade,
                lane_state=settled_state,
                apply_risk=apply_risk,
                money_management=self.bot.get("money_management", "fixed"),
                money_config=dict(self.bot.get("money_config") or {}),
                risk_config=dict(self.bot.get("risk_config") or {}),
                initial_stake=float(self.bot.get("initial_stake", NEXUS_DEMO_STAKE)),
                settled_epoch=float(
                    contract.get("sell_time")
                    or contract.get("date_expiry")
                    or time.time()
                ),
            )
        else:
            await self.repository.upsert_trade(trade)
            result = None
            if (
                apply_risk
                and hasattr(self.repository, "settle_trade_and_risk")
            ):
                result = await self.repository.settle_trade_and_risk(
                    trade,
                    money_management=self.bot.get("money_management", "fixed"),
                    money_config=dict(self.bot.get("money_config") or {}),
                    risk_config=dict(self.bot.get("risk_config") or {}),
                    initial_stake=float(
                        self.bot.get("initial_stake", NEXUS_DEMO_STAKE),
                    ),
                    settled_epoch=float(
                        contract.get("sell_time")
                        or contract.get("date_expiry")
                        or time.time()
                    ),
                )
        if isinstance(result, dict) and isinstance(result.get("state"), dict):
            self._restore_champion_risk_state(result["state"])
        strategy.mark_position_closed(owner_decision_id, contract_id)
        dispatcher = self.dispatchers[lane]
        dispatcher.release_position(lane, contract_id)
        if not callable(atomic_settler):
            await self._save_lane_state(lane)

    async def _restore_lane_states(self) -> None:
        loader = getattr(self.repository, "load_nexus_lane_states", None)
        if callable(loader):
            stored = await loader()
            for lane in Lane:
                state = stored.get(lane.value) if isinstance(stored, dict) else None
                if state is not None:
                    self.strategies[lane] = NexusTradeStrategy(lane=lane, state=state)

    async def _recover_reserved_lanes(self) -> None:
        loader = getattr(self.repository, "list_nexus_recovery_intents", None)
        if not callable(loader):
            return
        journals = await loader(self.bot_id)
        for lane, strategy in self.strategies.items():
            state = strategy.state
            if state.position_status != "RESERVED":
                continue
            matches = [
                item
                for item in (journals or [])
                if item.get("lane") == lane.value
                and item.get("decision_id") == state.owner_decision_id
                and item.get("id") == f"nexus-{state.owner_decision_id}"
            ]
            if not matches:
                # The durable reservation precedes create_order_intent. With no
                # transport journal, no buy could have been sent.
                strategy.release_reservation(state.owner_decision_id)
                await self._save_lane_state(lane)
                continue
            if len(matches) != 1:
                correlation_id = f"nexus-{state.owner_decision_id}"
                strategy.mark_position_quarantined(
                    state.owner_decision_id, correlation_id,
                )
                await self._save_lane_state(lane)
                continue
            intent = matches[0]
            correlation_id = intent["id"]
            journal_state = intent.get("state")
            if journal_state == "owned":
                metadata = intent.get("metadata") or {}
                entry_intent = metadata.get("entry_intent") or {}
                contract_id = intent.get("contract_id")
                exact = (
                    metadata.get("correlation_id") == correlation_id
                    and entry_intent.get("decision_id") == state.owner_decision_id
                    and not isinstance(contract_id, bool)
                    and type(contract_id) is int
                    and contract_id > 0
                )
                if exact:
                    strategy.mark_position_active(
                        state.owner_decision_id, contract_id,
                    )
                else:
                    strategy.mark_position_quarantined(
                        state.owner_decision_id, correlation_id,
                    )
            elif journal_state in {"submitting", "reconcile_pending", "ambiguous"}:
                strategy.mark_position_quarantined(
                    state.owner_decision_id, correlation_id,
                )
            elif journal_state == "prepared":
                updater = getattr(self.repository, "update_order_intent", None)
                if callable(updater):
                    await updater(
                        correlation_id,
                        "cancelled",
                        error="restart occurred before Nexus buy transport",
                    )
                strategy.release_reservation(state.owner_decision_id)
            else:
                strategy.mark_position_quarantined(
                    state.owner_decision_id, correlation_id,
                )
            await self._save_lane_state(lane)

    def _restore_dispatcher_ownership(self) -> None:
        for lane, strategy in self.strategies.items():
            state = strategy.state
            if state.position_status == "ACTIVE":
                self.dispatchers[lane].restore_position(lane, state.contract_id)
            elif state.position_status == "QUARANTINED":
                self.dispatchers[lane].restore_quarantine(lane)

    async def _reconcile_quarantines(self) -> None:
        for lane, strategy in self.strategies.items():
            state = strategy.state
            if state.position_status != "QUARANTINED":
                continue
            dispatcher = self.dispatchers[lane]
            result = await dispatcher.reconcile_quarantine(
                state.quarantine_correlation_id,
                state.owner_decision_id,
            )
            if result is None:
                continue
            strategy.reconcile_quarantine(result)
            if result.outcome == "CONTRACT_FOUND":
                dispatcher.restore_position(lane, result.contract_id)
            await self._save_lane_state(lane)

    async def _resume_active_monitors(self) -> None:
        for lane, strategy in self.strategies.items():
            state = strategy.state
            if state.position_status == "ACTIVE":
                await self._start_monitor(
                    lane, state.owner_decision_id, state.contract_id,
                )

    async def _refresh_runtime_snapshot(self) -> None:
        getter = getattr(self.repository, "get_nexus_runtime_snapshot", None)
        if not callable(getter):
            return
        snapshot = await getter()
        if snapshot is None or snapshot == self._runtime_snapshot:
            return
        await self._provision_champion_dispatcher(snapshot)
        self.apply_champion_mode(snapshot)
        self._runtime_snapshot = snapshot

    async def _provision_champion_dispatcher(self, snapshot: dict) -> None:
        if not self._managed_champion_factory:
            return
        runtime = snapshot.get("runtime") or {}
        if not bool(runtime.get("champion_enabled", 0)):
            return
        identity = (
            str(runtime.get("champion_account_id") or "").strip(),
            str(runtime.get("champion_account_type") or "").lower(),
        )
        ensure_account_allowed(identity[1])
        current = self.dispatchers.get(Lane.CHAMPION)
        current_identity = (
            self._dispatcher_identity(current) if current is not None else (None, None)
        )
        route_changes = not self._champion_enabled or current_identity != identity
        if (
            route_changes
            and self.strategies[Lane.CHAMPION].state.position_status != "IDLE"
        ):
            raise ValueError(
                "Champion dispatcher can only switch at a safe boundary with an IDLE lane",
            )
        if identity in self._champion_dispatchers:
            return
        selected = next(
            (
                account for account in self._available_accounts
                if (account["account_id"], account["account_type"]) == identity
            ),
            None,
        )
        if selected is None:
            raise ValueError("selected Champion account is unavailable")
        if identity == self._dispatcher_identity(self._shared_demo_dispatcher):
            connection = self._shared_connection
            buy_lock = self._shared_buy_lock
            monitor = self._shared_demo_monitor
        else:
            auth = self._auth_factory()
            connection = self._connection_factory(auth)
            if not await connection.connect(identity[0]):
                await auth.close()
                raise ConnectionError("unable to connect the selected Champion account")
            self._connections.append(connection)
            buy_lock = asyncio.Lock()
            monitor = self._monitor_factory(connection)
        dispatcher = self._account_dispatcher_factory(
            connection,
            self.repository,
            account_id=identity[0],
            account_type=identity[1],
            stake=float(self.bot.get("initial_stake", NEXUS_DEMO_STAKE)),
            stake_provider=self._champion_money_manager.get_stake,
            buy_lock=buy_lock,
        )
        starter = getattr(dispatcher, "start", None)
        if callable(starter):
            await starter()
            self._dispatchers_started.add(id(dispatcher))
        self._champion_dispatchers[identity] = dispatcher
        self._champion_monitors[identity] = monitor

    async def run(self):
        self._stop_event.clear()
        try:
            await self.bootstrap()
            await self._restore_lane_states()
            self.apply_champion_mode(self._runtime_snapshot)
            await self._recover_reserved_lanes()
            self._restore_dispatcher_ownership()
            await self._reconcile_quarantines()
            await self._resume_active_monitors()
            while not self._stop_event.is_set():
                if hasattr(self.repository, "touch_bot_heartbeat"):
                    await self.repository.touch_bot_heartbeat(self.bot_id)
                produced = self._cycle_source(self)
                if inspect.isawaitable(produced):
                    cycle_task = asyncio.create_task(produced)
                    stop_task = asyncio.create_task(self._stop_event.wait())
                    done, _ = await asyncio.wait(
                        {cycle_task, stop_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if stop_task in done:
                        if not cycle_task.done():
                            cycle_task.cancel()
                        await asyncio.gather(cycle_task, return_exceptions=True)
                        break
                    stop_task.cancel()
                    await asyncio.gather(stop_task, return_exceptions=True)
                    cycle = await cycle_task
                else:
                    cycle = produced
                if self._stop_event.is_set():
                    break
                await self._refresh_runtime_snapshot()
                if cycle is not None and not self._stop_event.is_set():
                    await self.process_cycle(cycle)
                elif not self._stop_event.is_set():
                    await asyncio.sleep(0)
        finally:
            await self.close()


class _LaneStrategyGroup:
    def __init__(self, strategies):
        self._strategies = strategies

    def on_closed_candle(self, candle, indicators, *, causal_epoch):
        decisions = []
        for lane in Lane:
            decisions.extend(self._strategies[lane].on_closed_candle(
                candle, indicators, causal_epoch=causal_epoch,
            ))
        return decisions


__all__ = ["NexusTradeRuntime"]
