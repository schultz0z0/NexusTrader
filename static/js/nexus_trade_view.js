import { NEXUS_BOT_ID } from "./nexus_trade_store.js";

export function resolveDashboardView(botId) {
  return botId === NEXUS_BOT_ID ? "nexus" : "standard";
}

export function mountNexusTradeView({ root = null, standardRoot = null } = {}) {
  return {
    show() {
      if (root) root.hidden = false;
      if (standardRoot) standardRoot.hidden = true;
    },
    hide() {
      if (root) root.hidden = true;
      if (standardRoot) standardRoot.hidden = false;
    },
  };
}
