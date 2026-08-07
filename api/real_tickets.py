import secrets
import time


class RealStartTicketStore:
    """One-time REAL-start acknowledgements bound to an immutable bot revision."""

    def __init__(self, ttl_seconds=60, clock=time.monotonic):
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._tickets = {}

    def issue(self, bot: dict) -> str:
        now = self._clock()
        self._discard_expired(now)
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (
            str(bot["id"]),
            str(bot["account_id"]),
            int(bot.get("config_revision", 1)),
            now + self.ttl_seconds,
        )
        return ticket

    def consume(self, ticket: str, bot: dict) -> bool:
        now = self._clock()
        record = self._tickets.get(str(ticket or ""))
        if record is None:
            self._discard_expired(now)
            return False
        expected = (
            str(bot["id"]),
            str(bot["account_id"]),
            int(bot.get("config_revision", 1)),
        )
        if record[3] < now:
            self._tickets.pop(ticket, None)
            return False
        if record[:3] != expected:
            return False
        self._tickets.pop(ticket, None)
        return True

    def _discard_expired(self, now):
        for ticket, record in tuple(self._tickets.items()):
            if record[3] < now:
                self._tickets.pop(ticket, None)

    def revoke_all(self):
        self._tickets.clear()
