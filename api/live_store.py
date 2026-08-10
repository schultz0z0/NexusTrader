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
        self.nexus_event_limit = min(int(event_limit), 1000)

    def _state(self, bot_id):
        return self._bots.setdefault(bot_id, {
            "bot_id": bot_id,
            "schema_version": 1,
            "snapshot_version": 0,
            "runtime": {"status": "STOPPED", "error": None},
            "market": {"mode": "candles", "symbol": None, "timeframe_seconds": 60, "points": []},
            "last_tick": None,
            "active_trade": None,
            "recent_trades": [],
            "last_event_epoch": None,
        })

    @classmethod
    def sanitize_event(cls, event):
        return cls._redact(deepcopy(event))

    @classmethod
    def _redact(cls, value):
        if isinstance(value, dict):
            sanitized = {}
            for key, item in value.items():
                normalized = str(key).lower()
                sensitive = (
                    normalized in {
                        "token", "api_key", "password", "authorization",
                        "credential", "credentials", "otp", "path",
                        "file_path", "local_path", "real_ticket", "ticket",
                    }
                    or "ticket" in normalized
                    or "secret" in normalized
                    or normalized.endswith("_token")
                    or normalized.endswith("_path")
                )
                if not sensitive:
                    sanitized[key] = cls._redact(item)
            return sanitized
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        if isinstance(value, tuple):
            return [cls._redact(item) for item in value]
        return value

    def hydrate_nexus(self, durable_snapshot):
        durable = self._redact(deepcopy(durable_snapshot or {}))
        state = self._state("nexus-trade")
        state["schema_version"] = int(durable.get("schema_version", 1))
        state["snapshot_version"] = max(
            int(state.get("snapshot_version", 0)),
            int(durable.get("snapshot_version", 0)),
        )
        for key in (
            "runtime", "lanes", "active_campaigns", "decisions", "trades",
            "reports", "proposals",
        ):
            if key in durable:
                state[key] = deepcopy(durable[key])
        state["emergency_stop"] = bool(
            durable.get(
                "emergency_stop",
                (durable.get("runtime") or {}).get("emergency_stop", False),
            ),
        )
        state.setdefault("nexus_events", [])
        return self.snapshot("nexus-trade")

    def apply(self, event):
        event = self.sanitize_event(event)
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
        if event_type in {
            "nexus.runtime",
            "nexus.decision",
            "nexus.trade",
            "nexus.campaign",
            "nexus.report",
            "nexus.trial_changed",
            "nexus.proposal",
            "nexus.version_changed",
        }:
            return self._apply_nexus_event(state, event)
        if event_type == "market.tick":
            market = state["market"]
            event_symbol = event.get("symbol")
            event_timeframe = int(event.get("timeframe_seconds", market["timeframe_seconds"]))
            if market.get("symbol") and event_symbol != market["symbol"]:
                return False
            if market.get("symbol") and event_timeframe != int(market["timeframe_seconds"]):
                return False
            if not market.get("symbol"):
                market["bot_id"] = event["bot_id"]
                market["symbol"] = event_symbol
                market["timeframe_seconds"] = event_timeframe
                market["mode"] = "candles" if event.get("candle") else "line"
        state["last_event_epoch"] = event.get("epoch")
        if event_type == "runtime.status":
            status = event.get("status")
            state["runtime"] = {"status": status, "error": event.get("error")}
            if status == "STARTING":
                state["active_trade"] = None
        elif event_type == "market.history":
            state["market"] = {
                "bot_id": event["bot_id"],
                "mode": event.get("mode", "candles"),
                "symbol": event.get("symbol"),
                "timeframe_seconds": event.get("timeframe_seconds", 60),
                "indicator_mode": event.get("indicator_mode", "donchian"),
                "points": list(event.get("points", []))[-self.history_limit:],
            }
            if "donchian" in event:
                state["market"]["donchian"] = deepcopy(event["donchian"])
            if "zigzag" in event:
                state["market"]["zigzag"] = deepcopy(event["zigzag"])
            if "ema" in event:
                state["market"]["ema"] = deepcopy(event["ema"])
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
            band = event.get("bollinger") or {}
            if band:
                donchian = state["market"].setdefault("donchian", {"upper": [], "middle": [], "lower": []})
                band_time = point.get("time") if point else event.get("epoch")
                for name in ("upper", "middle", "lower"):
                    value = band.get(name)
                    if value is None:
                        continue
                    series = donchian.setdefault(name, [])
                    band_point = {"time": band_time, "value": value}
                    if series and series[-1].get("time") == band_time:
                        series[-1] = band_point
                    else:
                        series.append(band_point)
                        del series[:-self.history_limit]
            if "zigzag" in event:
                state["market"]["zigzag"] = deepcopy(event["zigzag"])
            if event.get("indicator_mode"):
                state["market"]["indicator_mode"] = event["indicator_mode"]
            if event.get("ema") is not None:
                series = state["market"].setdefault("ema", [])
                ema_time = point.get("time") if point else event.get("epoch")
                ema_point = {"time": ema_time, "value": event["ema"]}
                if not series or series[-1].get("time") != ema_time:
                    series.append(ema_point)
                    del series[:-self.history_limit]
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

    def _apply_nexus_event(self, state, event):
        event_revision = int(
            event.get("snapshot_version", state.get("snapshot_version", 0) + 1),
        )
        if event_revision < int(state.get("snapshot_version", 0)):
            return False
        state["schema_version"] = int(event.get("schema_version", 1))
        state["snapshot_version"] = max(
            event_revision,
            int(state.get("snapshot_version", 0)),
        )
        state["last_event_epoch"] = event.get("epoch")
        state["last_nexus_event"] = deepcopy(event)
        events = state.setdefault("nexus_events", [])
        events.append(deepcopy(event))
        del events[:-self.nexus_event_limit]

        payload = deepcopy(event.get("payload") or {})
        event_type = event["type"]
        if event_type == "nexus.runtime":
            runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else payload
            state.setdefault("runtime", {}).update(runtime)
            emergency = payload.get(
                "emergency_stop",
                state["runtime"].get("emergency_stop", state.get("emergency_stop", False)),
            )
            state["emergency_stop"] = bool(emergency)
            state["runtime"]["emergency_stop"] = int(bool(emergency))
        elif event_type == "nexus.decision":
            self._upsert(state.setdefault("decisions", []), payload, ("id", "decision_id"))
        elif event_type == "nexus.trade":
            self._upsert(state.setdefault("trades", []), payload, ("id", "contract_id"))
        elif event_type == "nexus.campaign":
            self._upsert(state.setdefault("campaigns", []), payload, ("id",))
            active = state.setdefault("active_campaigns", [])
            if payload.get("status") == "ACTIVE":
                self._upsert(active, payload, ("id",))
            elif payload.get("id") is not None:
                active[:] = [item for item in active if item.get("id") != payload["id"]]
        elif event_type == "nexus.report":
            self._upsert(state.setdefault("reports", []), payload, ("id",))
        elif event_type == "nexus.proposal":
            self._upsert(state.setdefault("proposals", []), payload, ("id",))
        elif event_type == "nexus.trial_changed":
            state["trial_change"] = payload
        elif event_type == "nexus.version_changed":
            state["version_change"] = payload
            if isinstance(payload.get("lanes"), list):
                state["lanes"] = payload["lanes"]
            elif payload.get("lane") and isinstance(payload.get("version"), dict):
                self._upsert(state.setdefault("lanes", []), payload, ("lane",))
        return True

    @staticmethod
    def _upsert(items, payload, identity_keys):
        identity_key = next(
            (key for key in identity_keys if payload.get(key) is not None),
            None,
        )
        if identity_key is None:
            items.append(payload)
            return
        identity = payload[identity_key]
        items[:] = [item for item in items if item.get(identity_key) != identity]
        items.insert(0, payload)

    def snapshot(self, bot_id):
        return self._redact(deepcopy(self._state(bot_id)))
