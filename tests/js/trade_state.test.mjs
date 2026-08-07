import test from "node:test";
import assert from "node:assert/strict";
import { contractPresentation, formatCountdown } from "../../static/js/trade_state.js";

test("live contract retains countdown and floating pnl", () => {
  assert.deepEqual(contractPresentation({ status: "open", expiry_time: 160 }, 100), {
    state: "live", chip: "AO VIVO", pnlLabel: "P&L FLUTUANTE",
    countdown: "1:00", countdownLabel: "Expira em",
  });
});

test("expired open contract waits for Deriv settlement", () => {
  assert.deepEqual(contractPresentation({ status: "open", expiry_time: 100 }, 101), {
    state: "awaiting_settlement", chip: "AGUARDANDO",
    pnlLabel: "RESULTADO PROVISÓRIO", countdown: "Aguardando liquidação",
    countdownLabel: "Liquidação Deriv",
  });
});

test("backend awaiting state wins over a slow local clock", () => {
  assert.equal(contractPresentation({
    status: "open", lifecycle_state: "awaiting_settlement", expiry_time: 200,
  }, 100).state, "awaiting_settlement");
});

test("missing expiry remains live without inventing a deadline", () => {
  assert.deepEqual(contractPresentation({ status: "open", expiry_time: null }, 100), {
    state: "live", chip: "AO VIVO", pnlLabel: "P&L FLUTUANTE",
    countdown: "—", countdownLabel: "Expira em",
  });
});

test("closed contract is not presented as active", () => {
  assert.equal(contractPresentation({ status: "closed", expiry_time: 100 }, 100), null);
});

test("countdown is deterministic", () => {
  assert.equal(formatCountdown(181, 120), "1:01");
});
