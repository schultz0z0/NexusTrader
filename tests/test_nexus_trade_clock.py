import asyncio
import json
import math
import threading
import time
import unittest

from nexus_trade.clock import DispatchReceipt, EntryClock, EntryIntent
from nexus_trade.constants import NEXUS_DURATION_SECONDS
from nexus_trade.indicators import IndicatorFrame
from nexus_trade.strategy import Decision, NexusTradeStrategy


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

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds):
        self.epoch += seconds
        self.monotonic += seconds


class EntryClockBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_boundary_precedes_candle_finalization_strategy_and_intent(self):
        fake = FakeClock(epoch=119.5)
        events = []
        clock = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            async_sleeper=fake.sleep,
        )

        live_candle = {
            "time": 60, "open": 99, "high": 101, "low": 99, "close": 101,
            "is_closed": False, "close_epoch": None,
        }
        indicators = IndicatorFrame(
            epoch=60, upper=110, middle=100, lower=90, adx=20, values={},
        )
        strategy = NexusTradeStrategy()
        with self.assertRaisesRegex(ValueError, "closure|live"):
            strategy.on_closed_candle(live_candle, indicators)

        boundary = await clock.await_boundary(120)
        events.append(("boundary", boundary))
        closed_candle = {**live_candle, "is_closed": True, "close_epoch": 120}
        events.append(("finalize", 60, 120))
        closed_candle_decision = strategy.on_closed_candle(closed_candle, indicators)[0]
        events.append(("strategy", closed_candle_decision.signal_epoch))
        intent = clock.schedule(closed_candle_decision)
        events.append(("intent", intent.status))

        self.assertEqual(fake.sleeps, [0.5])
        self.assertEqual(events, [
            ("boundary", 120),
            ("finalize", 60, 120),
            ("strategy", 60),
            ("intent", "PENDING"),
        ])
        self.assertIsNone(intent.dispatch_epoch)
        self.assertIsNone(intent.accepted_epoch)

    async def test_causal_cycle_creates_indicators_only_after_finalization(self):
        fake = FakeClock(epoch=119.5)
        events = []
        created = {"indicators": None, "decision": None}

        class RecordingClock(EntryClock):
            async def await_boundary(self, target_epoch):
                result = await super().await_boundary(target_epoch)
                events.append("boundary")
                return result

            def schedule(self, value):
                result = super().schedule(value)
                events.append("intent")
                return result

        clock = RecordingClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            async_sleeper=fake.sleep,
        )

        def finalize(boundary_epoch):
            self.assertEqual(boundary_epoch, 120)
            self.assertIsNone(created["indicators"])
            self.assertIsNone(created["decision"])
            events.append("finalize")
            return {
                "time": 60, "open": 99, "high": 101, "low": 99, "close": 101,
                "is_closed": True, "close_epoch": boundary_epoch,
            }

        async def calculate(closed_candle):
            self.assertEqual(closed_candle["close_epoch"], 120)
            events.append("indicators")
            created["indicators"] = IndicatorFrame(
                epoch=60, upper=110, middle=100, lower=90, adx=20, values={},
            )
            return created["indicators"]

        class RecordingStrategy:
            def __init__(self):
                self.real = NexusTradeStrategy()

            def on_closed_candle(self, closed_candle, indicators, *, causal_epoch):
                self.assert_causal(causal_epoch, indicators)
                events.append("strategy")
                result = self.real.on_closed_candle(
                    closed_candle, indicators, causal_epoch=causal_epoch,
                )
                created["decision"] = result[0]
                return result

            def assert_causal(self, causal_epoch, indicators):
                self_outer.assertEqual(causal_epoch, 120)
                self_outer.assertIs(indicators, created["indicators"])
                self_outer.assertIsNone(created["decision"])

        self_outer = self
        cycle = await clock.await_and_prepare(
            120,
            finalize_candle=finalize,
            calculate_indicators=calculate,
            strategy=RecordingStrategy(),
        )

        self.assertEqual(events, [
            "boundary", "finalize", "indicators", "strategy", "intent",
        ])
        self.assertIs(cycle.indicators, created["indicators"])
        self.assertIs(cycle.decisions[0], created["decision"])
        self.assertEqual(cycle.intents[0].status, "PENDING")

    async def test_sync_cycle_callbacks_do_not_block_the_event_loop(self):
        fake = FakeClock(epoch=120)
        clock = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            async_sleeper=fake.sleep,
        )
        release = threading.Event()
        heartbeat = asyncio.Event()

        def finalize(_boundary):
            release.wait(timeout=1)
            return {
                "time": 60, "open": 99, "high": 101, "low": 99, "close": 101,
                "is_closed": True, "close_epoch": 120,
            }

        async def beat():
            await asyncio.sleep(0)
            heartbeat.set()
            release.set()

        started_at = time.perf_counter()
        beat_task = asyncio.create_task(beat())
        cycle_task = asyncio.create_task(clock.await_and_prepare(
            120,
            finalize_candle=finalize,
            calculate_indicators=lambda _candle: IndicatorFrame(
                epoch=60, upper=110, middle=100, lower=90, adx=20, values={},
            ),
            strategy=NexusTradeStrategy(),
        ))

        await asyncio.wait_for(heartbeat.wait(), timeout=0.2)
        self.assertLess(time.perf_counter() - started_at, 0.5)
        await beat_task
        cycle = await asyncio.wait_for(cycle_task, timeout=0.5)
        self.assertEqual(cycle.intents[0].status, "PENDING")

    async def test_await_boundary_is_async_and_has_no_finalizer_or_dispatcher(self):
        fake = FakeClock(epoch=119.75)
        clock = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            async_sleeper=fake.sleep,
        )

        self.assertEqual(await clock.await_boundary(120), 120)
        with self.assertRaises(TypeError):
            EntryClock(finalize_bucket=lambda *_: None)
        with self.assertRaises(TypeError):
            EntryClock(dispatch=lambda *_: None)

    async def test_next_boundary_is_pure_and_explicit_about_previous_boundary(self):
        fake = FakeClock(epoch=119.2)
        clock = EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            async_sleeper=fake.sleep,
        )

        self.assertEqual(clock.next_boundary_epoch(), 120)
        self.assertEqual(clock.next_boundary_epoch(after_epoch=120), 180)


class EntryClockIntentTests(unittest.TestCase):
    def clock_at(self, epoch):
        fake = FakeClock(epoch=epoch)
        return fake, EntryClock(
            epoch_now=fake.epoch_now,
            monotonic_now=fake.monotonic_now,
            async_sleeper=fake.sleep,
        )

    def accepted(self, delay):
        fake, clock = self.clock_at(120 + delay)
        pending = clock.schedule(decision())
        dispatched = pending.mark_dispatched(120 + delay)
        receipt = DispatchReceipt(
            decision_id=pending.decision_id,
            contract_id=101,
            dispatch_epoch=120 + delay,
            accepted_epoch=120 + delay,
        )
        return dispatched.apply_receipt(receipt)

    def test_schedule_after_closed_candle_creates_pending_intent_only(self):
        _fake, clock = self.clock_at(120.25)

        intent = clock.schedule(decision())

        self.assertEqual(intent.status, "PENDING")
        self.assertEqual(intent.prepared_epoch, 120.25)
        self.assertIsNone(intent.dispatch_epoch)
        self.assertIsNone(intent.accepted_epoch)
        self.assertIsNone(intent.contract_id)
        self.assertEqual(intent.duration_seconds, NEXUS_DURATION_SECONDS)

    def test_decision_cannot_be_prepared_before_its_closed_candle_boundary(self):
        _fake, clock = self.clock_at(119.999)

        with self.assertRaisesRegex(ValueError, "before target"):
            clock.schedule(decision())

    def test_accepted_latency_boundaries_are_inclusive(self):
        cases = (
            (0.250, "TARGET"),
            (1.000, "TARGET"),
            (2.000, "CONTINGENCY"),
        )
        for delay, status in cases:
            with self.subTest(delay=delay):
                intent = self.accepted(delay)
                self.assertEqual(intent.status, status)
                self.assertAlmostEqual(intent.entry_delay_ms, delay * 1000, places=6)
                self.assertEqual(intent.contract_id, 101)

    def test_2001ms_before_dispatch_is_stale_and_must_not_be_sent(self):
        _fake, clock = self.clock_at(122.001)

        intent = clock.schedule(decision())

        self.assertEqual(intent.status, "STALE_BEFORE_DISPATCH")
        self.assertIsNone(intent.dispatch_epoch)
        self.assertIsNone(intent.accepted_epoch)
        self.assertAlmostEqual(intent.entry_delay_ms, 2001.0, places=6)
        with self.assertRaisesRegex(ValueError, "STALE_BEFORE_DISPATCH"):
            intent.mark_dispatched(122.001)

    def test_pending_intent_can_expire_while_waiting_for_dispatcher(self):
        _fake, clock = self.clock_at(120.2)
        pending = clock.schedule(decision())

        stale = pending.mark_dispatched(122.001)

        self.assertEqual(stale.status, "STALE_BEFORE_DISPATCH")
        self.assertEqual(stale.pre_dispatch_epoch, 122.001)
        self.assertIsNone(stale.dispatch_epoch)
        self.assertAlmostEqual(stale.entry_delay_ms, 2001.0, places=6)

    def test_real_contract_accepted_after_deadline_is_accepted_late(self):
        _fake, clock = self.clock_at(120.2)
        sent = clock.schedule(decision()).mark_dispatched(120.2)

        accepted = sent.apply_receipt(DispatchReceipt(
            decision_id="decision-1",
            contract_id=202,
            dispatch_epoch=120.2,
            accepted_epoch=122.001,
        ))

        self.assertEqual(accepted.status, "ACCEPTED_LATE")
        self.assertEqual(accepted.contract_id, 202)
        self.assertEqual(accepted.dispatch_epoch, 120.2)
        self.assertEqual(accepted.accepted_epoch, 122.001)
        self.assertAlmostEqual(accepted.entry_delay_ms, 2001.0, places=6)
        self.assertIsNone(accepted.blocked_reason)

    def test_ambiguous_post_send_result_enters_ownership_quarantine(self):
        _fake, clock = self.clock_at(120.2)
        sent = clock.schedule(decision()).mark_dispatched(120.2)

        quarantined = sent.mark_ownership_quarantine("AMBIGUOUS_RESPONSE")

        self.assertEqual(quarantined.status, "OWNERSHIP_QUARANTINE")
        self.assertEqual(quarantined.dispatch_epoch, 120.2)
        self.assertIsNone(quarantined.accepted_epoch)
        self.assertIsNone(quarantined.contract_id)
        self.assertEqual(quarantined.error_code, "AMBIGUOUS_RESPONSE")
        with self.assertRaisesRegex(ValueError, "OWNERSHIP_QUARANTINE"):
            quarantined.mark_dispatched(120.3)

    def test_pre_dispatch_error_is_persistible_and_distinct_from_quarantine(self):
        _fake, clock = self.clock_at(120.2)

        failed = clock.schedule(decision()).mark_pre_dispatch_error("SERIALIZATION_FAILED")

        self.assertEqual(failed.status, "PRE_DISPATCH_ERROR")
        self.assertIsNone(failed.dispatch_epoch)
        self.assertEqual(failed.error_code, "SERIALIZATION_FAILED")

    def test_receipt_must_be_structured_real_and_match_sent_identity(self):
        _fake, clock = self.clock_at(120.2)
        sent = clock.schedule(decision()).mark_dispatched(120.2)

        with self.assertRaises(TypeError):
            sent.apply_receipt(None)
        with self.assertRaises(TypeError):
            sent.apply_receipt({"contract_id": "contract-1"})
        with self.assertRaisesRegex(ValueError, "decision_id"):
            sent.apply_receipt(DispatchReceipt(
                decision_id="other",
                contract_id=101,
                dispatch_epoch=120.2,
                accepted_epoch=120.3,
            ))
        with self.assertRaisesRegex(ValueError, "dispatch_epoch"):
            sent.apply_receipt(DispatchReceipt(
                decision_id="decision-1",
                contract_id=101,
                dispatch_epoch=120.3,
                accepted_epoch=120.4,
            ))

    def test_nontradable_decision_becomes_persistible_non_io_intent(self):
        _fake, clock = self.clock_at(120.0)

        intent = clock.schedule(decision(blocked_reason="ADX_BLOCKED"))

        self.assertEqual(intent.status, "ADX_BLOCKED")
        self.assertIsNone(intent.dispatch_epoch)
        self.assertIsNone(intent.accepted_epoch)

    def test_monotonic_and_epoch_values_must_be_finite_and_ordered(self):
        for bad_epoch in (math.nan, math.inf, -math.inf):
            with self.subTest(anchor=bad_epoch):
                fake = FakeClock(epoch=0)
                fake.epoch = bad_epoch
                with self.assertRaises(ValueError):
                    EntryClock(
                        epoch_now=fake.epoch_now,
                        monotonic_now=fake.monotonic_now,
                        async_sleeper=fake.sleep,
                    )

        _fake, clock = self.clock_at(120.2)
        pending = clock.schedule(decision())
        with self.assertRaisesRegex(ValueError, "target_epoch"):
            pending.mark_dispatched(119.9)
        sent = pending.mark_dispatched(120.2)
        with self.assertRaisesRegex(ValueError, "accepted_epoch"):
            sent.apply_receipt(DispatchReceipt(
                decision_id="decision-1",
                contract_id=101,
                dispatch_epoch=120.2,
                accepted_epoch=120.1,
            ))

    def test_intent_and_receipt_strictly_round_trip_json(self):
        intent = self.accepted(0.25)
        encoded = json.dumps(intent.to_dict(), sort_keys=True, allow_nan=False)
        restored = EntryIntent.from_dict(json.loads(encoded))

        receipt = DispatchReceipt(
            decision_id="decision-1",
            contract_id=101,
            dispatch_epoch=120.25,
            accepted_epoch=120.25,
        )
        receipt_encoded = json.dumps(receipt.to_dict(), sort_keys=True, allow_nan=False)

        self.assertEqual(restored, intent)
        self.assertEqual(DispatchReceipt.from_dict(json.loads(receipt_encoded)), receipt)

    def test_intent_from_dict_rejects_coercions_nan_and_invalid_status(self):
        valid = self.accepted(0.25).to_dict()
        mutations = (
            {"target_epoch": "120"},
            {"prepared_epoch": math.nan},
            {"dispatch_epoch": math.inf},
            {"accepted_epoch": 120.1},
            {"entry_delay_ms": -1.0},
            {"status": "STALE"},
            {"contract_type": "BUY"},
            {"lane": "unknown"},
            {"duration_seconds": "58"},
            {"contract_id": "101"},
            {"adx": 100.01},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                payload = {**valid, **mutation}
                with self.assertRaises((TypeError, ValueError)):
                    EntryIntent.from_dict(payload)

    def test_deriv_contract_id_is_a_strict_positive_integer(self):
        for invalid in (True, 0, -1, "101", 1.5):
            with self.subTest(contract_id=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    DispatchReceipt(
                        decision_id="decision-1",
                        contract_id=invalid,
                        dispatch_epoch=120.2,
                        accepted_epoch=120.3,
                    )


if __name__ == "__main__":
    unittest.main()
