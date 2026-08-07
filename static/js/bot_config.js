const FIXED_STRATEGY_CONFIG = Object.freeze({
  period: 21,
  deviation: 1,
  depth: 15,
  backstep: 3,
});

export function configuredBotPayload(bot, account) {
  return {
    name: bot.name,
    account_id: account.account_id,
    account_type: account.account_type,
    symbol: bot.symbol,
    timeframe_seconds: 60,
    strategy_id: "donchian",
    strategy_config: { ...FIXED_STRATEGY_CONFIG },
    duration: 2,
    duration_unit: "m",
    initial_stake: bot.initial_stake,
    money_management: bot.money_management,
    money_config: bot.money_config || {},
    risk_config: bot.risk_config || {},
  };
}
