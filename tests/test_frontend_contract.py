import os
import unittest
from html.parser import HTMLParser


class LandmarkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.elements_by_id = {}
        self.scripts = []
        self.styles = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
            self.elements_by_id[attributes["id"]] = attributes
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
        touch_tolerance = parser.elements_by_id["nexus-touch-tolerance"]
        self.assertIn("readonly", touch_tolerance)
        self.assertEqual(touch_tolerance["value"], "Cruzamento exato da EMA")
        touch_window = parser.elements_by_id["nexus-touch-window"]
        self.assertEqual(touch_window["value"], "Toque entre 01s e 30s")
        m5_filter = parser.elements_by_id["nexus-m5-filter"]
        self.assertEqual(m5_filter["value"], "Bloqueia 1ª e 5ª velas")

    def test_frontend_modules_exist(self):
        root = os.path.dirname(os.path.dirname(__file__))
        for filename in (
            "api.js", "store.js", "chart.js", "app.js",
            "nexus_trade_api.js", "nexus_trade_store.js", "nexus_trade_view.js",
            "nexus_trade_metrics.js", "nexus_trade_diff.js",
        ):
            self.assertTrue(os.path.isfile(os.path.join(root, "static", "js", filename)))

    def test_nexus_trade_has_a_fixed_operational_view_and_no_strategy_option(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "static", "index.html"), encoding="utf-8") as stream:
            html_source = stream.read()

        parser = LandmarkParser()
        parser.feed(html_source)
        self.assertTrue({
            "standard-workspace", "nexus-trade-view", "nexus-champion-status",
            "nexus-champion-version", "nexus-trial-version", "nexus-campaign-progress",
            "nexus-champion-toggle", "nexus-emergency-stop", "nexus-open-evolution",
        }.issubset(parser.ids))
        self.assertIn('data-nexus-action="champion-toggle"', html_source)
        self.assertIn('data-nexus-fixed-symbol="R_100"', html_source)
        self.assertIn('data-nexus-fixed-timeframe="60"', html_source)
        self.assertIn('data-nexus-fixed-duration="58"', html_source)
        strategy_source = html_source[
            html_source.index('id="strategy-select"'):html_source.index('</select>', html_source.index('id="strategy-select"'))
        ]
        self.assertNotIn('value="nexus_trade"', strategy_source)
        trial_source = html_source[
            html_source.index('id="nexus-trial-card"'):html_source.index('</article>', html_source.index('id="nexus-trial-card"'))
        ]
        self.assertNotIn('button', trial_source)

    def test_nexus_trade_exposes_weekly_reports_and_accumulated_evolution_panels(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "static", "index.html"), encoding="utf-8") as stream:
            html_source = stream.read()
        with open(os.path.join(root, "static", "js", "nexus_trade_view.js"), encoding="utf-8") as stream:
            view_source = stream.read()

        parser = LandmarkParser()
        parser.feed(html_source)
        self.assertTrue({
            "nexus-reports-panel", "nexus-report-week", "nexus-report-days",
            "nexus-report-metrics", "nexus-evolution-panel",
            "nexus-evolution-progress", "nexus-evolution-metrics",
            "nexus-evolution-gates", "nexus-recommendation",
        }.issubset(parser.ids))
        self.assertIn('data-nexus-tab="reports"', html_source)
        self.assertIn('data-nexus-tab="evolution"', html_source)
        self.assertIn('from "./nexus_trade_metrics.js"', view_source)
        with open(os.path.join(root, "static", "styles.css"), encoding="utf-8") as stream:
            styles = stream.read()
        self.assertIn(".nexus-tabs button{flex:1;min-width:105px;min-height:44px}", styles)

    def test_governance_dialog_keeps_human_credential_transient_and_explains_diff(self):
        root = os.path.dirname(os.path.dirname(__file__))
        with open(os.path.join(root, "static", "index.html"), encoding="utf-8") as stream:
            html_source = stream.read()
        with open(os.path.join(root, "static", "js", "nexus_trade_view.js"), encoding="utf-8") as stream:
            view_source = stream.read()

        parser = LandmarkParser()
        parser.feed(html_source)
        self.assertTrue({
            "nexus-evolution-diff", "nexus-approve", "nexus-reanalyze",
            "nexus-rollback", "nexus-governance-dialog", "nexus-governance-form",
            "nexus-governance-justification", "nexus-human-key",
            "nexus-reinforced-confirmation", "nexus-rollback-target",
        }.issubset(parser.ids))
        human_key = parser.elements_by_id["nexus-human-key"]
        self.assertEqual(human_key.get("type"), "password")
        self.assertEqual(human_key.get("autocomplete"), "off")
        self.assertNotIn("localStorage", view_source)
        self.assertIn('humanKeyNode.value = ""', view_source)
        with open(os.path.join(root, "static", "styles.css"), encoding="utf-8") as stream:
            styles = stream.read()
        self.assertIn(".nexus-reinforced-confirmation[hidden]{display:none!important}", styles)

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
