import unittest

from config.settings import Settings, settings


class NexusTradeSettingsTests(unittest.TestCase):
    def test_nexus_defaults_are_safe(self):
        self.assertEqual(settings.NEXUS_DEMO_STAKE, 0.35)
        self.assertEqual(settings.NEXUS_DAILY_CLOSE_HOUR, 10)
        self.assertEqual(settings.NEXUS_ENTRY_MAX_DELAY_SECONDS, 2)
        self.assertEqual(settings.BUSINESS_TIMEZONE, "America/Sao_Paulo")

    def test_nexus_demo_stake_is_fixed(self):
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                INTERNAL_API_TOKEN="internal-token",
                DASHBOARD_API_KEY="dashboard-key",
                NEXUS_HUMAN_ACTION_KEY="human-key",
                NEXUS_HUMAN_ACTOR="human:operator",
                NEXUS_DEMO_STAKE=0.36,
            )

    def test_nexus_daily_close_hour_is_within_a_day(self):
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                INTERNAL_API_TOKEN="internal-token",
                DASHBOARD_API_KEY="dashboard-key",
                NEXUS_HUMAN_ACTION_KEY="human-key",
                NEXUS_HUMAN_ACTOR="human:operator",
                NEXUS_DAILY_CLOSE_HOUR=24,
            )

    def test_nexus_entry_delay_is_fixed(self):
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                INTERNAL_API_TOKEN="internal-token",
                DASHBOARD_API_KEY="dashboard-key",
                NEXUS_HUMAN_ACTION_KEY="human-key",
                NEXUS_HUMAN_ACTOR="human:operator",
                NEXUS_ENTRY_MAX_DELAY_SECONDS=3,
            )


if __name__ == "__main__":
    unittest.main()
