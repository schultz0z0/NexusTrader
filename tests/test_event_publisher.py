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


class FailOnceHttpClient(FakeHttpClient):
    async def post(self, url, json, headers):
        self.posts.append((url, json, headers))
        if len(self.posts) == 1:
            raise ConnectionError("temporary API outage")
        return FakeResponse()


class EventPublisherTests(unittest.IsolatedAsyncioTestCase):
    async def test_critical_trade_event_is_retried_after_transient_failure(self):
        client = FailOnceHttpClient()
        publisher = HttpEventPublisher(
            base_url="http://api.test",
            internal_token="internal",
            client=client,
            queue_max=10,
            retry_delays=(0,),
        )
        await publisher.start()
        await publisher.publish({"type": "trade.closed", "event_id": "closed-42"})
        await publisher.flush()
        await publisher.close()

        self.assertEqual(len(client.posts), 2)
        self.assertEqual(client.posts[0][1], client.posts[1][1])
        self.assertEqual(publisher.failed_events, 0)

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
