import assert from "node:assert/strict";
import test from "node:test";

import { mountNexusTradeView, resolveDashboardView } from "../../static/js/nexus_trade_view.js";

test("only the fixed NexusTrade bot routes to the dedicated view", () => {
  assert.equal(resolveDashboardView("nexus-trade"), "nexus");
  assert.equal(resolveDashboardView("donchian-a"), "standard");
  assert.equal(resolveDashboardView("nexus-speed"), "standard");
});

test("the view controller toggles its owned root without touching standard content", () => {
  const root = { hidden: true };
  const standardRoot = { hidden: false };
  const controller = mountNexusTradeView({ root, standardRoot });

  controller.show();
  assert.equal(root.hidden, false);
  assert.equal(standardRoot.hidden, true);

  controller.hide();
  assert.equal(root.hidden, true);
  assert.equal(standardRoot.hidden, false);
});
