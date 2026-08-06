import os
import unittest


class DeploymentContractTests(unittest.TestCase):
    def test_compose_explicitly_propagates_security_and_real_trading_flags(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "docker-compose.yml"), encoding="utf-8") as stream:
            compose = stream.read()

        self.assertGreaterEqual(compose.count("INTERNAL_API_TOKEN:"), 2)
        self.assertGreaterEqual(compose.count("DASHBOARD_API_KEY:"), 2)
        self.assertGreaterEqual(compose.count("ALLOW_REAL_TRADING:"), 2)
        self.assertIn("API_BASE_URL: http://nexus-api:8000", compose)
        self.assertIn("trade.solucoes-nexus.tech", compose)

    def test_operations_runbook_contains_safe_vps_update_sequence(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "docs", "OPERATIONS.md"), encoding="utf-8") as stream:
            runbook = stream.read()

        for command in (
            "docker compose stop nexus-bot",
            "docker compose config",
            "docker compose up -d --build",
            "http://127.0.0.1:8989/api/v1/health",
        ):
            self.assertIn(command, runbook)
        self.assertIn("trade.solucoes-nexus.tech", runbook)
        self.assertIn("ALLOW_REAL_TRADING=true", runbook)


if __name__ == "__main__":
    unittest.main()
