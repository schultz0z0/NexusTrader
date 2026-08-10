import json
import unittest
from dataclasses import asdict

from nexus_trade.clock import EntryClock, EntryIntent
from nexus_trade.constants import NEXUS_DURATION_SECONDS
from nexus_trade.strategy import Decision


def decision(target_epoch=120, *, blocked_reason=None, contract_type="CALL"):
    return Decision(
        decision_id="decision-1",
        contract_type=contract_type,
        reason_codes=("center_cross_up",),
        signal_epoch=target_epoch - 60,
        target_epoch=target_epoch,
        adx=20.0,
        blocked_reason=blocked_reason,
    )


class FakeClock:
    def __init__(self, epoch, monotonic=10.0):
        self.epoch = float(epoch)
        self.monotonic = float(monotonic)
        self.sleeps = []

    def epoch_now(self):
        return self.epoch

    def monotonic_now(self):
        return self.monotonic

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.epoch += seconds
        self.monotonic += seconds

    def advance(self, seconds):
        self.epoch += seconds
        self.monotonic += seconds


class EntryClockTests(unittest.TestCase):
    def test_schedules_on_boundary_and_finalizes_before_dispatch_without_a_tick(self):
        fake = FakeClock(epoch=119.5)
        events = []
        clock = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            sleeper=fake.sleep,
            finalize_bucket=lambda start, end: events.append(("finalize", start, end)),
            dispatch=lambda item: events.append(("dispatch", item.decision_id)),
        )

        intent = clock.schedule(decision())

        self.assertEqual(fake.sleeps, [0.5])
        self.assertEqual(events, [("finalize", 60, 120), ("dispatch", "decision-1")])
        self.assertEqual(intent.dispatch_epoch, 120.0)
        self.assertEqual(intent.accepted_epoch, 120.0)
        self.assertEqual(intent.entry_delay_ms, 0.0)
        self.assertEqual(intent.classification, "TARGET")
        self.assertEqual(intent.duration_seconds, NEXUS_DURATION_SECONDS)

    def test_latency_boundaries_are_inclusive_and_2001ms_is_stale(self):
        cases = (
            (0.250, "TARGET", True),
            (1.000, "TARGET", True),
            (2.000, "CONTINGENCY", True),
            (2.001, "STALE", False),
        )
        for delay, classification, dispatched in cases:
            with self.subTest(delay=delay):
                fake = FakeClock(epoch=120 + delay)
                calls = []
                intent = EntryClock(
                    epoch_now=fake.epoch_now,
                    monotonic_now=fake.monotonic_now,
                    sleeper=fake.sleep,
                    dispatch=lambda item: calls.append(item.decision_id),
                ).schedule(decision())

                self.assertEqual(intent.classification, classification)
                self.assertEqual(bool(calls), dispatched)
                self.assertAlmostEqual(intent.entry_delay_ms, delay * 1000, places=6)
                if dispatched:
                    self.assertAlmostEqual(intent.dispatch_epoch, 120 + delay, places=9)
                    self.assertAlmostEqual(intent.accepted_epoch, 120 + delay, places=9)
                else:
                    self.assertIsNone(intent.dispatch_epoch)
                    self.assertIsNone(intent.accepted_epoch)
                    self.assertEqual(intent.blocked_reason, "STALE")

    def test_acceptance_time_controls_target_vs_contingency(self):
        fake = FakeClock(epoch=120.2)

        def dispatch(_item):
            fake.advance(1.3)

        intent = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            sleeper=fake.sleep,
            dispatch=dispatch,
        ).schedule(decision())

        self.assertAlmostEqual(intent.dispatch_epoch, 120.2)
        self.assertAlmostEqual(intent.accepted_epoch, 121.5)
        self.assertAlmostEqual(intent.entry_delay_ms, 1500.0)
        self.assertEqual(intent.classification, "CONTINGENCY")

    def test_epoch_alignment_uses_monotonic_time_after_anchor(self):
        fake = FakeClock(epoch=119.0, monotonic=500.0)
        clock = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            sleeper=fake.sleep,
        )
        fake.epoch = 9999.0

        intent = clock.schedule(decision())

        self.assertEqual(intent.dispatch_epoch, 120.0)
        self.assertEqual(fake.sleeps, [1.0])

    def test_nontradable_decision_is_preserved_without_wait_or_dispatch(self):
        fake = FakeClock(epoch=119.0)
        calls = []
        intent = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            sleeper=fake.sleep,
            dispatch=lambda item: calls.append(item),
        ).schedule(decision(blocked_reason="ADX_BLOCKED"))

        self.assertEqual(intent.classification, "ADX_BLOCKED")
        self.assertEqual(intent.blocked_reason, "ADX_BLOCKED")
        self.assertEqual(fake.sleeps, [])
        self.assertEqual(calls, [])

    def test_intent_is_json_serializable_and_round_trips(self):
        fake = FakeClock(epoch=120.25)
        intent = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            sleeper=fake.sleep,
        ).schedule(decision())

        self.assertTrue(json.dumps(asdict(intent), sort_keys=True))
        self.assertEqual(EntryIntent.from_dict(intent.to_dict()), intent)


if __name__ == "__main__":
    unittest.main()
