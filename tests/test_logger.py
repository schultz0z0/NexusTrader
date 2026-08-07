import subprocess
import sys
import unittest


class LoggerConsoleTests(unittest.TestCase):
    def test_import_does_not_replace_stdout_stream(self):
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; original=sys.stdout; import utils.logger; "
                "print('same' if sys.stdout is original else 'wrapped')",
            ],
            capture_output=True,
            check=True,
            text=True,
        )

        self.assertEqual(probe.stdout.strip(), "same")


if __name__ == "__main__":
    unittest.main()
