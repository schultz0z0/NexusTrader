import unittest
from strategies.bollinger import BollingerBandsStrategy
from strategies.base import MoneyManager

class TestBollingerBandsStrategy(unittest.TestCase):
    def setUp(self):
        self.money_manager = MoneyManager(mode="martingale", initial_stake=1.0, martingale_multiplier=2.0)
        self.strategy = BollingerBandsStrategy(period=5, std_dev=1.0, money_manager=self.money_manager)

    def test_insufficient_ticks(self):
        ticks = [{'quote': 100.0, 'epoch': 1}]
        signal = self.strategy.analyze(ticks)
        self.assertIsNone(signal)

    def test_call_signal_on_lower_band_break(self):
        # 5 ticks estaveis em 100, e o 6º tick caindo forte para 90 (quebrando a banda inferior)
        ticks = [
            {'quote': 100.0, 'epoch': 1},
            {'quote': 100.0, 'epoch': 2},
            {'quote': 100.0, 'epoch': 3},
            {'quote': 100.0, 'epoch': 4},
            {'quote': 100.0, 'epoch': 5},
            {'quote': 90.0, 'epoch': 6},
        ]
        signal = self.strategy.analyze(ticks)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, "CALL")

    def test_put_signal_on_upper_band_break(self):
        # 5 ticks estaveis em 100, e o 6º tick subindo forte para 110 (quebrando a banda superior)
        ticks = [
            {'quote': 100.0, 'epoch': 1},
            {'quote': 100.0, 'epoch': 2},
            {'quote': 100.0, 'epoch': 3},
            {'quote': 100.0, 'epoch': 4},
            {'quote': 100.0, 'epoch': 5},
            {'quote': 110.0, 'epoch': 6},
        ]
        signal = self.strategy.analyze(ticks)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.action, "PUT")

    def test_martingale_multiplier_on_loss(self):
        self.assertEqual(self.money_manager.get_stake(), 1.0)
        
        # Simula Loss
        self.money_manager.on_trade_result(is_win=False, profit=-1.0)
        self.assertEqual(self.money_manager.get_stake(), 2.0)
        
        # Simula mais um Loss
        self.money_manager.on_trade_result(is_win=False, profit=-2.0)
        self.assertEqual(self.money_manager.get_stake(), 4.0)

        # Simula Win (deve resetar para 1.0)
        self.money_manager.on_trade_result(is_win=True, profit=3.8)
        self.assertEqual(self.money_manager.get_stake(), 1.0)

if __name__ == '__main__':
    unittest.main()
