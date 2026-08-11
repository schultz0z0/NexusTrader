import subprocess
import socket
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start_dev.ps1"


class StartDevSafetyTests(unittest.TestCase):
    @staticmethod
    def _write_dummy_env(path):
        path.write_text(
            "\n".join(
                (
                    "DERIV_APP_ID=dummy-app",
                    "DERIV_API_TOKEN=dummy-secret-token",
                    "DERIV_ACCOUNT_ID=DOT-DUMMY",
                    "DERIV_ACCOUNT_TYPE=real",
                    "INTERNAL_API_TOKEN=dummy-internal",
                    "DASHBOARD_API_KEY=dummy-dashboard",
                    "ALLOW_REAL_TRADING=true",
                )
            ),
            encoding="utf-8",
        )

    def test_preflight_imports_external_env_then_forces_safe_local_overrides(self):
        """Catches trusting unsafe values or echoing an external secret/path."""
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            env_file = temporary_path / "validation.env"
            self._write_dummy_env(env_file)
            database_path = temporary_path / "isolated.db"
            logs_path = temporary_path / "logs"
            pid_path = temporary_path / "pids"

            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(SCRIPT),
                    "-EnvFile",
                    str(env_file),
                    "-DatabasePath",
                    str(database_path),
                    "-LogsDirectory",
                    str(logs_path),
                    "-PidDirectory",
                    str(pid_path),
                    "-RunId",
                    "phase1-validation",
                    "-PreflightOnly",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("SAFE_PREFLIGHT", completed.stdout)
        self.assertIn('"allow_real_trading":false', completed.stdout.lower())
        self.assertIn('"account_type":"demo"', completed.stdout.lower())
        combined = (completed.stdout + completed.stderr).lower()
        self.assertNotIn("dummy-secret-token", combined)
        self.assertNotIn(str(env_file).lower(), combined)
        self.assertNotIn(str(database_path).lower(), combined)

    def test_preflight_refuses_non_loopback_bind(self):
        """Catches accidentally exposing the development server on the network."""
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPT),
                "-BindAddress",
                "0.0.0.0",
                "-PreflightOnly",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("loopback", (completed.stdout + completed.stderr).lower())

    def test_stop_owned_closes_recorded_worker_without_inherited_pipes(self):
        """Catches orphaning the real worker behind the Windows venv redirector."""
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            env_file = temporary_path / "validation.env"
            self._write_dummy_env(env_file)
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            common = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
                "-EnvFile", str(env_file), "-DatabasePath", str(temporary_path / "isolated.db"),
                "-LogsDirectory", str(temporary_path / "logs"),
                "-PidDirectory", str(temporary_path / "pids"),
                "-RunId", "owned-worker", "-Port", str(port),
            ]
            started = subprocess.Popen(
                [*common, "-ApiOnly", "-Detached"],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertEqual(started.wait(timeout=20), 0)
            worker_pid_file = temporary_path / "pids" / "api.worker.pid"
            self.assertTrue(worker_pid_file.is_file())
            worker_pid = int(worker_pid_file.read_text().strip())
            self.assertGreater(worker_pid, 0)

            stop_error = temporary_path / "stop.stderr.log"
            with stop_error.open("w", encoding="utf-8") as error_stream:
                stopped = subprocess.run(
                    [*common, "-StopOwned"],
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=error_stream,
                    timeout=20,
                    check=False,
                )

            sanitized_error = stop_error.read_text(encoding="utf-8").replace(
                str(temporary_path), "<temp>",
            )
            self.assertEqual(stopped.returncode, 0, sanitized_error)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                with socket.socket() as client:
                    if client.connect_ex(("127.0.0.1", port)) != 0:
                        break
                time.sleep(0.05)
            with socket.socket() as client:
                self.assertNotEqual(client.connect_ex(("127.0.0.1", port)), 0)

if __name__ == "__main__":
    unittest.main()
