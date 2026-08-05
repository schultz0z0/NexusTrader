import unittest
import time
from risk.risk_manager import RiskManager
from risk.circuit_breaker import CircuitBreaker

class TestRiskManagement(unittest.TestCase):
    def setUp(self):
        self.config = {
            "stop_loss_daily": 50.0,
            "take_profit_daily": 100.0,
            "max_daily_trades": 10,
            "max_single_stake": 20.0,
            "max_consecutive_losses": 3,
            "cooldown_minutes": 15
        }
        self.risk_mgr = RiskManager(self.config)
        self.circuit_breaker = CircuitBreaker(self.config)

    def test_trade_allowed_normal_conditions(self):
        allowed, reason = self.risk_mgr.check_trade_allowed(current_pnl=0.0, daily_trades=0, proposed_stake=5.0)
        self.assertTrue(allowed)
        self.assertEqual(reason, "OK")

    def test_stop_loss_triggered(self):
        allowed, reason = self.risk_mgr.check_trade_allowed(current_pnl=-55.0, daily_trades=2, proposed_stake=5.0)
        self.assertFalse(allowed)
        self.assertIn("STOP LOSS DIARIO ATINGIDO", reason)

    def test_take_profit_triggered(self):
        allowed, reason = self.risk_mgr.check_trade_allowed(current_pnl=105.0, daily_trades=4, proposed_stake=5.0)
        self.assertFalse(allowed)
        self.assertIn("TAKE PROFIT DIARIO ALCANCADO", reason)

    def test_max_daily_trades_triggered(self):
        allowed, reason = self.risk_mgr.check_trade_allowed(current_pnl=10.0, daily_trades=10, proposed_stake=5.0)
        self.assertFalse(allowed)
        self.assertIn("LIMITE DIARIO DE TRADES ALCANCADO", reason)

    def test_max_stake_triggered(self):
        allowed, reason = self.risk_mgr.check_trade_allowed(current_pnl=0.0, daily_trades=1, proposed_stake=25.0)
        self.assertFalse(allowed)
        self.assertIn("STAKE SOLICITADA", reason)

    def test_circuit_breaker_tripped_on_3_consecutive_losses(self):
        tripped, _ = self.circuit_breaker.is_tripped()
        self.assertFalse(tripped)

        self.circuit_breaker.record_result(is_win=False)
        self.circuit_breaker.record_result(is_win=False)
        tripped, _ = self.circuit_breaker.is_tripped()
        self.assertFalse(tripped)

        # 3º loss -> deve pausar
        self.circuit_breaker.record_result(is_win=False)
        tripped, remaining = self.circuit_breaker.is_tripped()
        self.assertTrue(tripped)
        self.assertGreater(remaining, 0)

        # Win reseta perdas
        self.circuit_breaker.record_result(is_win=True)
        tripped, _ = self.circuit_breaker.is_tripped()
        self.assertFalse(tripped)

if __name__ == '__main__':
    unittest.main()
