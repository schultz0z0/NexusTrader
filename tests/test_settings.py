import unittest

from config.settings import Settings


class SettingsContractTests(unittest.TestCase):
    def test_internal_token_must_not_be_empty_in_production(self):
        with self.assertRaises(ValueError):
            Settings(
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                DOMAIN="trade.example.com",
                INTERNAL_API_TOKEN="",
                DASHBOARD_API_KEY="dashboard-secret",
            )

    def test_dashboard_key_must_not_be_empty_in_production(self):
        with self.assertRaises(ValueError):
            Settings(
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                DOMAIN="trade.example.com",
                INTERNAL_API_TOKEN="internal-secret",
                DASHBOARD_API_KEY="",
            )

    def test_demo_execution_is_default(self):
        configured = Settings(
            _env_file=None,
            DERIV_APP_ID="test-app",
            DERIV_API_TOKEN="test-token",
            DEV_MODE=True,
        )

        self.assertFalse(configured.ALLOW_REAL_TRADING)
        self.assertEqual(configured.BUSINESS_TIMEZONE, "America/Sao_Paulo")

    def test_direct_runtime_fails_closed_without_explicit_dev_mode(self):
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                DOMAIN="",
                INTERNAL_API_TOKEN="",
                DASHBOARD_API_KEY="",
            )

    def test_contract_reconcile_interval_must_be_positive(self):
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                DEV_MODE=True,
                CONTRACT_RECONCILE_INTERVAL_SECONDS=0,
            )

    def test_contract_expiry_grace_cannot_be_negative(self):
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                DEV_MODE=True,
                CONTRACT_EXPIRY_GRACE_SECONDS=-1,
            )

    def test_contract_reconciliation_defaults_are_safe(self):
        configured = Settings(
            _env_file=None,
            DERIV_APP_ID="test-app",
            DERIV_API_TOKEN="test-token",
            DEV_MODE=True,
        )

        self.assertEqual(configured.CONTRACT_RECONCILE_INTERVAL_SECONDS, 5)
        self.assertEqual(configured.CONTRACT_EXPIRY_GRACE_SECONDS, 1)


if __name__ == "__main__":
    unittest.main()
