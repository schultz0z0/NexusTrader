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
            DERIV_APP_ID="test-app",
            DERIV_API_TOKEN="test-token",
        )

        self.assertFalse(configured.ALLOW_REAL_TRADING)
        self.assertEqual(configured.BUSINESS_TIMEZONE, "America/Sao_Paulo")


if __name__ == "__main__":
    unittest.main()
