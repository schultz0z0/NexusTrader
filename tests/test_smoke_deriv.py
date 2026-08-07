import unittest

from scripts import smoke_deriv


class DerivSmokeContractTests(unittest.TestCase):
    def test_dot_login_is_recognized_as_demo(self):
        self.assertTrue(smoke_deriv.is_demo({"account_id": "DOT93156117"}))
        self.assertFalse(smoke_deriv.is_demo({"account_id": "ROT91855276"}))

    def test_smoke_cli_has_no_trade_mode(self):
        parser = smoke_deriv.build_parser()

        _, unknown = parser.parse_known_args(["--trade"])

        self.assertEqual(unknown, ["--trade"])


if __name__ == "__main__":
    unittest.main()
