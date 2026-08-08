const DONCHIAN_CONFIG = Object.freeze({
  period: 21,
  deviation: 1,
  depth: 15,
  backstep: 3,
});

const NEXUS_SPEED_CONFIG = Object.freeze({
  ema_period: 5,
  adx_period: 10,
  adx_threshold: 30,
  atr_period: 14,
  min_distance_atr: 0.30,
  touch_tolerance_bps: 1,
  ema_flat_tolerance_pips: 1,
  min_profit_ratio: 0.87,
  max_entry_delay_ticks: 1,
  min_closed_candles: 270,
});

export function strategyProfile(strategyId, overrides = {}) {
  if (strategyId === "nexus_speed") {
    const adxThreshold = Number(overrides.adx_threshold ?? 30);
    return {
      strategy_id: "nexus_speed",
      strategy_config: { ...NEXUS_SPEED_CONFIG, adx_threshold: adxThreshold },
      timeframe_seconds: 60,
      duration: 5,
      duration_unit: "t",
    };
  }
  return {
    strategy_id: "donchian",
    strategy_config: { ...DONCHIAN_CONFIG },
    timeframe_seconds: 60,
    duration: 2,
    duration_unit: "m",
  };
}

export function configuredBotPayload(bot, account) {
  const profile = strategyProfile(bot.strategy_id, bot.strategy_config);
  return {
    name: bot.name,
    account_id: account.account_id,
    account_type: account.account_type,
    symbol: bot.symbol,
    ...profile,
    initial_stake: bot.initial_stake,
    money_management: bot.money_management,
    money_config: bot.money_config || {},
    risk_config: bot.risk_config || {},
  };
}
