import asyncio
import time
import uuid

from core.auth import AuthManager
from core.accounts import validate_selected_account
from core.connection import NexusConnection
from core.event_publisher import HttpEventPublisher
from core.events import runtime_event
from config.settings import settings
from core.recovery import CrashRecoveryHandler
from data.market_data import MarketDataHandler
from risk.circuit_breaker import CircuitBreaker
from risk.risk_manager import RiskManager
from strategies.base import MoneyManager

from strategies.donchian_zigzag import DonchianZigZagStrategy
from strategies.nexus_speed import NexusSpeedStrategy
from trading.monitor import ContractMonitor
from trading.ownership import ActiveOrderIntentError, OrderOwnershipCoordinator
from trading.proposal import ProposalManager
from trading.safety import ensure_account_allowed
from utils.logger import setup_logger

logger = setup_logger("BotSession")


class BotSession:
    """Executes one persisted bot without sharing mutable trading state."""

    def __init__(self, repository, bot, publisher=None):
        self.repository = repository
        self.bot = dict(bot)
        self.bot_id = self.bot["id"]
        self.publisher = publisher or HttpEventPublisher()
        self._owns_publisher = publisher is None
        self._stop_requested = asyncio.Event()
        self._active_contracts = set()
        self._session_id = str(uuid.uuid4())
        self._connection = None
        self._market_data = None
        self._last_trade_at = 0.0

    async def request_stop(self):
        """Stop opening positions immediately; existing contracts may settle safely."""
        self._stop_requested.set()
        await self._set_status("STOPPING")

    async def _publish(self, event_type, **payload):
        await self.publisher.publish(runtime_event(event_type, self.bot_id, **payload))

    async def _set_status(self, status, error=None):
        await self.repository.set_runtime_state(self.bot_id, status, error)
        await self._publish("runtime.status", status=status, error=error)

    async def _foreign_account_unresolved_intents(self):
        if not hasattr(self.repository, "list_unresolved_order_intents"):
            return []
        account_id = self.bot.get("account_id")
        if not account_id:
            return []
        unresolved = await self.repository.list_unresolved_order_intents(
            account_id=account_id,
        )
        return [
            item for item in unresolved
            if item.get("bot_id") and item.get("bot_id") != self.bot_id
        ]

    def _build_strategy(self):
        strategy_id = self.bot.get("strategy_id", "donchian")
        if strategy_id not in ("donchian", "nexus_speed"):
            raise ValueError(f"Estrategia nao suportada: {strategy_id}")

        strategy_config = self.bot.get("strategy_config") or {}
        money_config = self.bot.get("money_config") or {}
        money = MoneyManager(
            mode=self.bot.get("money_management", "fixed"),
            initial_stake=float(self.bot.get("initial_stake", 1.0)),
            martingale_multiplier=float(money_config.get("multiplier", 2.0)),
            max_martingale_levels=int(money_config.get("max_levels", 3)),
            soros_levels=int(money_config.get("levels", 2)),
            soros_percent=float(money_config.get("percent", 0.5)),
        )
        
        if strategy_id == "donchian":
            return DonchianZigZagStrategy(
                money_manager=money,
            )
        if self.bot.get("duration_unit", "t") != "t":
            raise ValueError("Nexus Speed usa expiracao fixa de 5 ticks")
        min_profit_ratio = float(strategy_config.get("min_profit_ratio", 0.87))
        if min_profit_ratio < 0.87:
            raise ValueError("Nexus Speed exige min_profit_ratio >= 0.87")
        return NexusSpeedStrategy(
            money_manager=money,
            duration=int(self.bot.get("duration", 5)),
            adx_threshold=strategy_config.get("adx_threshold", 30),
            touch_tolerance_bps=float(
                strategy_config.get("touch_tolerance_bps", 0.0)
            ),
            ema_flat_tolerance_pips=float(
                strategy_config.get("ema_flat_tolerance_pips", 1.0)
            ),
            min_profit_ratio=min_profit_ratio,
            max_entry_delay_ticks=int(
                strategy_config.get("max_entry_delay_ticks", 1)
            ),
            min_closed_candles=int(strategy_config.get("min_closed_candles", 270)),
        )

    @staticmethod
    def _nexus_trade_block_reason(strategy, signal, proposal, latest):
        if not isinstance(strategy, NexusSpeedStrategy):
            return None
        latest_sequence = latest.get("sequence") if latest else None
        if signal.tick_sequence is None or latest_sequence is None:
            return "signal_stale_by_ticks"
        if int(latest_sequence) - int(signal.tick_sequence) > strategy.max_entry_delay_ticks:
            return "signal_stale_by_ticks"
        profit_ratio = ProposalManager.profit_ratio(proposal)
        if profit_ratio is None:
            return "proposal_profit_ratio_unavailable"
        if profit_ratio + 1e-12 < strategy.min_profit_ratio:
            return "profit_ratio_below_minimum"
        return None

    async def run(self):
        ensure_account_allowed(self.bot)
        if not self.bot.get("account_id"):
            raise ValueError("Configure uma conta Deriv antes de iniciar o robo")

        await self.publisher.start()
        await self.repository.create_session(self._session_id)
        await self._set_status("STARTING")
        auth = AuthManager()
        self._connection = NexusConnection(auth)
        monitor = None

        try:
            accounts = await auth.list_accounts()
            account = next(
                (
                    item for item in accounts
                    if item.get("account_id") == self.bot["account_id"]
                    or item.get("loginid") == self.bot["account_id"]
                ),
                None,
            )
            if account is None:
                raise ValueError("Conta demo configurada nao foi encontrada no token Deriv")
            selected_account = validate_selected_account(self.bot, account)
            if not await self._connection.connect(self.bot["account_id"]):
                raise ConnectionError("Nao foi possivel abrir o WebSocket autenticado da Deriv")

            strategy = self._build_strategy()
            risk_config = self.bot.get("risk_config") or {}
            risk = RiskManager(risk_config)
            circuit_breaker = CircuitBreaker(risk_config)
            if hasattr(self.repository, "get_risk_state"):
                persisted_risk = await self.repository.get_risk_state(
                    self.bot_id,
                    initial_stake=float(self.bot.get("initial_stake", 1.0)),
                )
                strategy.money_manager.restore_state(persisted_risk)
                circuit_breaker.restore_state(persisted_risk)
            proposal_manager = ProposalManager(self._connection)
            if isinstance(strategy, NexusSpeedStrategy):
                symbol = self.bot.get("symbol", "R_100")
                await proposal_manager.validate_contract_types(
                    symbol, {"CALL", "PUT"}
                )
                params = strategy.get_contract_params()
                await proposal_manager.validate_fixed_duration(
                    symbol,
                    {"CALL", "PUT"},
                    strategy.get_stake(),
                    params["duration"],
                    params["duration_unit"],
                )
            ownership = OrderOwnershipCoordinator(
                self._connection,
                self.repository,
                account_type=selected_account["account_type"],
            )
            await ownership.start()
            monitor = ContractMonitor(self._connection)
            self._market_data = MarketDataHandler(
                self._connection,
                bot_id=self.bot_id,
                publisher=self.publisher,
                bollinger_period=getattr(strategy, "period", 21),
                bollinger_std_dev=getattr(strategy, "std_dev", None),
                indicator_mode=(
                    "ema" if isinstance(strategy, NexusSpeedStrategy) else "donchian"
                ),
                ema_period=getattr(strategy, "ema_period", 5),
            )
            await self._market_data.start(
                self.bot.get("symbol", "R_100"),
                int(self.bot.get("timeframe_seconds", 60)),
            )
            await self._recover_owned_contracts(monitor, strategy, circuit_breaker)
            await self._recover_order_intents(ownership, monitor, strategy, circuit_breaker)
            await self._set_status("RUNNING")
            await self._trade_loop(strategy, risk, circuit_breaker, proposal_manager, ownership, monitor)

            if self._active_contracts:
                await self._set_status("STOPPING")
                await self._wait_for_active_contracts()
            await self._set_status("STOPPED")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._set_status("ERROR", str(exc))
            raise
        finally:
            if monitor:
                await monitor.close()
            if self._market_data:
                await self._market_data.close()
            if self._connection:
                await self._connection.disconnect()
            else:
                await auth.close()
            if self._owns_publisher:
                await self.publisher.close()
            try:
                await self.repository.close_session(self._session_id, status="closed")
            except Exception:
                logger.exception(f"Falha ao encerrar journal da sessao {self._session_id}")

    async def _wait_for_active_contracts(self, timeout_seconds=None):
        timeout = float(
            settings.SETTLEMENT_WAIT_TIMEOUT_SECONDS
            if timeout_seconds is None
            else timeout_seconds
        )
        deadline = asyncio.get_running_loop().time() + timeout
        while self._active_contracts:
            await self.repository.touch_bot_heartbeat(self.bot_id)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                unresolved = sorted(self._active_contracts)
                raise TimeoutError(
                    f"Settlement nao confirmado no prazo para contratos: {unresolved}"
                )
            await asyncio.sleep(min(0.25, remaining))

    async def _daily_totals(self):
        return await self.repository.get_bot_daily_stats(self.bot_id)

    async def _publish_nexus_transitions(self, strategy):
        if not isinstance(strategy, NexusSpeedStrategy):
            return
        for transition in strategy.drain_transition_events():
            await self._publish("strategy.transition", **transition)

    async def _publish_strategy_signal(self, signal):
        await self._publish(
            "strategy.signal",
            action=signal.action,
            reason=signal.reason,
            price=signal.price,
            signal_epoch=signal.timestamp,
            tick_sequence=signal.tick_sequence,
            candle_time=signal.candle_time,
        )

    async def _trade_loop(self, strategy, risk, circuit_breaker, proposal_manager, ownership, monitor):
        symbol = self.bot.get("symbol", "R_100")
        risk_config = self.bot.get("risk_config") or {}
        cooldown_seconds = int(risk_config.get("cooldown_minutes", 0)) * 60
        while not self._stop_requested.is_set():
            await self.repository.touch_bot_heartbeat(self.bot_id)
            if self._active_contracts:
                await asyncio.sleep(0.2)
                continue
            if cooldown_seconds > 0 and self._last_trade_at > 0:
                elapsed = time.time() - self._last_trade_at
                remaining = cooldown_seconds - elapsed
                if remaining > 0:
                    mins = int(remaining) // 60
                    secs = int(remaining) % 60
                    logger.info(f"Cooldown ativo: aguardando {mins}m{secs:02d}s antes da proxima analise.")
                    await asyncio.sleep(min(remaining, 5.0))
                    continue
                else:
                    self._last_trade_at = 0.0
                    logger.info("Cooldown encerrado. Retomando analise de sinais.")
            if hasattr(self.repository, "list_unresolved_order_intents"):
                unresolved = await self.repository.list_unresolved_order_intents(self.bot_id)
                if unresolved:
                    await self._recover_order_intents(
                        ownership, monitor, strategy, circuit_breaker
                    )
                    unresolved = await self.repository.list_unresolved_order_intents(self.bot_id)
                    if unresolved:
                        await self._publish(
                            "risk.blocked",
                            reason="ownership_quarantine",
                            order_intent_ids=[item["id"] for item in unresolved],
                        )
                        await asyncio.sleep(settings.CONTRACT_RECONCILE_INTERVAL_SECONDS)
                        continue
                foreign_unresolved = await self._foreign_account_unresolved_intents()
                if foreign_unresolved:
                    intent_ids = sorted(
                        str(item["id"]) for item in foreign_unresolved if item.get("id")
                    )
                    blocking_bot_ids = sorted({
                        str(item["bot_id"]) for item in foreign_unresolved if item.get("bot_id")
                    })
                    logger.warning(
                        "Conta %s bloqueada por order_intents pendentes de outros robos: %s",
                        self.bot.get("account_id"),
                        ", ".join(intent_ids) if intent_ids else "(desconhecido)",
                    )
                    await self._publish(
                        "risk.blocked",
                        reason="account_ownership_quarantine",
                        account_id=self.bot.get("account_id"),
                        blocking_bot_ids=blocking_bot_ids,
                        order_intent_ids=intent_ids,
                    )
                    await asyncio.sleep(settings.CONTRACT_RECONCILE_INTERVAL_SECONDS)
                    continue
            latest = self._market_data.get_latest_tick(symbol)
            if hasattr(self.repository, "record_bot_health"):
                await self.repository.record_bot_health(
                    self.bot_id,
                    deriv_connected=bool(
                        self._connection and self._connection.is_connected
                    ),
                    publisher_healthy=bool(
                        getattr(self.publisher, "is_healthy", True)
                    ),
                    market_epoch=int(latest.get("epoch", 0)) if latest else None,
                )
            if not latest or time.time() - int(latest.get("epoch", 0)) > settings.MARKET_STALE_AFTER_SECONDS:
                await asyncio.sleep(0.5)
                continue
            is_tripped, remaining = circuit_breaker.is_tripped()
            if is_tripped:
                await self._publish("risk.blocked", reason="circuit_breaker", remaining_seconds=remaining)
                await asyncio.sleep(1)
                continue
            signal = strategy.analyze(
                self._market_data.get_tick_history(symbol),
                candles=self._market_data.get_candle_history(symbol)
            )
            await self._publish_nexus_transitions(strategy)
            if not signal:
                await asyncio.sleep(0.2)
                continue
            pnl, daily_trades = await self._daily_totals()
            stake = strategy.get_stake()
            allowed, reason = risk.check_trade_allowed(pnl, daily_trades, stake)
            if not allowed:
                await self._publish("risk.blocked", reason=reason)
                await asyncio.sleep(1)
                continue
            if self._stop_requested.is_set():
                break
            await self._publish_strategy_signal(signal)
            params = strategy.get_contract_params()
            proposal = await proposal_manager.request_proposal(
                symbol,
                signal.action,
                stake,
                params["duration"],
                params["duration_unit"],
            )
            if not proposal or self._stop_requested.is_set():
                continue
            latest_after_proposal = self._market_data.get_latest_tick(symbol)
            block_reason = self._nexus_trade_block_reason(
                strategy, signal, proposal, latest_after_proposal
            )
            if block_reason:
                await self._publish(
                    "risk.blocked",
                    reason=block_reason,
                    signal_tick_sequence=signal.tick_sequence,
                    latest_tick_sequence=(
                        latest_after_proposal.get("sequence")
                        if latest_after_proposal
                        else None
                    ),
                    profit_ratio=ProposalManager.profit_ratio(proposal),
                    min_profit_ratio=getattr(strategy, "min_profit_ratio", None),
                )
                continue
            try:
                buy = await ownership.buy({
                    "bot_id": self.bot_id,
                    "account_id": self.bot["account_id"],
                    "session_id": self._session_id,
                    "proposal_id": proposal["id"],
                    "symbol": symbol,
                    "contract_type": signal.action,
                    "stake": stake,
                    "price": proposal["ask_price"],
                    "duration": params["duration"],
                    "duration_unit": params["duration_unit"],
                    "signal_epoch": signal.timestamp,
                })
            except ActiveOrderIntentError as exc:
                await self._publish("risk.blocked", reason="ownership_quarantine", error=str(exc))
                continue
            if buy:
                await self._register_contract(buy, signal.action, strategy, monitor, circuit_breaker)

    async def _register_contract(
        self,
        contract,
        contract_type,
        strategy,
        monitor,
        circuit_breaker,
        persist_open=True,
    ):
        contract_id = int(contract["contract_id"])
        self._active_contracts.add(contract_id)
        open_trade = {
            "bot_id": self.bot_id,
            "session_id": self._session_id,
            "strategy_name": strategy.name(),
            "symbol": self.bot.get("symbol"),
            "contract_type": contract_type,
            "contract_id": contract_id,
            "stake": float(contract.get("buy_price", self.bot.get("initial_stake", 1.0))),
            "payout": float(contract.get("payout", 0) or 0),
            "profit": 0.0,
            "result": "open",
            "status": "open",
            "lifecycle_state": "live",
            "is_sold": False,
            "is_expired": False,
            "date_settlement": None,
            "entry_spot": contract.get("entry_spot"),
            "purchase_time": contract.get("purchase_time"),
            "expiry_time": contract.get("date_expiry"),
        }
        if persist_open:
            await self.repository.upsert_trade(open_trade)
            await self._publish("trade.opened", trade=open_trade)

        async def on_update(poc):
            await self._publish("trade.updated", trade=self._trade_payload(poc, strategy, "open"))

        async def on_settled(poc):
            trade = self._trade_payload(poc, strategy, "closed")
            if hasattr(self.repository, "settle_trade_and_risk"):
                result = await self.repository.settle_trade_and_risk(
                    trade,
                    money_management=self.bot.get("money_management", "fixed"),
                    money_config=self.bot.get("money_config") or {},
                    risk_config=self.bot.get("risk_config") or {},
                    initial_stake=float(self.bot.get("initial_stake", 1.0)),
                    settled_epoch=float(
                        poc.get("date_settlement") or time.time()
                    ),
                )
                strategy.money_manager.restore_state(result["state"])
                circuit_breaker.restore_state(result["state"])
            else:
                await self.repository.upsert_trade(trade)
                strategy.on_trade_result(poc)
                circuit_breaker.record_result(float(poc.get("profit", 0) or 0) > 0)
            self._active_contracts.discard(contract_id)
            self._last_trade_at = time.time()
            await self._publish("trade.closed", trade=trade)

        await monitor.monitor_contract(contract_id, on_settled, on_update)

    def _trade_payload(self, poc, strategy, status):
        is_sold = poc.get("is_sold") == 1
        is_expired = poc.get("is_expired") == 1
        lifecycle_state = "closed" if status == "closed" or is_sold else (
            "awaiting_settlement" if is_expired else "live"
        )
        return {
            "bot_id": self.bot_id,
            "session_id": self._session_id,
            "strategy_name": strategy.name(),
            "symbol": poc.get("underlying", self.bot.get("symbol")),
            "contract_type": poc.get("contract_type"),
            "contract_id": int(poc["contract_id"]),
            "stake": float(poc.get("buy_price", 0) or 0),
            "payout": float(poc.get("payout", 0) or 0),
            "profit": float(poc.get("profit", 0) or 0),
            "result": poc.get("status", status),
            "status": status,
            "lifecycle_state": lifecycle_state,
            "is_sold": is_sold,
            "is_expired": is_expired,
            "date_settlement": poc.get("date_settlement"),
            "entry_spot": poc.get("entry_spot"),
            "exit_spot": poc.get("exit_spot", poc.get("current_spot")),
            "purchase_time": poc.get("purchase_time"),
            "expiry_time": poc.get("date_expiry"),
        }

    async def _recover_owned_contracts(self, monitor, strategy, circuit_breaker):
        owned = {
            int(item["contract_id"]): item
            for item in await self.repository.list_trades(self.bot_id, limit=1000)
            if item.get("status") == "open" and item.get("contract_id") is not None
        }
        if not owned:
            return
        portfolio = await CrashRecoveryHandler(self._connection).check_open_contracts()
        portfolio_by_id = {
            int(contract["contract_id"]): contract
            for contract in portfolio
            if contract.get("contract_id") is not None
        }
        for contract_id, stored_trade in owned.items():
            contract = portfolio_by_id.get(contract_id, stored_trade)
            await self._register_contract(
                contract,
                contract.get("contract_type"),
                strategy,
                monitor,
                circuit_breaker,
                persist_open=False,
            )

    async def _recover_order_intents(self, ownership, monitor, strategy, circuit_breaker):
        if not hasattr(self.repository, "list_unresolved_order_intents"):
            return
        if hasattr(self.repository, "list_owned_intents_without_trade"):
            for intent in await self.repository.list_owned_intents_without_trade(self.bot_id):
                contract = {
                    **(intent.get("metadata") or {}),
                    "contract_id": intent["contract_id"],
                    "contract_type": intent["contract_type"],
                    "buy_price": intent["price"],
                }
                if int(contract["contract_id"]) not in self._active_contracts:
                    await self._register_contract(
                        contract,
                        intent["contract_type"],
                        strategy,
                        monitor,
                        circuit_breaker,
                        persist_open=True,
                    )
        for intent, contract in await ownership.reconcile_pending(self.bot_id):
            contract_id = int(contract["contract_id"])
            if contract_id in self._active_contracts:
                continue
            await self._register_contract(
                contract,
                contract.get("contract_type") or intent["contract_type"],
                strategy,
                monitor,
                circuit_breaker,
                persist_open=True,
            )
