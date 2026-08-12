import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rebuild_local_docker.ps1"


class RebuildLocalDockerSafetyTests(unittest.TestCase):
    def test_preflight_forces_demo_and_never_echoes_credentials_or_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            env_file = Path(temporary) / "unsafe.env"
            env_file.write_text(
                "\n".join((
                    "DERIV_APP_ID=dummy-app",
                    "DERIV_API_TOKEN=sentinel-deriv-secret",
                    "DERIV_ACCOUNT_ID=synthetic-account-id",
                    "DERIV_ACCOUNT_TYPE=real",
                    "INTERNAL_API_TOKEN=sentinel-internal-secret",
                    "DASHBOARD_API_KEY=sentinel-dashboard-secret",
                    "ALLOW_REAL_TRADING=true",
                    "REAL_MAX_STAKE_USD=999",
                )),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(SCRIPT), "-EnvFile", str(env_file),
                    "-PreflightOnly",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('"outcome":"SAFE_DOCKER_PREFLIGHT"', completed.stdout)
        self.assertIn('"account_type":"demo"', completed.stdout)
        self.assertIn('"allow_real_trading":false', completed.stdout)
        self.assertIn('"real_max_stake_usd":0', completed.stdout)
        combined = (completed.stdout + completed.stderr).lower()
        for forbidden in (
            "sentinel-deriv-secret", "sentinel-internal-secret",
            "sentinel-dashboard-secret", "synthetic-account-id", str(env_file).lower(),
        ):
            self.assertNotIn(forbidden, combined)

    def test_script_never_removes_the_persistent_volume(self):
        source = SCRIPT.read_text(encoding="utf-8").lower()
        self.assertNotIn("down -v", source)
        self.assertNotIn("volume rm", source)
        self.assertNotIn("docker volume", source)


if __name__ == "__main__":
    unittest.main()
