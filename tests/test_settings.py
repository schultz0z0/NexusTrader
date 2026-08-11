import unittest

from config.settings import Settings


class SettingsContractTests(unittest.TestCase):
    @staticmethod
    def _production_settings(**overrides):
        values = {
            "DERIV_APP_ID": "test-app",
            "DERIV_API_TOKEN": "deriv-secret",
            "INTERNAL_API_TOKEN": "internal-secret",
            "DASHBOARD_API_KEY": "dashboard-secret",
            "NEXUS_HUMAN_ACTION_KEY": "human-secret",
            "NEXUS_HUMAN_ACTOR": "human:operator",
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    def test_human_action_key_cannot_collide_with_any_existing_authority(self):
        for field in (
            "DASHBOARD_API_KEY",
            "INTERNAL_API_TOKEN",
            "DERIV_API_TOKEN",
        ):
            for dev_mode in (False, True):
                with self.subTest(field=field, dev_mode=dev_mode):
                    secret = f"collision-{field.lower()}"
                    with self.assertRaises(ValueError) as caught:
                        self._production_settings(
                            DEV_MODE=dev_mode,
                            NEXUS_HUMAN_ACTION_KEY=secret,
                            **{field: secret},
                        )
                    self.assertNotIn(secret, str(caught.exception))

    def test_distinct_production_authorities_are_valid(self):
        configured = self._production_settings()

        self.assertEqual(configured.NEXUS_HUMAN_ACTION_KEY, "human-secret")
        self.assertEqual(configured.NEXUS_HUMAN_ACTOR, "human:operator")

    def test_dev_mode_allows_unconfigured_human_authority(self):
        configured = Settings(
            _env_file=None,
            DERIV_APP_ID="test-app",
            DERIV_API_TOKEN="test-token",
            DEV_MODE=True,
            NEXUS_HUMAN_ACTION_KEY="",
            NEXUS_HUMAN_ACTOR="",
        )

        self.assertEqual(configured.NEXUS_HUMAN_ACTION_KEY, "")
        self.assertEqual(configured.NEXUS_HUMAN_ACTOR, "")

    def test_production_requires_both_human_authority_fields(self):
        for field in ("NEXUS_HUMAN_ACTION_KEY", "NEXUS_HUMAN_ACTOR"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self._production_settings(**{field: ""})

    def test_internal_token_must_not_be_empty_in_production(self):
        with self.assertRaises(ValueError):
            Settings(
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                DOMAIN="trade.example.com",
                INTERNAL_API_TOKEN="",
                DASHBOARD_API_KEY="dashboard-secret",
                NEXUS_HUMAN_ACTION_KEY="human-secret",
                NEXUS_HUMAN_ACTOR="human:operator",
            )

    def test_dashboard_key_must_not_be_empty_in_production(self):
        with self.assertRaises(ValueError):
            Settings(
                DERIV_APP_ID="test-app",
                DERIV_API_TOKEN="test-token",
                DOMAIN="trade.example.com",
                INTERNAL_API_TOKEN="internal-secret",
                DASHBOARD_API_KEY="",
                NEXUS_HUMAN_ACTION_KEY="human-secret",
                NEXUS_HUMAN_ACTOR="human:operator",
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
