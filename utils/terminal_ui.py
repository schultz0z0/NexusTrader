import sys
from utils.logger import setup_logger

logger = setup_logger("TerminalUI")

class TerminalDashboard:
    """
    Exibe um painel de controle e estatisticas de trading no terminal.
    """
    def __init__(self, strategy_name: str, account_id: str, initial_balance: float = 0.0):
        self.strategy_name = strategy_name
        self.account_id = account_id
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.total_profit = 0.0

    def update_balance(self, balance: float):
        self.current_balance = balance

    def add_trade_result(self, result: str, profit: float, stake: float):
        self.total_trades += 1
        self.total_profit += profit
        if result == 'won' or profit > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.render()

    def render(self):
        win_rate = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0.0
        pnl_color = "+" if self.total_profit >= 0 else ""
        
        banner = f"""
====================================================================
 🤖 NEXUSTRADER - PAINEL DE CONTROLE (FASE 2)
--------------------------------------------------------------------
 Conta: {self.account_id} | Estrategia: {self.strategy_name}
 Total Operacoes: {self.total_trades} | Wins: {self.wins} | Losses: {self.losses} | WinRate: {win_rate:.1f}%
 PnL Acumulado: {pnl_color}${self.total_profit:.2f} USD
====================================================================
"""
        logger.info(banner)
