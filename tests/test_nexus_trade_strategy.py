import json
import unittest
from dataclasses import asdict

from nexus_trade.domain import Lane
from nexus_trade.indicators import IndicatorFrame
from nexus_trade.strategy import (
    Decision,
    NexusTradeStrategy,
    OwnershipReconciliation,
    SetupState,
)


def candle(epoch, opening, close, *, high=None, low=None):
    return {
        "time": epoch,
        "open": opening,
        "high": max(opening, close) if high is None else high,
        "low": min(opening, close) if low is None else low,
        "close": close,
        "is_closed": True,
        "close_epoch": epoch + 60,
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

    def test_reserved_position_blocks_signal_and_owner_controls_active_close(self):
        strategy = NexusTradeStrategy()
        owner = strategy.on_closed_candle(candle(0, 99, 101), frame(0, adx=20))[0]

        blocked = strategy.on_closed_candle(
            candle(60, 101, 99), frame(60, adx=20)
        )[0]
        self.assertEqual(blocked.blocked_reason, "POSITION_ACTIVE")
        self.assertEqual(strategy.state.position_status, "RESERVED")
        self.assertEqual(strategy.state.owner_decision_id, owner.decision_id)

        with self.assertRaisesRegex(ValueError, "owner"):
            strategy.mark_position_active("wrong-owner", 101)
        strategy.mark_position_active(owner.decision_id, 101)
        self.assertEqual(strategy.state.position_status, "ACTIVE")
        self.assertEqual(strategy.state.contract_id, 101)

        with self.assertRaisesRegex(ValueError, "owner"):
            strategy.mark_position_closed("wrong-owner", 101)
        with self.assertRaisesRegex(ValueError, "contract"):
            strategy.mark_position_closed(owner.decision_id, 102)
        strategy.mark_position_closed(owner.decision_id, 101)
        self.assertEqual(strategy.state.position_status, "IDLE")

    def test_reservation_can_only_be_released_by_its_owner_before_send(self):
        strategy = NexusTradeStrategy()
        owner = strategy.on_closed_candle(candle(0, 99, 101), frame(0))[0]

        with self.assertRaisesRegex(ValueError, "owner"):
            strategy.release_reservation("wrong-owner")
        strategy.release_reservation(owner.decision_id)

        self.assertEqual(strategy.state.position_status, "IDLE")
        self.assertIsNone(strategy.state.owner_decision_id)

    def test_quarantine_survives_restart_and_cannot_be_blindly_released(self):
        strategy = NexusTradeStrategy()
        owner = strategy.on_closed_candle(candle(0, 99, 101), frame(0))[0]
        strategy.mark_position_quarantined(owner.decision_id)

        restarted = NexusTradeStrategy(state=json.loads(json.dumps(strategy.snapshot())))

        self.assertEqual(restarted.state.position_status, "QUARANTINED")
        self.assertEqual(restarted.state.owner_decision_id, owner.decision_id)
        with self.assertRaisesRegex(ValueError, "QUARANTINED"):
            restarted.release_reservation(owner.decision_id)
        with self.assertRaisesRegex(ValueError, "QUARANTINED"):
            restarted.mark_position_closed(owner.decision_id, 101)

    def test_quarantine_reconciliation_found_contract_is_owner_checked_and_idempotent(self):
        strategy = NexusTradeStrategy()
        owner = strategy.on_closed_candle(candle(0, 99, 101), frame(0))[0]
        strategy.mark_position_quarantined(owner.decision_id)
        found = OwnershipReconciliation(
            correlation_id="reconcile-1",
            decision_id=owner.decision_id,
            outcome="CONTRACT_FOUND",
            contract_id=707,
        )

        with self.assertRaisesRegex(ValueError, "owner"):
            strategy.reconcile_quarantine(OwnershipReconciliation(
                correlation_id="reconcile-wrong",
                decision_id="wrong-owner",
                outcome="CONTRACT_FOUND",
                contract_id=707,
            ))
        strategy.reconcile_quarantine(found)
        snapshot = strategy.snapshot()
        strategy.reconcile_quarantine(found)

        self.assertEqual(strategy.snapshot(), snapshot)
        self.assertEqual(strategy.state.position_status, "ACTIVE")
        self.assertEqual(strategy.state.contract_id, 707)
        restarted = NexusTradeStrategy(state=json.loads(json.dumps(snapshot)))
        restarted.reconcile_quarantine(found)
        restarted.mark_position_closed(owner.decision_id, 707)
        self.assertEqual(restarted.state.position_status, "IDLE")

    def test_quarantine_reconciliation_confirmed_absent_is_strict_and_idempotent(self):
        strategy = NexusTradeStrategy()
        owner = strategy.on_closed_candle(candle(0, 99, 101), frame(0))[0]
        strategy.mark_position_quarantined(owner.decision_id)
        absent = OwnershipReconciliation(
            correlation_id="reconcile-absent",
            decision_id=owner.decision_id,
            outcome="PURCHASE_ABSENT",
            contract_id=None,
        )

        with self.assertRaises(TypeError):
            strategy.reconcile_quarantine(False)
        strategy.reconcile_quarantine(absent)
        snapshot = strategy.snapshot()
        strategy.reconcile_quarantine(absent)

        self.assertEqual(strategy.snapshot(), snapshot)
        self.assertEqual(strategy.state.position_status, "IDLE")
        restarted = NexusTradeStrategy(state=json.loads(json.dumps(snapshot)))
        restarted.reconcile_quarantine(absent)
        self.assertEqual(restarted.state.position_status, "IDLE")

    def test_reconciliation_rejects_invalid_outcome_contract_and_correlation(self):
        cases = (
            {"correlation_id": "", "decision_id": "owner", "outcome": "PURCHASE_ABSENT", "contract_id": None},
            {"correlation_id": "r1", "decision_id": "owner", "outcome": "MAYBE", "contract_id": None},
            {"correlation_id": "r1", "decision_id": "owner", "outcome": "CONTRACT_FOUND", "contract_id": None},
            {"correlation_id": "r1", "decision_id": "owner", "outcome": "PURCHASE_ABSENT", "contract_id": 1},
            {"correlation_id": "r1", "decision_id": "owner", "outcome": "CONTRACT_FOUND", "contract_id": "1"},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises((TypeError, ValueError)):
                    OwnershipReconciliation.from_dict(payload)

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
        }, sort_keys=True, allow_nan=False)

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

    def test_requires_unambiguous_closed_candle_evidence(self):
        base = candle(0, 99, 101)
        ambiguous = dict(base)
        ambiguous.pop("is_closed")
        ambiguous.pop("close_epoch")
        cases = (
            ambiguous,
            {**base, "is_closed": "true"},
            {**base, "is_closed": True, "closed": False},
            {**base, "close_epoch": None},
            {**base, "close_epoch": 61},
            {**base, "close_epoch": True},
            {**base, "closed": "false"},
        )
        for bar in cases:
            with self.subTest(bar=bar):
                with self.assertRaises(ValueError):
                    NexusTradeStrategy().on_closed_candle(bar, frame(0))

    def test_causal_epoch_rejects_a_candle_closed_after_the_cycle(self):
        strategy = NexusTradeStrategy()
        before = strategy.snapshot()

        with self.assertRaisesRegex(ValueError, "causal_epoch"):
            strategy.on_closed_candle(
                candle(60, 99, 101), frame(60), causal_epoch=60,
            )

        self.assertEqual(strategy.snapshot(), before)
        historical = strategy.on_closed_candle(candle(60, 99, 101), frame(60))[0]
        self.assertEqual(historical.target_epoch, 120)

    def test_adx_domain_fails_before_any_state_mutation(self):
        for invalid in (-0.01, 100.01, float("nan"), float("inf"), -float("inf")):
            with self.subTest(adx=invalid):
                strategy = NexusTradeStrategy()
                before = strategy.snapshot()
                with self.assertRaises((TypeError, ValueError)):
                    strategy.on_closed_candle(
                        candle(0, 99, 101), frame(0, adx=invalid)
                    )
                self.assertEqual(strategy.snapshot(), before)

        for valid in (0.0, 100.0):
            with self.subTest(adx=valid):
                value = Decision(
                    decision_id="decision",
                    contract_type="CALL",
                    reason_codes=("center_cross_up",),
                    signal_epoch=0,
                    target_epoch=60,
                    adx=valid,
                    blocked_reason=None,
                )
                self.assertEqual(value.adx, valid)

    def test_setup_state_from_dict_is_strict_about_status_epochs_and_floats(self):
        valid = SetupState().to_dict()
        mutations = (
            {"last_candle_epoch": "60"},
            {"upper_break_epoch": 1},
            {"previous_upper": float("nan")},
            {"position_status": "active"},
            {"position_status": "IDLE", "owner_decision_id": "owner"},
            {"unexpected": "field"},
            {"contract_id": "101"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises((TypeError, ValueError)):
                    SetupState.from_dict({**valid, **mutation})

    def test_decision_from_dict_rejects_coercions_nan_and_invalid_domain(self):
        valid = NexusTradeStrategy().on_closed_candle(
            candle(0, 99, 101), frame(0)
        )[0].to_dict()
        mutations = (
            {"signal_epoch": "0"},
            {"target_epoch": 120},
            {"adx": float("nan")},
            {"adx": -0.01},
            {"adx": 100.01},
            {"contract_type": "BUY"},
            {"lane": "unknown"},
            {"blocked_reason": "STALE"},
            {"reason_codes": ["center_cross_up", 1]},
            {"decision_id": ""},
            {"unexpected": "field"},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                with self.assertRaises((TypeError, ValueError)):
                    Decision.from_dict({**valid, **mutation})


if __name__ == "__main__":
    unittest.main()
