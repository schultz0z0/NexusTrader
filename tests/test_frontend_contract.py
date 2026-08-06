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
        }.issubset(parser.ids))
        self.assertIn("/static/styles.css", parser.styles)
        self.assertIn("/static/js/app.js", parser.scripts)
        self.assertTrue(any("lightweight-charts" in item for item in parser.scripts))

    def test_frontend_modules_exist(self):
        root = os.path.dirname(os.path.dirname(__file__))
        for filename in ("api.js", "store.js", "chart.js", "app.js"):
            self.assertTrue(os.path.isfile(os.path.join(root, "static", "js", filename)))


if __name__ == "__main__":
    unittest.main()
