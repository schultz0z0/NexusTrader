import hashlib
import json


NEXUS_TRADE_BOT_ID = "nexus-trade"
NEXUS_SYMBOL = "R_100"
NEXUS_TIMEFRAME_SECONDS = 60
NEXUS_DURATION_SECONDS = 58
NEXUS_DURATION_UNIT = "s"
NEXUS_DEMO_STAKE = 0.35
NEXUS_PROVENANCE_HASH = hashlib.sha256(json.dumps(
    {
        "feature_schema": "nexus-feature-v1",
        "source": "deriv-ticks-history-stream",
        "symbol": NEXUS_SYMBOL,
        "timeframe_seconds": NEXUS_TIMEFRAME_SECONDS,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")).hexdigest()
