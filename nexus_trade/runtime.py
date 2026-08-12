"""Continuous isolated runtime for the two NexusTrade lanes."""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from dataclasses import replace

from config.settings import settings
from core.accounts import normalize_account
from core.events import runtime_event
from nexus_trade.artifacts import CandidateArtifact, canonical_json
from nexus_trade.clock import CausalCycleResult, EntryClock
from nexus_trade.constants import (
    NEXUS_DEMO_STAKE,
    NEXUS_DURATION_SECONDS,
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
from nexus_trade.repository import NexusTradeRepository
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
        publisher=None,
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
        self.publisher = publisher
        self._owns_publisher = False
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
        self._pending_runtime_snapshot = None
        self._lane_owners = {lane: None for lane in Lane}
        # Lock order is strict: runtime lane -> dispatcher internals (lane/buy)
        # -> repository transaction. Repository and dispatcher internals never
        # call back into the runtime while holding their locks, avoiding inversion.
        self._lane_locks = {lane: asyncio.Lock() for lane in Lane}
        self._champion_management = self._management_from_snapshot(
            runtime_snapshot,
        )
        self._champion_money_manager = self._money_manager_for(
            self._champion_management,
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

    def _management_from_snapshot(self, snapshot: dict | None) -> dict:
        raw = (snapshot or {}).get("champion_management")
        if raw is None:
            raw = {
                "revision": 1,
                "initial_stake": self.bot.get("initial_stake", NEXUS_DEMO_STAKE),
                "money_management": self.bot.get("money_management", "fixed"),
                "money_config": dict(self.bot.get("money_config") or {}),
                "risk_config": dict(self.bot.get("risk_config") or {}),
            }
        if type(raw) is not dict:
            raise ValueError("Champion management snapshot must be an object")
        revision = raw.get("revision")
        if isinstance(revision, bool) or type(revision) is not int or revision < 1:
            raise ValueError("Champion management revision is invalid")
        normalized = NexusTradeRepository._normalize_champion_management({
            "initial_stake": raw.get("initial_stake"),
            "money_management": raw.get("money_management"),
            "money_config": raw.get("money_config"),
            "risk_config": raw.get("risk_config"),
        })
        return {"revision": revision, **normalized}

    @staticmethod
    def _money_manager_for(management: dict) -> MoneyManager:
        money_config = management["money_config"]
        return MoneyManager(
            mode=management["money_management"],
            initial_stake=float(management["initial_stake"]),
            martingale_multiplier=float(money_config.get("multiplier", 2.0)),
            max_martingale_levels=int(money_config.get("max_levels", 3)),
            soros_levels=int(money_config.get("levels", 2)),
            soros_percent=float(money_config.get("percent", 0.5)),
        )

    def _champion_stake_for(
        self,
        account_type: str,
        *,
        manager: MoneyManager | None = None,
        management: dict | None = None,
    ) -> float:
        manager = manager or self._champion_money_manager
        management = management or self._champion_management
        stake = manager.get_stake()
        if isinstance(stake, bool) or type(stake) not in {int, float}:
            raise ValueError("managed stake must be numeric")
        stake = float(stake)
        if not math.isfinite(stake) or stake <= 0:
            raise ValueError("managed stake must be positive and finite")
        if account_type == "real" and (
            settings.REAL_MAX_STAKE_USD <= 0
            or stake > float(settings.REAL_MAX_STAKE_USD)
        ):
            raise ValueError("managed stake exceeds the REAL server cap")
        maximum = management["risk_config"].get("max_single_stake")
        if maximum is not None and stake > float(maximum):
            raise ValueError("managed stake exceeds max_single_stake")
        return stake

    async def _champion_risk_block_reason(self, *, now_epoch=None) -> str | None:
        risk = self._champion_management["risk_config"]
        loader = getattr(self.repository, "get_nexus_champion_daily_risk", None)
        daily = (
            await loader()
            if callable(loader)
            else {"profit": 0.0, "trades": 0, "last_settled_epoch": None}
        )
        if type(daily) is not dict:
            return "RISK_DATA_INVALID"
        try:
            profit = float(daily["profit"])
            trades = int(daily["trades"])
            last_settled = daily.get("last_settled_epoch")
            last_settled = int(last_settled) if last_settled is not None else None
        except (KeyError, TypeError, ValueError):
            return "RISK_DATA_INVALID"
        if not math.isfinite(profit) or trades < 0 or (last_settled is not None and last_settled < 0):
            return "RISK_DATA_INVALID"
        take_profit = risk.get("take_profit_daily")
        if take_profit is not None and float(take_profit) > 0 and profit >= float(take_profit):
            return "TAKE_PROFIT_DAILY"
        stop_loss = risk.get("stop_loss_daily")
        if stop_loss is not None and float(stop_loss) > 0 and profit <= -float(stop_loss):
            return "STOP_LOSS_DAILY"
        maximum_trades = risk.get("max_daily_trades")
        if maximum_trades is not None and trades >= int(maximum_trades):
            return "MAX_DAILY_TRADES"
        maximum_losses = risk.get("max_consecutive_losses")
        if (
            maximum_losses is not None
            and self._champion_money_manager.consecutive_losses >= int(maximum_losses)
        ):
            return "MAX_CONSECUTIVE_LOSSES"
        cooldown_minutes = risk.get("cooldown_minutes")
        now_epoch = int(time.time() if now_epoch is None else now_epoch)
        if (
            cooldown_minutes is not None
            and int(cooldown_minutes) > 0
            and last_settled is not None
            and now_epoch - last_settled < int(cooldown_minutes) * 60
        ):
            return "COOLDOWN"
        maximum_stake = risk.get("max_single_stake")
        if (
            maximum_stake is not None
            and float(self._champion_money_manager.get_stake()) > float(maximum_stake)
        ):
            return "MAX_SINGLE_STAKE"
        return None

    @property
    def stop_event(self):
        return self._stop_event

    async def request_stop(self):
        # Block both account queues synchronously before waking/cancelling the
        # current boundary wait.
        self.set_emergency_stop(True)
        self._stop_event.set()

    async def _start_event_publisher(self) -> None:
        if self.publisher is None:
            from core.event_publisher import HttpEventPublisher

            self.publisher = HttpEventPublisher()
            self._owns_publisher = True
        starter = getattr(self.publisher, "start", None)
        if callable(starter):
            result = starter()
            if inspect.isawaitable(result):
                await result

    def _snapshot_version(self, snapshot: dict | None = None) -> int:
        source = snapshot if snapshot is not None else self._runtime_snapshot
        bot = (source or {}).get("bot") or {}
        revision = bot.get("config_revision", 1)
        if isinstance(revision, bool) or type(revision) is not int or revision < 1:
            return 1
        return revision

    async def _publish_nexus_event(
        self,
        event_type: str,
        event_id: str,
        payload: dict,
        *,
        snapshot: dict | None = None,
    ) -> bool:
        if self.publisher is None:
            return False
        event = runtime_event(
            event_type,
            self.bot_id,
            event_id=event_id,
            schema_version=1,
            snapshot_version=self._snapshot_version(snapshot),
            payload=payload,
        )
        return bool(await self.publisher.publish(event))

    @staticmethod
    def _optional_position_number(value):
        if value is None or isinstance(value, bool) or type(value) not in {int, float}:
            return None
        normalized = float(value)
        return normalized if math.isfinite(normalized) else None

    async def _publish_position(
        self,
        *,
        lane: Lane,
        owner_decision_id: str,
        contract_id: int,
        status: str,
        update_epoch: int,
        **values,
    ) -> bool:
        payload = {
            "lane": lane.value,
            "owner_decision_id": owner_decision_id,
            "contract_id": contract_id,
            "status": status,
            "update_epoch": int(update_epoch),
        }
        for field in (
            "stake", "buy_price", "entry_spot", "current_spot", "exit_spot", "profit",
        ):
            if field in values:
                payload[field] = self._optional_position_number(values[field])
        if values.get("date_expiry") is not None:
            try:
                payload["date_expiry"] = int(values["date_expiry"])
            except (TypeError, ValueError):
                payload["date_expiry"] = None
        if values.get("purchase_time") is not None:
            try:
                payload["purchase_time"] = int(values["purchase_time"])
            except (TypeError, ValueError):
                payload["purchase_time"] = None
        for field in ("result", "contract_type", "entry_delay_ms"):
            if values.get(field) is not None:
                payload[field] = values[field]
        try:
            return await self._publish_nexus_event(
                "nexus.position",
                (
                    f"nexus.position:{lane.value}:{contract_id}:"
                    f"{int(update_epoch)}:{status}"
                ),
                payload,
            )
        except Exception:
            # Position telemetry is post-buy observability. A publisher outage
            # must never repeat, cancel, or quarantine an already accepted order.
            return False

    async def _publish_snapshot_transitions(
        self,
        previous: dict | None,
        current: dict,
        *,
        applied: bool,
    ) -> None:
        previous = previous or {}
        revision = self._snapshot_version(current)
        previous_runtime = previous.get("runtime") or {}
        current_runtime = current.get("runtime") or {}
        previous_management = previous.get("champion_management")
        current_management = current.get("champion_management")
        if (
            current_runtime != previous_runtime
            or current_management != previous_management
        ):
            await self._publish_nexus_event(
                "nexus.runtime",
                f"nexus.runtime:{revision}",
                {
                    "runtime": current_runtime,
                    "champion_management": current_management,
                    "applied": bool(applied),
                },
                snapshot=current,
            )

        previous_campaigns = {
            item.get("id"): item
            for item in (previous.get("active_campaigns") or [])
            if item.get("id")
        }
        current_campaigns = {
            item.get("id"): item
            for item in (current.get("active_campaigns") or [])
            if item.get("id")
        }
        for campaign_id, campaign in current_campaigns.items():
            if previous_campaigns.get(campaign_id) != campaign:
                await self._publish_nexus_event(
                    "nexus.campaign",
                    f"nexus.campaign:{campaign_id}:{campaign.get('status', 'ACTIVE')}",
                    campaign,
                    snapshot=current,
                )

        def lane_versions(snapshot):
            return {
                item.get("lane"): (item.get("version") or {})
                for item in (snapshot.get("lanes") or [])
                if item.get("lane")
            }

        previous_versions = lane_versions(previous)
        current_versions = lane_versions(current)
        for lane, version in current_versions.items():
            if previous_versions.get(lane) != version:
                version_id = version.get("id", "missing")
                await self._publish_nexus_event(
                    "nexus.version_changed",
                    f"nexus.version_changed:{lane}:{version_id}",
                    {"lane": lane, "version": version},
                    snapshot=current,
                )

        trial_lane = Lane.TRIAL.value
        previous_trial = (
            previous_versions.get(trial_lane),
            next(iter(previous_campaigns), None),
        )
        current_trial = (
            current_versions.get(trial_lane),
            next(iter(current_campaigns), None),
        )
        if current_trial != previous_trial:
            trial_version = (current_versions.get(trial_lane) or {}).get("id", "missing")
            campaign_id = current_trial[1] or "none"
            await self._publish_nexus_event(
                "nexus.trial_changed",
                f"nexus.trial_changed:{trial_version}:{campaign_id}",
                {
                    "lane": trial_lane,
                    "version": current_versions.get(trial_lane),
                    "campaign": current_campaigns.get(current_trial[1]),
                },
                snapshot=current,
            )

    async def bootstrap(self, *, apply_snapshot: bool = True) -> None:
        if self._bootstrapped:
            if apply_snapshot and self._runtime_snapshot is not None:
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
                float(self._champion_management["initial_stake"]),
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
        durable_demo_owners = [
            owner["account_id"]
            for lane in (Lane.TRIAL, Lane.CHAMPION)
            for owner in [self._lane_owners.get(lane)]
            if owner is not None
            and owner["account_type"] == "demo"
            and not owner["management_active"]
            and self.strategies[lane].state.position_status != "IDLE"
        ]
        preferred_demo = (
            durable_demo_owners[0]
            if durable_demo_owners
            else str(runtime.get("champion_account_id") or "")
        )
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
        if champion_enabled and apply_snapshot:
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
                stake=self._champion_stake_for(selected["account_type"]),
                stake_provider=(
                    lambda account_type=selected["account_type"]:
                    self._champion_stake_for(account_type)
                ),
                buy_lock=champion_lock,
                management_active=True,
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
        if apply_snapshot:
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
        if self._owns_publisher and self.publisher is not None:
            await self.publisher.close()
            self.publisher = None

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

    def apply_champion_mode(self, snapshot: dict) -> bool:
        runtime = snapshot.get("runtime") or {}
        self._apply_persisted_emergency_stop(runtime)
        next_management = self._management_from_snapshot(snapshot)
        management_changes = next_management != self._champion_management
        next_money_manager = (
            self._money_manager_for(next_management)
            if management_changes else self._champion_money_manager
        )
        lanes = snapshot.get("lanes") or []
        next_versions = {
            Lane(item["lane"]): (item.get("version") or {}).get("id")
            for item in lanes
        }
        next_gates = {
            Lane(item["lane"]): self._gate_from_version(item.get("version") or {})
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
        if (
            management_changes
            and self.strategies[Lane.CHAMPION].state.position_status != "IDLE"
        ):
            self._pending_runtime_snapshot = snapshot
            return False
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
            initial_stake = self._champion_stake_for(
                account_type,
                manager=next_money_manager,
                management=next_management,
            )
            config = {
                "account_id": desired_identity[0],
                "account_type": desired_identity[1],
                "stake": initial_stake,
                "stake_provider": (
                    lambda account_type=account_type:
                    self._champion_stake_for(account_type)
                ),
                "money_management": next_management["money_management"],
                "money_config": dict(next_management["money_config"]),
                "risk_config": dict(next_management["risk_config"]),
                "management_active": True,
            }
            desired_dispatcher = (
                None
                if management_changes
                else self._champion_dispatchers.get(desired_identity)
            )
            if desired_dispatcher is None:
                desired_dispatcher = self._champion_dispatcher_factory(config)
            if self._dispatcher_identity(desired_dispatcher) != desired_identity:
                raise ValueError(
                    "Champion dispatcher identity mismatch for current configuration",
                )
            desired_dispatcher.set_emergency_stop(self._emergency_stop)
            self._champion_dispatchers[desired_identity] = desired_dispatcher

        route_changes = (
            desired_enabled != self._champion_enabled
            or current_identity != desired_identity
            or desired_dispatcher is not current_dispatcher
        )
        version_changes = {
            lane
            for lane in Lane
            if self._versions[lane] is not None
            and self._versions[lane] != next_versions.get(lane)
        }
        gate_changes = {
            lane
            for lane in Lane
            if getattr(self.strategies[lane].gate, "artifact_hash", None)
            != getattr(next_gates.get(lane), "artifact_hash", None)
        }
        strategy_changes = version_changes | gate_changes
        if (
            (
                route_changes
                and self.strategies[Lane.CHAMPION].state.position_status != "IDLE"
            )
            or (
                management_changes
                and self.strategies[Lane.CHAMPION].state.position_status != "IDLE"
            )
            or any(
                self.strategies[lane].state.position_status != "IDLE"
                for lane in strategy_changes
            )
        ):
            self._pending_runtime_snapshot = snapshot
            return False

        # Commit the route only after all validation/factory work succeeds.
        if management_changes:
            self._champion_management = next_management
            self._champion_money_manager = next_money_manager
            if desired_enabled:
                self._champion_dispatchers = {desired_identity: desired_dispatcher}
            else:
                self._champion_dispatchers = {}
        for lane in Lane:
            if (
                self._versions[lane] != next_versions.get(lane)
                or lane in gate_changes
            ):
                self.strategies[lane] = NexusTradeStrategy(
                    lane=lane,
                    state=self.strategies[lane].state,
                    gate=next_gates.get(lane),
                )
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
            if champion_monitor is None:
                champion_monitor = getattr(desired_dispatcher, "monitor", None)
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
        self._pending_runtime_snapshot = None
        return True

    @staticmethod
    def _gate_from_version(version: dict):
        snapshot = version.get("snapshot")
        if not isinstance(snapshot, dict) or "artifact" not in snapshot:
            return None
        artifact = CandidateArtifact.from_json(canonical_json(snapshot["artifact"]))
        return artifact.executable_gate()

    def _apply_persisted_emergency_stop(self, runtime: dict) -> None:
        if "emergency_stop" not in runtime:
            return
        emergency_stop = runtime["emergency_stop"]
        if emergency_stop not in {0, 1, False, True}:
            raise ValueError("emergency_stop must be boolean")
        self.set_emergency_stop(bool(emergency_stop))

    @staticmethod
    def _dispatcher_identity(dispatcher) -> tuple[str, str]:
        account_id = str(getattr(dispatcher, "account_id", "") or "").strip()
        account_type = str(
            getattr(dispatcher, "account_type", "") or "",
        ).lower()
        if not account_id or account_type not in {"demo", "real"}:
            raise ValueError("dispatcher must expose an exact account identity")
        return account_id, account_type

    @staticmethod
    def _normalize_lane_owner(owner) -> dict | None:
        if owner is None:
            return None
        account_id = str(owner.get("account_id") or "").strip()
        account_type = str(owner.get("account_type") or "").lower()
        management_active = owner.get("management_active")
        if not account_id or account_type not in {"demo", "real"}:
            raise ValueError("lane owner requires an exact account identity")
        if type(management_active) is not bool:
            raise TypeError("lane owner management_active must be boolean")
        return {
            "account_id": account_id,
            "account_type": account_type,
            "management_active": management_active,
        }

    def _owner_for_dispatcher(self, dispatcher, *, management_active: bool) -> dict:
        account_id, account_type = self._dispatcher_identity(dispatcher)
        return self._normalize_lane_owner({
            "account_id": account_id,
            "account_type": account_type,
            "management_active": management_active,
        })

    def _dispatcher_for_owner(self, owner: dict):
        owner = self._normalize_lane_owner(owner)
        identity = (owner["account_id"], owner["account_type"])
        shared_identity = self._dispatcher_identity(self._shared_demo_dispatcher)
        if not owner["management_active"]:
            if identity != shared_identity:
                raise ValueError("unmanaged Nexus ownership must remain on shared DEMO")
            return self._shared_demo_dispatcher
        dispatcher = self._champion_dispatchers.get(identity)
        if dispatcher is None:
            if self._champion_dispatcher_factory is None:
                raise ValueError("owner dispatcher is unavailable during restart")
            dispatcher = self._champion_dispatcher_factory({
                "account_id": identity[0],
                "account_type": identity[1],
                # This dispatcher owns a contract that was accepted before the
                # restart. Reconstructing it must not be mistaken for approval
                # of a new REAL buy; the provider below still gates every future buy.
                "stake": float(self._champion_management["initial_stake"]),
                "stake_provider": (
                    lambda account_type=identity[1]:
                    self._champion_stake_for(account_type)
                ),
                "money_management": self._champion_management["money_management"],
                "money_config": dict(self._champion_management["money_config"]),
                "risk_config": dict(self._champion_management["risk_config"]),
                "management_active": True,
                "restoring_owner": True,
            })
            if self._dispatcher_identity(dispatcher) != identity:
                raise ValueError("restored owner dispatcher identity mismatch")
            self._champion_dispatchers[identity] = dispatcher
        return dispatcher

    async def _restore_lane_owners(self) -> None:
        loader = getattr(self.repository, "load_nexus_lane_owners", None)
        if callable(loader):
            stored = await loader()
            for lane in Lane:
                raw = stored.get(lane.value) if isinstance(stored, dict) else None
                if raw is not None:
                    self._lane_owners[lane] = self._normalize_lane_owner(raw)

        journal_loader = getattr(self.repository, "list_nexus_recovery_intents", None)
        self._recovery_journals = (
            await journal_loader(self.bot_id) if callable(journal_loader) else []
        )
        for lane, strategy in self.strategies.items():
            if strategy.state.position_status == "IDLE":
                continue
            decision_id = strategy.state.owner_decision_id
            expected_id = f"nexus-{decision_id}"
            matches = [
                item for item in (self._recovery_journals or [])
                if item.get("lane") == lane.value
                and item.get("decision_id") == decision_id
                and item.get("id") == expected_id
            ]
            journal_owner = None
            if len(matches) == 1:
                if matches[0].get("nexus_version_id") is not None:
                    self._versions[lane] = matches[0].get("nexus_version_id")
                self._campaigns[lane] = matches[0].get("campaign_id")
                metadata = matches[0].get("metadata") or {}
                exact = (
                    metadata.get("correlation_id") == expected_id
                    and metadata.get("order_intent_id") == expected_id
                    and metadata.get("decision_id") == decision_id
                    and (metadata.get("entry_intent") or {}).get("decision_id") == decision_id
                    and matches[0].get("account_id") == metadata.get("account_id")
                )
                if exact and all(
                    key in metadata
                    for key in ("account_id", "account_type", "management_active")
                ):
                    journal_owner = self._normalize_lane_owner(metadata)
            durable_owner = self._lane_owners[lane]
            if durable_owner is not None and journal_owner is not None:
                if durable_owner != journal_owner:
                    raise ValueError("lane and ownership journals disagree on owner identity")
            elif journal_owner is not None:
                self._lane_owners[lane] = journal_owner

    async def _route_restored_owners(self) -> None:
        for lane, strategy in self.strategies.items():
            if strategy.state.position_status == "IDLE":
                continue
            owner = self._lane_owners[lane]
            if owner is None:
                raise ValueError("non-IDLE Nexus lane is missing its durable owner")
            if lane is Lane.TRIAL and owner["management_active"]:
                raise ValueError("Trial owner can never enable money management")
            identity = (owner["account_id"], owner["account_type"])
            if (
                lane is Lane.CHAMPION
                and owner["management_active"]
                and self._managed_champion_factory
                and identity not in self._champion_dispatchers
            ):
                await self._provision_champion_dispatcher(
                    self._runtime_snapshot,
                    restoring_owner=owner,
                )
            dispatcher = self._dispatcher_for_owner(owner)
            self.dispatchers[lane] = dispatcher
            if lane is Lane.CHAMPION:
                self._champion_enabled = owner["management_active"]
            monitor = self._champion_monitors.get(identity)
            if monitor is None:
                monitor = getattr(dispatcher, "monitor", None)
            if monitor is not None:
                self.monitors[lane] = monitor

    def set_emergency_stop(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise TypeError("emergency_stop must be boolean")
        self._emergency_stop = enabled
        seen = set()
        for dispatcher in [
            *self.dispatchers.values(),
            *self._champion_dispatchers.values(),
        ]:
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
            async with self._lane_locks[lane]:
                monitor_request = await self._process_lane_intent(
                    lane,
                    decision,
                    intent,
                )
            if monitor_request is not None:
                owner_decision_id, contract_id, entry_delay_ms = monitor_request
                await self._start_monitor(
                    lane,
                    owner_decision_id,
                    contract_id,
                    entry_delay_ms=entry_delay_ms,
                )

    async def _process_lane_intent(self, lane, decision, intent):
        """Mutate and persist one lane while its runtime lock is held."""
        strategy = self.strategies[lane]
        if intent.status == "PENDING" and strategy.state.position_status == "IDLE":
            # This also supports restoration from a fully persisted causal result.
            strategy.state = replace(
                strategy.state,
                position_status="RESERVED",
                owner_decision_id=intent.decision_id,
            )
        execution_blocked_reason = None
        if (
            intent.status == "PENDING"
            and lane is Lane.CHAMPION
            and self._champion_enabled
        ):
            execution_blocked_reason = await self._champion_risk_block_reason()
            if execution_blocked_reason is not None:
                strategy.release_reservation(intent.decision_id)
                self._lane_owners[lane] = None
                await self._record_decision(
                    decision,
                    strategy,
                    execution_blocked_reason=execution_blocked_reason,
                )
                await self._save_lane_state(lane)
                return None
        if intent.status == "PENDING":
            dispatcher = self.dispatchers.get(lane)
            if dispatcher is None:
                raise ValueError(f"dispatcher for {lane.value} is not configured")
            self._lane_owners[lane] = self._owner_for_dispatcher(
                dispatcher,
                management_active=(lane is Lane.CHAMPION and self._champion_enabled),
            )
        await self._record_decision(decision, strategy)
        if intent.status != "PENDING":
            await self._save_lane_state(lane)
            return None
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
                self._lane_owners[lane] = None
        else:
            strategy.mark_position_active(
                intent.decision_id, receipt.contract_id,
            )
        await self._save_lane_state(lane)
        if strategy.state.position_status != "ACTIVE":
            return None
        stake = (
            self._champion_stake_for(dispatcher.account_type)
            if lane is Lane.CHAMPION and self._champion_enabled
            else NEXUS_DEMO_STAKE
        )
        await self._publish_position(
            lane=lane,
            owner_decision_id=intent.decision_id,
            contract_id=receipt.contract_id,
            status="OPEN",
            update_epoch=int(receipt.accepted_epoch),
            stake=stake,
            buy_price=stake,
            contract_type=intent.contract_type,
            entry_delay_ms=int(round(
                (receipt.accepted_epoch - intent.target_epoch) * 1000.0,
            )),
            date_expiry=intent.target_epoch + NEXUS_DURATION_SECONDS,
        )
        return (
            intent.decision_id,
            receipt.contract_id,
            int(round(
                (receipt.accepted_epoch - intent.target_epoch) * 1000.0,
            )),
        )

    async def _record_decision(
        self,
        decision,
        strategy,
        *,
        execution_blocked_reason: str | None = None,
    ) -> None:
        recorder = getattr(self.repository, "record_nexus_decision", None)
        if not callable(recorder):
            return
        payload = decision.to_dict()
        payload["id"] = payload["decision_id"]
        if execution_blocked_reason is not None:
            payload["execution_blocked_reason"] = execution_blocked_reason
        lane = Lane(decision.lane)
        state = strategy.snapshot()
        await recorder(
            payload,
            nexus_version_id=self._versions[lane],
            campaign_id=self._campaigns[lane],
            state=state,
            owner=self._lane_owners[lane],
        )
        await self._publish_nexus_event(
            "nexus.decision",
            f"nexus.decision:{decision.decision_id}",
            {
                **payload,
                "nexus_version_id": self._versions[lane],
                "campaign_id": self._campaigns[lane],
                "state": state,
            },
        )

    async def _save_lane_state(self, lane: Lane) -> None:
        saver = getattr(self.repository, "save_nexus_lane_state", None)
        if callable(saver):
            await saver(
                lane.value,
                self.strategies[lane].snapshot(),
                owner=self._lane_owners[lane],
            )

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

        async def on_update(contract):
            if not isinstance(contract, dict):
                return
            strategy = self.strategies[lane]
            if (
                strategy.state.position_status != "ACTIVE"
                or strategy.state.owner_decision_id != owner_decision_id
                or strategy.state.contract_id != contract_id
            ):
                return
            update_epoch = int(
                contract.get("current_spot_time")
                or contract.get("date_start")
                or time.time()
            )
            await self._publish_position(
                lane=lane,
                owner_decision_id=owner_decision_id,
                contract_id=contract_id,
                status="UPDATED",
                update_epoch=update_epoch,
                stake=contract.get("buy_price"),
                buy_price=contract.get("buy_price"),
                entry_spot=contract.get("entry_spot"),
                current_spot=contract.get("current_spot"),
                profit=contract.get("profit"),
                date_expiry=contract.get("date_expiry"),
                purchase_time=contract.get("date_start") or contract.get("purchase_time"),
                contract_type=contract.get("contract_type"),
            )

        await monitor.monitor_contract(
            contract_id,
            on_settled,
            on_update_callback=on_update,
        )

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
        async with self._lane_locks[lane]:
            settlement = asyncio.create_task(
                self._settle_contract_locked(
                    lane,
                    owner_decision_id,
                    contract,
                    contract_id,
                ),
            )
            cancellation = None
            while True:
                try:
                    await asyncio.shield(settlement)
                    break
                except asyncio.CancelledError as exc:
                    # Once settlement persistence starts, keep the runtime lock
                    # until durable and in-memory ownership converge. The caller's
                    # cancellation is re-raised only after that barrier completes.
                    if cancellation is None:
                        cancellation = exc
                    if settlement.done():
                        settlement.result()
                        break
            if cancellation is not None:
                raise cancellation

    async def _settle_contract_locked(
        self,
        lane: Lane,
        owner_decision_id: str,
        contract: dict,
        contract_id: int,
    ) -> None:
        """Persist and close a settlement while the lane lock remains held."""
        strategy = self.strategies[lane]
        if (
            strategy.state.position_status != "ACTIVE"
            or strategy.state.owner_decision_id != owner_decision_id
            or strategy.state.contract_id != contract_id
        ):
            raise ValueError("settlement does not own the active lane")
        owner = self._lane_owners[lane]
        if owner is None:
            owner = self._owner_for_dispatcher(
                self.dispatchers[lane],
                management_active=(lane is Lane.CHAMPION and self._champion_enabled),
            )
            self._lane_owners[lane] = owner
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
        apply_risk = lane is Lane.CHAMPION and owner["management_active"]
        atomic_settler = getattr(
            self.repository, "settle_nexus_trade_and_lane", None,
        )
        if callable(atomic_settler):
            result = await atomic_settler(
                trade,
                lane_state=settled_state,
                apply_risk=apply_risk,
                money_management=self._champion_management["money_management"],
                money_config=dict(self._champion_management["money_config"]),
                risk_config=dict(self._champion_management["risk_config"]),
                initial_stake=float(self._champion_management["initial_stake"]),
                settled_epoch=float(
                    contract.get("sell_time")
                    or contract.get("date_expiry")
                    or time.time()
                ),
                owner=owner,
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
                    money_management=self._champion_management["money_management"],
                    money_config=dict(self._champion_management["money_config"]),
                    risk_config=dict(self._champion_management["risk_config"]),
                    initial_stake=float(self._champion_management["initial_stake"]),
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
        self._lane_owners[lane] = None
        if not callable(atomic_settler):
            await self._save_lane_state(lane)
        closed_epoch = int(
            contract.get("sell_time")
            or contract.get("date_expiry")
            or time.time()
        )
        await self._publish_position(
            lane=lane,
            owner_decision_id=owner_decision_id,
            contract_id=contract_id,
            status="CLOSED",
            update_epoch=closed_epoch,
            stake=stake,
            buy_price=contract.get("buy_price"),
            current_spot=contract.get("exit_spot") or contract.get("current_spot"),
            profit=contract.get("profit"),
            date_expiry=contract.get("date_expiry"),
            result=contract.get("status"),
            contract_type=contract.get("contract_type"),
        )
        if self._pending_runtime_snapshot is not None:
            pending = self._pending_runtime_snapshot
            if self._managed_champion_factory:
                await self._provision_champion_dispatcher(pending)
            self.apply_champion_mode(pending)
        await self._publish_nexus_event(
            "nexus.trade",
            f"nexus.trade:{lane.value}:{contract_id}",
            trade,
        )

    async def _restore_lane_states(self) -> None:
        loader = getattr(self.repository, "load_nexus_lane_states", None)
        if callable(loader):
            stored = await loader()
            for lane in Lane:
                state = stored.get(lane.value) if isinstance(stored, dict) else None
                if state is not None:
                    self.strategies[lane] = NexusTradeStrategy(lane=lane, state=state)

    async def _recover_reserved_lanes(self) -> None:
        journals = getattr(self, "_recovery_journals", None)
        if journals is None:
            loader = getattr(self.repository, "list_nexus_recovery_intents", None)
            journals = await loader(self.bot_id) if callable(loader) else []
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
                self._lane_owners[lane] = None
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
                try:
                    metadata_owner = self._normalize_lane_owner(metadata)
                except (TypeError, ValueError):
                    metadata_owner = None
                exact = (
                    metadata.get("correlation_id") == correlation_id
                    and metadata.get("order_intent_id") == correlation_id
                    and metadata.get("decision_id") == state.owner_decision_id
                    and entry_intent.get("decision_id") == state.owner_decision_id
                    and intent.get("account_id") == metadata.get("account_id")
                    and metadata_owner == self._lane_owners[lane]
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
                self._lane_owners[lane] = None
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
        previous = self._runtime_snapshot
        snapshot = await getter()
        if snapshot is None:
            return
        # Emergency is a total buy gate, not a route change. It must take
        # effect even while Champion ownership defers account/version changes.
        self._apply_persisted_emergency_stop(snapshot.get("runtime") or {})
        if snapshot == self._runtime_snapshot:
            return
        if self.strategies[Lane.CHAMPION].state.position_status != "IDLE":
            self._pending_runtime_snapshot = snapshot
            self._runtime_snapshot = snapshot
            await self._publish_snapshot_transitions(
                previous, snapshot, applied=False,
            )
            return
        await self._provision_champion_dispatcher(snapshot)
        applied = self.apply_champion_mode(snapshot)
        self._runtime_snapshot = snapshot
        await self._publish_snapshot_transitions(
            previous, snapshot, applied=applied,
        )

    async def _provision_champion_dispatcher(
        self, snapshot: dict, *, restoring_owner: dict = None,
    ) -> None:
        if not self._managed_champion_factory:
            return
        if restoring_owner is not None:
            owner = self._normalize_lane_owner(restoring_owner)
            identity = (owner["account_id"], owner["account_type"])
        else:
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
            restoring_owner is None
            and
            route_changes
            and self.strategies[Lane.CHAMPION].state.position_status != "IDLE"
        ):
            self._pending_runtime_snapshot = snapshot
            return
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
            stake=(
                float(self._champion_management["initial_stake"])
                if restoring_owner is not None
                else self._champion_stake_for(identity[1])
            ),
            stake_provider=(
                lambda account_type=identity[1]:
                self._champion_stake_for(account_type)
            ),
            buy_lock=buy_lock,
            management_active=True,
        )
        starter = getattr(dispatcher, "start", None)
        if callable(starter):
            await starter()
            self._dispatchers_started.add(id(dispatcher))
        self._champion_dispatchers[identity] = dispatcher
        self._champion_monitors[identity] = monitor

    async def run(self):
        if self._stop_event.is_set():
            return
        try:
            await self._start_event_publisher()
            await self._restore_lane_states()
            await self._restore_lane_owners()
            await self.bootstrap(apply_snapshot=False)
            await self._route_restored_owners()
            await self._recover_reserved_lanes()
            self._restore_dispatcher_ownership()
            await self._reconcile_quarantines()
            await self._resume_active_monitors()
            if self._managed_champion_factory:
                await self._provision_champion_dispatcher(self._runtime_snapshot)
            applied = False
            if not (
                self._managed_champion_factory
                and self._pending_runtime_snapshot is self._runtime_snapshot
            ):
                applied = self.apply_champion_mode(self._runtime_snapshot)
            await self._publish_snapshot_transitions(
                None, self._runtime_snapshot, applied=applied,
            )
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
