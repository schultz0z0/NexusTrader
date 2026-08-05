import asyncio
import uuid
from core.auth import AuthManager
from core.connection import NexusConnection
from core.state_machine import TradingStateMachine, TradingState
from core.recovery import CrashRecoveryHandler
from data.market_data import MarketDataHandler
from trading.proposal import ProposalManager
from trading.executor import OrderExecutor
from trading.monitor import ContractMonitor
from database.repository import DatabaseRepository
from strategies.bollinger import BollingerBandsStrategy
from strategies.base import MoneyManager
from risk.risk_manager import RiskManager
from risk.circuit_breaker import CircuitBreaker
from notifications.manager import NotificationManager
from utils.terminal_ui import TerminalDashboard
from utils.logger import setup_logger

logger = setup_logger("NexusTraderMain")

class RestartSessionException(Exception):
    """Excecao levantada quando a configuracao do robo muda e requer reinicializacao."""
    pass

async def run_bot_session(db: DatabaseRepository, session_id: str):
    # Carrega configuracoes atuais do banco de dados
    bot_settings = await db.get_bot_settings()
    risk_config = await db.get_risk_config()
    
    account_id = bot_settings.get('account_id')
    symbol = bot_settings.get('symbol', 'R_100')
    
    if not account_id:
        logger.warning("Nenhuma conta configurada. O robo aguardara configuracao via interface web.")
        await asyncio.sleep(5)
        raise RestartSessionException()

    logger.info(f"=== Iniciando Sessao na Conta: {account_id} | Ativo: {symbol} ===")
    
    risk_mgr = RiskManager(risk_config)
    circuit_breaker = CircuitBreaker(risk_config)
    
    # Autenticacao (REST + OTP)
    auth = AuthManager()
    accounts = await auth.list_accounts()
    
    # Encontra a conta especificada para pegar o saldo inicial (opcional)
    account_info = next((acc for acc in accounts if (acc.get('account_id') == account_id or acc.get('loginid') == account_id)), {})
    initial_balance = float(account_info.get('balance', 0.0))
    
    # Conexao WebSocket via OTP para a conta especifica
    connection = NexusConnection(auth)
    # Passamos o account_id para o auth manager buscar a URL correta no REST
    ws_url = await auth.get_websocket_url(account_id)
    
    if not ws_url:
        logger.error(f"Nao foi possivel obter WebSocket URL para a conta {account_id}")
        await auth.close()
        await asyncio.sleep(5)
        raise RestartSessionException()
        
    connection.ws_url = ws_url
    if not await connection.connect():
        logger.error("Falha ao estabelecer conexao WebSocket. Reiniciando em breve...")
        await auth.close()
        await asyncio.sleep(5)
        raise RestartSessionException()

    try:
        # Inicializa Modulo de Notificacoes e Ouvinte de Comandos do Telegram
        notifier = NotificationManager()
        notifier.start_command_listener()
        notifier.notify_session_start(account_id, symbol, initial_balance)

        # Crash Recovery
        recovery = CrashRecoveryHandler(connection)
        open_contracts = await recovery.check_open_contracts()
        
        # Inicializa Estrategia e Gestao de Banca
        money_mgr = MoneyManager(
            mode="martingale",
            initial_stake=risk_config.get("initial_stake", 1.0),
            martingale_multiplier=2.0,
            max_martingale_levels=risk_config.get("max_consecutive_losses", 3)
        )
        
        strategy = BollingerBandsStrategy(period=20, std_dev=2.0, money_manager=money_mgr, duration=5)
        dashboard = TerminalDashboard(strategy_name=strategy.name(), account_id=account_id, initial_balance=initial_balance)
        
        # Atualiza estatisticas acumuladas do dia
        daily_stats = await db.get_daily_stats()
        current_daily_pnl = daily_stats.get("daily_pnl", 0.0)
        current_daily_trades = daily_stats.get("daily_trades", 0)
        dashboard.total_profit = current_daily_pnl
        dashboard.total_trades = current_daily_trades
        dashboard.render()
        
        # Modulos Core
        fsm = TradingStateMachine()
        market_data = MarketDataHandler(connection, buffer_size=500)
        proposal_mgr = ProposalManager(connection)
        executor = OrderExecutor(connection)
        monitor = ContractMonitor(connection)
        
        await market_data.subscribe_ticks(symbol)
        fsm.transition_to(TradingState.ANALYZING)
        
        async def on_contract_settled(poc_data):
            nonlocal current_daily_pnl, current_daily_trades
            contract_id = poc_data.get('contract_id')
            status = poc_data.get('status')
            profit = float(poc_data.get('profit', 0))
            buy_price = float(poc_data.get('buy_price', 1.0))
            is_win = (status == 'won') or (profit > 0)
            
            current_daily_pnl += profit
            current_daily_trades += 1
            
            trade_data = {
                "session_id": session_id,
                "strategy_name": strategy.name(),
                "symbol": symbol,
                "contract_type": poc_data.get('contract_type'),
                "contract_id": contract_id,
                "stake": buy_price,
                "payout": float(poc_data.get('payout', 0)),
                "profit": profit,
                "result": status
            }
            await db.save_trade(trade_data)
            
            strategy.on_trade_result(poc_data)
            circuit_breaker.record_result(is_win=is_win)
            dashboard.add_trade_result(result=status, profit=profit, stake=buy_price)
            
            # Notificacao em Tempo Real via Telegram/Discord
            notifier.notify_trade_result(trade_data, current_daily_pnl)
            
            fsm.transition_to(TradingState.PROCESSING_RESULT)
            fsm.transition_to(TradingState.IDLE)
            fsm.transition_to(TradingState.ANALYZING)

        if open_contracts:
            for c in open_contracts:
                fsm.transition_to(TradingState.CONTRACT_ACTIVE)
                await monitor.monitor_contract(c['contract_id'], on_contract_settled)

        while True:
            await asyncio.sleep(0.5)
            
            # Hot-Swap: Monitorar mudancas em bot_settings
            current_bot_settings = await db.get_bot_settings()
            if current_bot_settings.get('account_id') != account_id or current_bot_settings.get('symbol') != symbol:
                logger.info("🔧 Alteracao de configuracao (Conta ou Ativo) detectada! Reiniciando sessao...")
                raise RestartSessionException()

            if fsm.state != TradingState.ANALYZING:
                continue
            
            # Hot-Swap: Atualizar configuracoes de risco dinamicamente
            new_risk_config = await db.get_risk_config()
            risk_mgr.update_config(new_risk_config)
            circuit_breaker.update_config(new_risk_config)
            money_mgr.update_config(
                max_martingale_levels=new_risk_config.get("max_consecutive_losses", 3),
                initial_stake=new_risk_config.get("initial_stake", 1.0)
            )

            is_tripped, remaining_seconds = circuit_breaker.is_tripped()
            if is_tripped:
                logger.warning(f"⚡ CIRCUIT BREAKER ATIVO: Operacoes em pausa por mais {remaining_seconds}s...")
                notifier.notify_risk_alert(
                    "Circuit Breaker Ativado",
                    f"O limite de perdas consecutivas foi atingido. Operações pausadas por {remaining_seconds} segundos para proteção de banca.",
                    alert_type="CIRCUIT_BREAKER"
                )
                await asyncio.sleep(5)
                continue
                
            ticks = market_data.get_tick_history(symbol)
            signal = strategy.analyze(ticks)
            
            if signal:
                proposed_stake = strategy.get_stake()
                
                allowed, reason = risk_mgr.check_trade_allowed(
                    current_pnl=current_daily_pnl,
                    daily_trades=current_daily_trades,
                    proposed_stake=proposed_stake
                )
                
                if not allowed:
                    logger.warning(f"🚫 Entrada bloqueada pelo RiskManager: {reason}")
                    notifier.notify_risk_alert("Entrada Bloqueada", f"Sinal detectado, mas bloqueado pelo RiskManager: {reason}", alert_type="WARNING")
                    await asyncio.sleep(2)
                    continue

                logger.info(f"🎯 SINAL APROVADO PELO RISCO: {signal.action} | Stake: ${proposed_stake:.2f}")
                fsm.transition_to(TradingState.SIGNAL_GENERATED)
                fsm.transition_to(TradingState.REQUESTING_PROPOSAL)
                
                params = strategy.get_contract_params()
                proposal = await proposal_mgr.request_proposal(
                    symbol=symbol,
                    contract_type=signal.action,
                    stake=proposed_stake,
                    duration=params.get('duration', 5),
                    duration_unit=params.get('duration_unit', 't')
                )
                
                if proposal:
                    fsm.transition_to(TradingState.PROPOSAL_RECEIVED)
                    fsm.transition_to(TradingState.PURCHASING)
                    buy_result = await executor.buy(proposal['id'], proposal['ask_price'])
                    
                    if buy_result:
                        fsm.transition_to(TradingState.CONTRACT_ACTIVE)
                        await monitor.monitor_contract(buy_result['contract_id'], on_contract_settled)
                    else:
                        fsm.transition_to(TradingState.ERROR)
                        await asyncio.sleep(2)
                        fsm.reset()
                        fsm.transition_to(TradingState.ANALYZING)
                else:
                    fsm.transition_to(TradingState.ERROR)
                    await asyncio.sleep(2)
                    fsm.reset()
                    fsm.transition_to(TradingState.ANALYZING)
                    
    finally:
        await notifier.close()
        await auth.close()
        await connection.disconnect()

async def main():
    logger.info("==========================================================")
    logger.info("=== NexusTrader - Fase 4.5: Hot-Swap & UI Enrichment ===")
    logger.info("==========================================================")
    
    db = DatabaseRepository()
    await db.init_db()
    
    while True:
        try:
            session_id = str(uuid.uuid4())
            await db.create_session(session_id)
            await run_bot_session(db, session_id)
        except RestartSessionException:
            logger.info("🔁 Reiniciando loop principal do robo em 2 segundos...")
            await asyncio.sleep(2)
        except KeyboardInterrupt:
            logger.info("Encerrando NexusTrader...")
            break
        except Exception as e:
            logger.error(f"Erro fatal no loop principal: {str(e)}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
