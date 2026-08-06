import unittest

from config.settings import settings
from core.accounts import normalize_account, validate_selected_account
from trading.safety import RealTradingDisabled, ensure_account_allowed


class AccountNormalizationTests(unittest.TestCase):
    def test_current_deriv_account_shape_is_normalized(self):
        real = normalize_account({
            "account_id": "ROT100", "account_type": "real", "balance": "12.50",
            "currency": "USD", "status": "active", "group": "row",
        })
        demo = normalize_account({
            "account_id": "DOT200", "account_type": "demo", "balance": "1000.00",
            "currency": "USD", "status": "active", "group": "demo",
        })

        self.assertEqual(real, {
            "account_id": "ROT100", "account_type": "real", "balance": 12.5,
            "currency": "USD", "status": "active", "group": "row",
        })
        self.assertEqual(demo["account_type"], "demo")
        self.assertEqual(demo["balance"], 1000.0)

    def test_persisted_type_must_match_selected_deriv_account(self):
        with self.assertRaisesRegex(ValueError, "tipo da conta"):
            validate_selected_account(
                {"account_id": "ROT100", "account_type": "demo"},
                {"account_id": "ROT100", "account_type": "real", "balance": "0"},
            )


class RealTradingFeatureFlagTests(unittest.TestCase):
    def setUp(self):
        self.previous = settings.ALLOW_REAL_TRADING

    def tearDown(self):
        settings.ALLOW_REAL_TRADING = self.previous

    def test_real_account_remains_blocked_when_feature_flag_is_false(self):
        settings.ALLOW_REAL_TRADING = False
        with self.assertRaises(RealTradingDisabled):
            ensure_account_allowed("real")

    def test_real_account_is_allowed_when_feature_flag_is_true(self):
        settings.ALLOW_REAL_TRADING = True
        ensure_account_allowed("real")


if __name__ == "__main__":
    unittest.main()
