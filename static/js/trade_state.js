export function formatCountdown(expiry, nowEpoch) {
  if (expiry === null || expiry === undefined || expiry === "") return "—";
  const remaining = Math.max(0, Number(expiry || 0) - Number(nowEpoch));
  return `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
}

export function contractPresentation(trade, nowEpoch = Math.floor(Date.now() / 1000)) {
  if (!trade || trade.status === "closed" || trade.lifecycle_state === "closed") return null;
  const awaiting = trade.lifecycle_state === "awaiting_settlement"
    || (trade.expiry_time !== null && trade.expiry_time !== undefined
      && trade.expiry_time !== "" && Number(trade.expiry_time) <= Number(nowEpoch));
  if (awaiting) return {
    state: "awaiting_settlement", chip: "AGUARDANDO",
    pnlLabel: "RESULTADO PROVISÓRIO", countdown: "Aguardando liquidação",
    countdownLabel: "Liquidação Deriv",
  };
  return {
    state: "live", chip: "AO VIVO", pnlLabel: "P&L FLUTUANTE",
    countdown: formatCountdown(trade.expiry_time, nowEpoch), countdownLabel: "Expira em",
  };
}
