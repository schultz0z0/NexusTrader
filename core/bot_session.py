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
from trading.executor import OrderExecutor
from trading.monitor import ContractMonitor
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

    async def request_stop(self):
        """Stop opening positions immediately; existing contracts may settle safely."""
        self._stop_requested.set()
        await self._set_status("STOPPING")

    async def _publish(self, event_type, **payload):
        await self.publisher.publish(runtime_event(event_type, self.bot_id, **payload))

    async def _set_status(self, status, error=None):
        await self.repository.set_runtime_state(self.bot_id, status, error)
        await self._publish("runtime.status", status=status, error=error)

    def _build_strategy(self):
        strategy_id = self.bot.get("strategy_id", "donchian")
        if strategy_id not in ("donchian",):
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

    async def run(self):
        ensure_account_allowed(self.bot)
        if not self.bot.get("account_id"):
            raise ValueError("Configure uma conta Deriv antes de iniciar o robo")

        await self.publisher.start()
        await self.repository.create_session(self._session_id)
        await self._set_status("STARTING")
        auth = AuthManager()
        self._connection = NexusConnection(auth)

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
            proposal_manager = ProposalManager(self._connection)
            executor = OrderExecutor(self._connection, account_type=selected_account["account_type"])
            monitor = ContractMonitor(self._connection)
            self._market_data = MarketDataHandler(
                self._connection,
                bot_id=self.bot_id,
                publisher=self.publisher,
                bollinger_period=getattr(strategy, "period", 21),
                bollinger_std_dev=getattr(strategy, "std_dev", None),
            )
            await self._market_data.start(
                self.bot.get("symbol", "R_100"),
                int(self.bot.get("timeframe_seconds", 60)),
            )
            await self._recover_owned_contracts(monitor, strategy, circuit_breaker)
            await self._set_status("RUNNING")
            await self._trade_loop(strategy, risk, circuit_breaker, proposal_manager, executor, monitor)

            if self._active_contracts:
                await self._set_status("STOPPING")
                while self._active_contracts:
                    await self.repository.touch_bot_heartbeat(self.bot_id)
                    await asyncio.sleep(0.25)
            await self._set_status("STOPPED")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._set_status("ERROR", str(exc))
            raise
        finally:
            if self._market_data:
                await self._market_data.close()
            if self._connection:
                await self._connection.disconnect()
            else:
                await auth.close()
            if self._owns_publisher:
                await self.publisher.close()

    async def _daily_totals(self):
        trades = await self.repository.list_trades(self.bot_id, limit=1000)
        closed = [item for item in trades if item.get("status") == "closed"]
        return sum(float(item.get("profit") or 0) for item in closed), len(closed)

    async def _trade_loop(self, strategy, risk, circuit_breaker, proposal_manager, executor, monitor):
        symbol = self.bot.get("symbol", "R_100")
        while not self._stop_requested.is_set():
            await self.repository.touch_bot_heartbeat(self.bot_id)
            if self._active_contracts:
                await asyncio.sleep(0.2)
                continue
            latest = self._market_data.get_latest_tick(symbol)
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
            await self._publish(
                "strategy.signal",
                action=signal.action,
                reason=signal.reason,
                price=signal.price,
                signal_epoch=signal.timestamp,
            )
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
            buy = await executor.buy(proposal["id"], proposal["ask_price"])
            if buy:
                await self._register_contract(buy, signal.action, strategy, monitor, circuit_breaker)

    async def _register_contract(self, contract, contract_type, strategy, monitor, circuit_breaker):
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
            "entry_spot": contract.get("entry_spot"),
            "purchase_time": contract.get("purchase_time"),
            "expiry_time": contract.get("date_expiry"),
        }
        await self.repository.upsert_trade(open_trade)
        await self._publish("trade.opened", trade=open_trade)

        async def on_update(poc):
            await self._publish("trade.updated", trade=self._trade_payload(poc, strategy, "open"))

        async def on_settled(poc):
            trade = self._trade_payload(poc, strategy, "closed")
            await self.repository.upsert_trade(trade)
            strategy.on_trade_result(poc)
            circuit_breaker.record_result(float(poc.get("profit", 0) or 0) > 0)
            self._active_contracts.discard(contract_id)
            await self._publish("trade.closed", trade=trade)

        await monitor.monitor_contract(contract_id, on_settled, on_update)

    def _trade_payload(self, poc, strategy, status):
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
            "entry_spot": poc.get("entry_spot"),
            "exit_spot": poc.get("exit_spot", poc.get("current_spot")),
            "purchase_time": poc.get("purchase_time"),
            "expiry_time": poc.get("date_expiry"),
        }

    async def _recover_owned_contracts(self, monitor, strategy, circuit_breaker):
        owned = {
            int(item["contract_id"])
            for item in await self.repository.list_trades(self.bot_id, limit=1000)
            if item.get("status") == "open" and item.get("contract_id") is not None
        }
        if not owned:
            return
        portfolio = await CrashRecoveryHandler(self._connection).check_open_contracts()
        for contract in portfolio:
            if int(contract.get("contract_id", -1)) in owned:
                await self._register_contract(
                    contract,
                    contract.get("contract_type"),
                    strategy,
                    monitor,
                    circuit_breaker,
                )
