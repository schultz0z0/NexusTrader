import time
import uuid


CRITICAL_EVENT_TYPES = {
    "runtime.status",
    "market.history",
    "trade.opened",
    "trade.closed",
    "risk.blocked",
    "system.error",
    "nexus.runtime",
    "nexus.decision",
    "nexus.trade",
    "nexus.position",
    "nexus.campaign",
    "nexus.report",
    "nexus.trial_changed",
    "nexus.proposal",
    "nexus.version_changed",
}


def runtime_event(event_type: str, bot_id: str, epoch: int = None, **payload) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "schema_version": 1,
        "type": event_type,
        "bot_id": bot_id,
        "epoch": int(epoch if epoch is not None else time.time()),
        **payload,
    }


def is_critical_event(event: dict) -> bool:
    return event.get("type") in CRITICAL_EVENT_TYPES
