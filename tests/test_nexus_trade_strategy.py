import json
import unittest
from dataclasses import asdict

from nexus_trade.domain import Lane
from nexus_trade.indicators import IndicatorFrame
from nexus_trade.strategy import Decision, NexusTradeStrategy, SetupState


def candle(epoch, opening, close, *, high=None, low=None):
    return {
        "time": epoch,
        "open": opening,
        "high": max(opening, close) if high is None else high,
        "low": min(opening, close) if low is None else low,
        "close": close,
        "is_closed": True,
    }


def frame(epoch, *, upper=110.0, middle=100.0, lower=90.0, adx=20.0):
    return IndicatorFrame(
        epoch=epoch,
        upper=upper,
        middle=middle,
        lower=lower,
        adx=adx,
        values={},
    )


class NexusTradeStrategyTests(unittest.TestCase):
    def test_upper_close_and_strict_upward_slope_waits_for_bearish_candle(self):
        strategy = NexusTradeStrategy()
        strategy.on_closed_candle(candle(0, 100, 105), frame(0, upper=110))

        breakout = strategy.on_closed_candle(
            candle(60, 109, 112, high=113), frame(60, upper=111)
        )[0]
        continuation = strategy.on_closed_candle(
            candle(120, 112, 114), frame(120, upper=112)
        )[0]
        confirmation = strategy.on_closed_candle(
            candle(180, 114, 113), frame(180, upper=113, adx=22.0)
        )[0]

        self.assertEqual(breakout.blocked_reason, "NO_TRADE")
        self.assertEqual(continuation.blocked_reason, "NO_TRADE")
        self.assertEqual(confirmation.contract_type, "PUT")
        self.assertEqual(confirmation.reason_codes, ("upper_reversal_confirmation",))
        self.assertEqual(confirmation.signal_epoch, 180)
        self.assertEqual(confirmation.target_epoch, 240)

    def test_lower_close_and_strict_downward_slope_waits_without_timeout(self):
        strategy = NexusTradeStrategy()
        strategy.on_closed_candle(candle(0, 100, 95), frame(0, lower=90))
        strategy.on_closed_candle(candle(60, 91, 88), frame(60, lower=89))
        for index in range(2, 9):
            strategy.on_closed_candle(
                candle(index * 60, 88 - index, 87 - index),
                frame(index * 60, lower=89 - index),
            )

        decision = strategy.on_closed_candle(
            candle(540, 78, 79), frame(540, lower=79, adx=21.99)
        )[0]

        self.assertEqual(decision.contract_type, "CALL")
        self.assertEqual(decision.reason_codes, ("lower_reversal_confirmation",))
        self.assertIsNone(decision.blocked_reason)

    def test_wick_equal_band_and_wrong_slope_do_not_start_external_setup(self):
        scenarios = (
            (candle(60, 105, 109, high=120), frame(60, upper=110)),
            (candle(60, 105, 110, high=120), frame(60, upper=110)),
            (candle(60, 105, 111, high=120), frame(60, upper=109)),
            (candle(60, 95, 91, low=80), frame(60, lower=90)),
            (candle(60, 95, 90, low=80), frame(60, lower=90)),
            (candle(60, 95, 89, low=80), frame(60, lower=91)),
        )
        for candidate, indicators in scenarios:
            with self.subTest(candidate=candidate, indicators=indicators):
                strategy = NexusTradeStrategy()
                strategy.on_closed_candle(candle(0, 100, 100), frame(0))
                strategy.on_closed_candle(candidate, indicators)
                followup = strategy.on_closed_candle(
                    candle(120, 100, 99), frame(120, upper=111, lower=89)
                )[0]
                self.assertEqual(followup.blocked_reason, "NO_TRADE")
                self.assertNotIn("upper_reversal_confirmation", followup.reason_codes)
                self.assertNotIn("lower_reversal_confirmation", followup.reason_codes)

    def test_doji_does_not_confirm_external_break(self):
        strategy = NexusTradeStrategy()
        strategy.on_closed_candle(candle(0, 100, 105), frame(0, upper=110))
        strategy.on_closed_candle(candle(60, 109, 112), frame(60, upper=111))

        doji = strategy.on_closed_candle(candle(120, 113, 113), frame(120, upper=112))[0]
        confirmation = strategy.on_closed_candle(
            candle(180, 113, 112), frame(180, upper=113)
        )[0]

        self.assertEqual(doji.blocked_reason, "NO_TRADE")
        self.assertEqual(confirmation.contract_type, "PUT")

    def test_center_cross_is_strict_and_enters_on_next_open_without_confirmation(self):
        cases = (
            (99, 101, "CALL", "center_cross_up"),
            (101, 99, "PUT", "center_cross_down"),
        )
        for opening, close, contract, reason in cases:
            with self.subTest(contract=contract):
                decision = NexusTradeStrategy().on_closed_candle(
                    candle(0, opening, close), frame(0, middle=100, adx=22)
                )[0]
                self.assertEqual(decision.contract_type, contract)
                self.assertEqual(decision.reason_codes, (reason,))
                self.assertEqual(decision.target_epoch, 60)

        equality_cases = ((100, 101), (99, 100), (100, 99), (101, 100))
        for opening, close in equality_cases:
            with self.subTest(opening=opening, close=close):
                decision = NexusTradeStrategy().on_closed_candle(
                    candle(0, opening, close), frame(0, middle=100)
                )[0]
                self.assertEqual(decision.blocked_reason, "NO_TRADE")

    def test_adx_limit_is_inclusive_and_blocked_signal_is_consumed(self):
        for adx in (21.99, 22.0):
            with self.subTest(adx=adx):
                decision = NexusTradeStrategy().on_closed_candle(
                    candle(0, 99, 101), frame(0, adx=adx)
                )[0]
                self.assertIsNone(decision.blocked_reason)
                self.assertEqual(decision.contract_type, "CALL")

        strategy = NexusTradeStrategy()
        blocked = strategy.on_closed_candle(
            candle(0, 99, 101), frame(0, adx=22.01)
        )[0]
        later = strategy.on_closed_candle(
            candle(60, 101, 102), frame(60, middle=100, adx=20)
        )[0]

        self.assertEqual(blocked.contract_type, "CALL")
        self.assertEqual(blocked.blocked_reason, "ADX_BLOCKED")
        self.assertEqual(later.blocked_reason, "NO_TRADE")

    def test_adx_block_clears_every_setup_created_on_the_signal_candle(self):
        strategy = NexusTradeStrategy()
        strategy.on_closed_candle(
            candle(0, 100, 105), frame(0, upper=110, lower=90)
        )
        strategy.on_closed_candle(
            candle(60, 109, 112), frame(60, upper=111, lower=89)
        )

        blocked = strategy.on_closed_candle(
            candle(120, 100, 85),
            frame(120, upper=112, middle=90, lower=88, adx=22.01),
        )[0]
        later = strategy.on_closed_candle(
            candle(180, 85, 86), frame(180, upper=113, middle=90, lower=87, adx=20)
        )[0]

        self.assertEqual(blocked.blocked_reason, "ADX_BLOCKED")
        self.assertEqual(later.blocked_reason, "NO_TRADE")
        self.assertNotIn("lower_reversal_confirmation", later.reason_codes)

    def test_matching_external_and_center_rules_create_one_deduplicated_decision(self):
        strategy = NexusTradeStrategy()
        strategy.on_closed_candle(candle(0, 100, 105), frame(0, upper=110))
        strategy.on_closed_candle(candle(60, 109, 112), frame(60, upper=111))

        decisions = strategy.on_closed_candle(
            candle(120, 101, 99), frame(120, upper=112, middle=100)
        )

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].contract_type, "PUT")
        self.assertEqual(
            decisions[0].reason_codes,
            ("upper_reversal_confirmation", "center_cross_down"),
        )

    def test_active_position_blocks_and_consumes_a_new_signal(self):
        strategy = NexusTradeStrategy(state=SetupState(position_active=True))

        blocked = strategy.on_closed_candle(
            candle(0, 99, 101), frame(0, adx=20)
        )[0]
        strategy.mark_position_closed()
        later = strategy.on_closed_candle(
            candle(60, 101, 102), frame(60, adx=20)
        )[0]

        self.assertEqual(blocked.blocked_reason, "POSITION_ACTIVE")
        self.assertEqual(later.blocked_reason, "NO_TRADE")

    def test_decisions_and_state_are_json_serializable_and_replay_deterministic(self):
        candles = (
            (candle(0, 100, 105), frame(0, upper=110)),
            (candle(60, 109, 112), frame(60, upper=111)),
            (candle(120, 112, 111), frame(120, upper=112, adx=22)),
        )

        first = NexusTradeStrategy(lane=Lane.CHAMPION)
        first_decisions = [first.on_closed_candle(bar, values)[0] for bar, values in candles]
        snapshot = first.snapshot()
        encoded = json.dumps({
            "state": snapshot,
            "decisions": [asdict(decision) for decision in first_decisions],
        }, sort_keys=True)

        second = NexusTradeStrategy(lane=Lane.CHAMPION)
        second_decisions = [second.on_closed_candle(bar, values)[0] for bar, values in candles]

        self.assertTrue(encoded)
        self.assertEqual(first_decisions, second_decisions)
        self.assertEqual(snapshot, second.snapshot())
        self.assertEqual(Decision.from_dict(first_decisions[-1].to_dict()), first_decisions[-1])
        self.assertEqual(SetupState.from_dict(snapshot), first.state)

    def test_rejects_live_misaligned_or_noncausal_input(self):
        cases = (
            (candle(1, 99, 101), frame(1)),
            ({**candle(0, 99, 101), "is_closed": False}, frame(0)),
            (candle(0, 99, 101), frame(60)),
        )
        for bar, values in cases:
            with self.subTest(bar=bar, values=values):
                with self.assertRaises(ValueError):
                    NexusTradeStrategy().on_closed_candle(bar, values)


if __name__ == "__main__":
    unittest.main()
