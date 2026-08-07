import asyncio
import unittest

from core.orchestrator import BotOrchestrator


class FakeRepository:
    def __init__(self, bots):
        self.bots = bots
        self.runtime_updates = []

    async def list_bots(self):
        return [dict(bot) for bot in self.bots]

    async def set_runtime_state(self, bot_id, state, error=None):
        self.runtime_updates.append((bot_id, state, error))


class FakeSession:
    def __init__(self, bot):
        self.bot = bot
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.stop_requests = 0

    async def run(self):
        self.started.set()
        await self.finished.wait()

    async def request_stop(self):
        self.stop_requests += 1
        self.finished.set()


class FakeSessionFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self, bot):
        session = FakeSession(bot)
        self.sessions.append(session)
        return session


class BotOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_running_desired_state_starts_exactly_one_session(self):
        repo = FakeRepository([{
            "id": "bot-a",
            "desired_state": "RUNNING",
            "config_revision": 1,
        }])
        factory = FakeSessionFactory()
        orchestrator = BotOrchestrator(repo, session_factory=factory)

        await orchestrator.reconcile_once()
        await orchestrator.reconcile_once()

        self.assertEqual(len(factory.sessions), 1)
        self.assertIn("bot-a", orchestrator.running_bot_ids)
        await orchestrator.stop()

    async def test_stopped_desired_state_requests_real_session_stop(self):
        bot = {"id": "bot-a", "desired_state": "RUNNING", "config_revision": 1}
        repo = FakeRepository([bot])
        factory = FakeSessionFactory()
        orchestrator = BotOrchestrator(repo, session_factory=factory)
        await orchestrator.reconcile_once()
        bot["desired_state"] = "STOPPED"

        await orchestrator.reconcile_once()
        await asyncio.sleep(0)

        self.assertEqual(factory.sessions[0].stop_requests, 1)
        await orchestrator.stop()

    async def test_two_running_bots_get_independent_sessions(self):
        repo = FakeRepository([
            {"id": "bot-a", "desired_state": "RUNNING", "config_revision": 1},
            {"id": "bot-b", "desired_state": "RUNNING", "config_revision": 1},
        ])
        factory = FakeSessionFactory()
        orchestrator = BotOrchestrator(repo, session_factory=factory)

        await orchestrator.reconcile_once()

        self.assertEqual(set(orchestrator.running_bot_ids), {"bot-a", "bot-b"})
        self.assertEqual(len(factory.sessions), 2)
        await orchestrator.stop()

    async def test_stale_runtime_is_normalized_when_stopped_bot_has_no_session(self):
        repo = FakeRepository([{
            "id": "bot-a", "desired_state": "STOPPED",
            "runtime_state": "STOPPING", "config_revision": 1,
        }])
        orchestrator = BotOrchestrator(repo, session_factory=FakeSessionFactory())

        await orchestrator.reconcile_once()

        self.assertEqual(repo.runtime_updates, [("bot-a", "STOPPED", None)])


if __name__ == "__main__":
    unittest.main()
