import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_dev.ps1"
DEVELOPMENT_GUIDE = ROOT / "docs" / "DEVELOPMENT.md"


class LocalDevProfileTests(unittest.TestCase):
    def test_launcher_forces_safe_local_runtime(self):
        script = LAUNCHER.read_text(encoding="utf-8")

        self.assertIn('$env:ALLOW_REAL_TRADING = "false"', script)
        self.assertIn('$env:DOMAIN = "localhost"', script)
        self.assertIn("nexus_trader.dev.db", script)
        self.assertIn("127.0.0.1", script)
        self.assertNotIn('$env:ALLOW_REAL_TRADING = "true"', script)

    def test_launcher_starts_api_before_bot_and_tracks_exact_processes(self):
        script = LAUNCHER.read_text(encoding="utf-8")

        self.assertIn("api.app:app", script)
        self.assertIn("/api/v1/health", script)
        self.assertIn("main.py", script)
        self.assertIn("-WindowStyle Hidden", script)
        self.assertIn("$apiProcess.Id", script)
        self.assertIn("$botProcess.Id", script)

    def test_guide_documents_demo_only_donchian_workflow(self):
        guide = DEVELOPMENT_GUIDE.read_text(encoding="utf-8")

        self.assertIn("scripts/start_dev.ps1", guide)
        self.assertIn("http://127.0.0.1:8990", guide)
        self.assertIn("Donchian+ZigZag", guide)
        self.assertIn("R_75", guide)
        self.assertIn("1 minuto", guide)
        self.assertIn("2 minutos", guide)
        self.assertIn("ALLOW_REAL_TRADING=false", guide)


if __name__ == "__main__":
    unittest.main()
