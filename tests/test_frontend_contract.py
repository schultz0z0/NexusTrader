import os
import unittest
from html.parser import HTMLParser


class LandmarkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = []
        self.styles = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if tag == "script" and attributes.get("src"):
            self.scripts.append(attributes["src"])
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.styles.append(attributes.get("href"))


class FrontendContractTests(unittest.TestCase):
    def test_trading_terminal_has_required_live_regions_and_assets(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "static", "index.html"), encoding="utf-8") as stream:
            parser = LandmarkParser()
            parser.feed(stream.read())

        self.assertTrue({
            "bot-list", "chart", "chart-state", "active-trade",
            "trade-history", "bot-config", "connection-status",
            "environment-badge", "account-select", "account-type",
            "account-summary", "real-account-dialog",
            "confirm-real-start", "cancel-real-start",
            "real-confirm-phrase", "strategy-fixed-profile",
        }.issubset(parser.ids))
        self.assertIn("/static/styles.css", parser.styles)
        self.assertIn("/static/js/app.js", parser.scripts)
        self.assertTrue(any("lightweight-charts" in item for item in parser.scripts))

    def test_frontend_modules_exist(self):
        root = os.path.dirname(os.path.dirname(__file__))
        for filename in ("api.js", "store.js", "chart.js", "app.js"):
            self.assertTrue(os.path.isfile(os.path.join(root, "static", "js", filename)))

    def test_account_catalog_and_dynamic_account_type_are_wired(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "static", "index.html"), encoding="utf-8") as stream:
            html_source = stream.read()
        with open(os.path.join(root, "static", "js", "api.js"), encoding="utf-8") as stream:
            api_source = stream.read()
        with open(os.path.join(root, "static", "js", "app.js"), encoding="utf-8") as stream:
            app_source = stream.read()
        with open(os.path.join(root, "static", "js", "bot_config.js"), encoding="utf-8") as stream:
            bot_config_source = stream.read()

        self.assertIn('/api/v1/accounts', api_source)
        self.assertIn('/api/v1/ws-tickets/', api_source)
        websocket_source = api_source[api_source.index('export function websocketUrl'):]
        self.assertNotIn('getApiKey()', websocket_source)
        self.assertNotIn('?key=', websocket_source)
        self.assertNotIn('account_type: "demo"', app_source)
        self.assertIn('account_type: account.account_type', app_source)
        self.assertLess(html_source.index('id="account-select"'), html_source.index('id="bot-config"'))
        config_source = html_source[html_source.index('id="bot-config"'):]
        self.assertNotIn('name="account_id"', config_source)
        self.assertIn('/real-confirmation', api_source)
        self.assertIn('REAL ${account.account_id}', app_source)
        self.assertIn('period: 21', bot_config_source)
        self.assertIn('deviation: 1', bot_config_source)
        self.assertIn('depth: 15', bot_config_source)
        self.assertIn('backstep: 3', bot_config_source)


if __name__ == "__main__":
    unittest.main()
