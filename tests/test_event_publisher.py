import asyncio
import unittest

from core.event_publisher import HttpEventPublisher


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None


class FakeHttpClient:
    def __init__(self):
        self.posts = []
        self.closed = False

    async def post(self, url, json, headers):
        self.posts.append((url, json, headers))
        return FakeResponse()

    async def aclose(self):
        self.closed = True


class EventPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_reuses_one_client_for_multiple_events(self):
        client = FakeHttpClient()
        publisher = HttpEventPublisher(
            base_url="http://api.test",
            internal_token="internal",
            client=client,
            queue_max=10,
        )
        await publisher.start()
        await publisher.publish({"type": "market.tick", "event_id": "1"})
        await publisher.publish({"type": "runtime.status", "event_id": "2"})
        await publisher.flush()
        await publisher.close()

        self.assertEqual(len(client.posts), 2)
        self.assertEqual(client.posts[0][2]["X-Internal-Token"], "internal")
        self.assertTrue(client.closed)

    async def test_full_queue_drops_new_market_tick_without_growing(self):
        publisher = HttpEventPublisher(
            base_url="http://api.test",
            internal_token="internal",
            client=FakeHttpClient(),
            queue_max=1,
        )

        first = await publisher.publish({"type": "market.tick", "event_id": "1"})
        second = await publisher.publish({"type": "market.tick", "event_id": "2"})

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(publisher.queue_size, 1)


if __name__ == "__main__":
    unittest.main()
