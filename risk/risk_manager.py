from typing import Tuple, Dict, Any
from utils.logger import setup_logger

logger = setup_logger("RiskManager")

class RiskManager:
    """
    Gerenciador Dinamico de Risco Financeiro.
    Valida limites de Stop Loss, Take Profit, Max Trades e Stake Maxima.
    """
    def __init__(self, config: Dict[str, Any] = None):
        self.update_config(config or {})

    def update_config(self, config: Dict[str, Any]):
        """Atualiza parametros dinamicamente vindo do Banco de Dados ou da API Web."""
        new_stop_loss = abs(float(config.get("stop_loss_daily", 50.0)))
        new_take_profit = abs(float(config.get("take_profit_daily", 100.0)))
        new_max_trades = int(config.get("max_daily_trades", 50))
        new_max_stake = float(config.get("max_single_stake", 20.0))

        if not hasattr(self, "stop_loss_daily") or (
            self.stop_loss_daily != new_stop_loss or 
            self.take_profit_daily != new_take_profit or
            self.max_daily_trades != new_max_trades or
            self.max_single_stake != new_max_stake
        ):
            self.stop_loss_daily = new_stop_loss
            self.take_profit_daily = new_take_profit
            self.max_daily_trades = new_max_trades
            self.max_single_stake = new_max_stake
            
            logger.info(
                f"Configuracao de Risco Carregada: Stop Loss: -${self.stop_loss_daily:.2f} | "
                f"Take Profit: +${self.take_profit_daily:.2f} | Max Trades: {self.max_daily_trades} | "
                f"Stake Max: ${self.max_single_stake:.2f}"
            )

    def check_trade_allowed(self, current_pnl: float, daily_trades: int, proposed_stake: float) -> Tuple[bool, str]:
        # 1. Stop Loss Diario
        if current_pnl <= -self.stop_loss_daily:
            msg = f"STOP LOSS DIARIO ATINGIDO: PnL Atual (${current_pnl:.2f}) <= Limite (-${self.stop_loss_daily:.2f})"
            logger.error(f"[STOP LOSS] {msg}")
            return False, msg

        # 2. Take Profit Diario
        if current_pnl >= self.take_profit_daily:
            msg = f"TAKE PROFIT DIARIO ALCANCADO! PnL Atual (${current_pnl:.2f}) >= Meta (+${self.take_profit_daily:.2f})"
            logger.info(f"[TAKE PROFIT] {msg}")
            return False, msg

        # 3. Limite Diario de Trades
        if daily_trades >= self.max_daily_trades:
            msg = f"LIMITE DIARIO DE TRADES ALCANCADO: {daily_trades}/{self.max_daily_trades}"
            logger.warning(f"[MAX TRADES] {msg}")
            return False, msg

        # 4. Stake Maxima por Operacao
        if proposed_stake > self.max_single_stake:
            msg = f"STAKE SOLICITADA (${proposed_stake:.2f}) EXCEDE O LIMITE SEGURO (${self.max_single_stake:.2f})"
            logger.warning(f"[MAX STAKE] {msg}")
            return False, msg

        return True, "OK"
