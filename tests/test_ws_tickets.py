import unittest

from api.ws_tickets import WebSocketTicketStore


class WebSocketTicketTests(unittest.TestCase):
    def test_ticket_is_bot_scoped_short_lived_and_single_use(self):
        now = [100.0]
        store = WebSocketTicketStore(ttl_seconds=10, clock=lambda: now[0])

        ticket = store.issue("bot-a")

        self.assertFalse(store.consume(ticket, "bot-b"))
        self.assertTrue(store.consume(ticket, "bot-a"))
        self.assertFalse(store.consume(ticket, "bot-a"))

        expired = store.issue("bot-a")
        now[0] = 111.0
        self.assertFalse(store.consume(expired, "bot-a"))


if __name__ == "__main__":
    unittest.main()
