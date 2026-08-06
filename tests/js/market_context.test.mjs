import test from "node:test";
import assert from "node:assert/strict";
import { marketMatchesBot } from "../../static/js/store.js";

test("market context must match selected bot symbol and timeframe", () => {
  const bot = { symbol: "R_75", timeframe_seconds: 60 };

  assert.equal(marketMatchesBot({ symbol: "R_75", timeframe_seconds: 60 }, bot), true);
  assert.equal(marketMatchesBot({ symbol: "R_50", timeframe_seconds: 60 }, bot), false);
  assert.equal(marketMatchesBot({ symbol: "R_75", timeframe_seconds: 300 }, bot), false);
  assert.equal(marketMatchesBot(null, bot), false);
});
