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
        self.assertGreaterEqual(compose.count("DERIV_ACCOUNT_TYPE:"), 2)
        self.assertIn("API_BASE_URL: http://nexus-api:8000", compose)
        self.assertIn("trade.solucoes-nexus.tech", compose)
        self.assertGreaterEqual(compose.count("REAL_MAX_STAKE_USD:"), 2)
        self.assertGreaterEqual(compose.count("read_only: true"), 2)
        self.assertGreaterEqual(compose.count("pids_limit:"), 2)
        self.assertGreaterEqual(compose.count("max-size:"), 2)
        self.assertIn("!PathPrefix(`/api/v1/internal`)", compose)
        self.assertIn("/api/v1/health/live", compose)

    def test_image_runs_as_non_root_with_dedicated_runtime_stage(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as stream:
            dockerfile = stream.read()

        self.assertIn("AS builder", dockerfile)
        self.assertIn("USER nexus", dockerfile)
        self.assertIn("COPY --from=builder", dockerfile)
        self.assertIn("libicu72", dockerfile)
        self.assertNotIn("gcc \\\n+    &&", dockerfile[dockerfile.rfind("FROM python"):])

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
