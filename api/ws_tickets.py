import secrets
import time


class WebSocketTicketStore:
    """In-memory, one-time WebSocket tickets so control keys never enter URLs."""

    def __init__(self, ttl_seconds=20, clock=time.monotonic):
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._tickets = {}

    def issue(self, bot_id: str) -> str:
        now = self._clock()
        self._discard_expired(now)
        ticket = secrets.token_urlsafe(32)
        self._tickets[ticket] = (str(bot_id), now + self.ttl_seconds)
        return ticket

    def consume(self, ticket: str, bot_id: str) -> bool:
        now = self._clock()
        record = self._tickets.get(ticket)
        if record is None:
            self._discard_expired(now)
            return False
        expected_bot_id, expires_at = record
        if expires_at < now:
            self._tickets.pop(ticket, None)
            return False
        if expected_bot_id != str(bot_id):
            return False
        self._tickets.pop(ticket, None)
        return True

    def _discard_expired(self, now):
        for ticket, (_, expires_at) in tuple(self._tickets.items()):
            if expires_at < now:
                self._tickets.pop(ticket, None)
