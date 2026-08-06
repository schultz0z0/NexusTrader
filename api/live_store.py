from collections import deque
from copy import deepcopy


class LiveStore:
    """Small bounded in-memory read model rebuilt continuously by runtime events."""

    def __init__(self, history_limit=1000, trade_limit=100, event_limit=10000):
        self.history_limit = history_limit
        self.trade_limit = trade_limit
        self._bots = {}
        self._event_order = deque(maxlen=event_limit)
        self._event_ids = set()

    def _state(self, bot_id):
        return self._bots.setdefault(bot_id, {
            "bot_id": bot_id,
            "runtime": {"status": "STOPPED", "error": None},
            "market": {"mode": "candles", "symbol": None, "timeframe_seconds": 60, "points": []},
            "last_tick": None,
            "active_trade": None,
            "recent_trades": [],
            "last_event_epoch": None,
        })

    def apply(self, event):
        event_id = event.get("event_id")
        if event_id and event_id in self._event_ids:
            return False
        if event_id:
            if len(self._event_order) == self._event_order.maxlen:
                self._event_ids.discard(self._event_order[0])
            self._event_order.append(event_id)
            self._event_ids.add(event_id)

        state = self._state(event["bot_id"])
        event_type = event.get("type")
        if event_type == "market.tick":
            market = state["market"]
            event_symbol = event.get("symbol")
            event_timeframe = int(event.get("timeframe_seconds", market["timeframe_seconds"]))
            if market.get("symbol") and event_symbol != market["symbol"]:
                return False
            if market.get("symbol") and event_timeframe != int(market["timeframe_seconds"]):
                return False
        state["last_event_epoch"] = event.get("epoch")
        if event_type == "runtime.status":
            status = event.get("status")
            state["runtime"] = {"status": status, "error": event.get("error")}
            if status == "STARTING":
                state["active_trade"] = None
                state["recent_trades"] = []
        elif event_type == "market.history":
            state["market"] = {
                "mode": event.get("mode", "candles"),
                "symbol": event.get("symbol"),
                "timeframe_seconds": event.get("timeframe_seconds", 60),
                "points": list(event.get("points", []))[-self.history_limit:],
            }
        elif event_type == "market.tick":
            state["last_tick"] = deepcopy(event)
            point = (
                {"time": event.get("epoch"), "value": event.get("price")}
                if state["market"]["mode"] == "line"
                else event.get("candle")
            )
            if point:
                points = state["market"]["points"]
                if points and points[-1].get("time") == point.get("time"):
                    points[-1] = deepcopy(point)
                else:
                    points.append(deepcopy(point))
                    del points[:-self.history_limit]
        elif event_type in {"trade.opened", "trade.updated"}:
            state["active_trade"] = deepcopy(event.get("trade"))
        elif event_type == "trade.closed":
            trade = deepcopy(event.get("trade"))
            state["active_trade"] = None
            contract_id = trade.get("contract_id") if trade else None
            state["recent_trades"] = [
                item for item in state["recent_trades"]
                if item.get("contract_id") != contract_id
            ]
            if trade:
                state["recent_trades"].insert(0, trade)
                del state["recent_trades"][self.trade_limit:]
        return True

    def snapshot(self, bot_id):
        return deepcopy(self._state(bot_id))
