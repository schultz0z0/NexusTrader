"""Serialized account dispatch with durable, causal NexusTrade ownership."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

from nexus_trade.clock import DispatchReceipt, EntryIntent
from nexus_trade.constants import (
    NEXUS_DEMO_STAKE,
    NEXUS_DURATION_SECONDS,
    NEXUS_DURATION_UNIT,
    NEXUS_SYMBOL,
    NEXUS_TRADE_BOT_ID,
)
from nexus_trade.domain import Lane
from nexus_trade.strategy import OwnershipReconciliation
from trading.ownership import OrderOwnershipCoordinator
from trading.proposal import ProposalManager


class DispatchBlockedError(RuntimeError):
    """A buy was blocked before transport ownership could become ambiguous."""


class EmergencyStopError(DispatchBlockedError):
    pass


class LanePositionActiveError(DispatchBlockedError):
    pass


class StaleIntentError(DispatchBlockedError):
    def __init__(self, intent: EntryIntent):
        super().__init__("entry intent became stale before dispatch")
        self.intent = intent


class PreDispatchError(DispatchBlockedError):
    def __init__(self, intent: EntryIntent, cause: BaseException):
        super().__init__(str(cause))
        self.intent = intent
        self.__cause__ = cause


class BuyRejectedError(DispatchBlockedError):
    pass


class OwnershipQuarantineError(RuntimeError):
    def __init__(
        self,
        intent: EntryIntent,
        correlation_id: str,
        cause: BaseException | None = None,
    ):
        super().__init__("buy ownership is unknown and requires reconciliation")
        self.intent = intent
        self.correlation_id = correlation_id
        self.__cause__ = cause


@dataclass(frozen=True)
class LaneJournalContext:
    nexus_version_id: str | None = None
    campaign_id: str | None = None


class AccountDispatcher:
    """One account executor with a single buy critical section and per-lane ownership."""

    def __init__(
        self,
        connection,
        repository,
        *,
        account_id: str,
        account_type: str,
        stake: float,
        epoch_now: Callable[[], float] = time.time,
        proposal_manager=None,
        executor=None,
        ownership_coordinator=None,
        buy_lock=None,
        stake_provider=None,
        management_active: bool = False,
    ):
        if type(account_id) is not str or not account_id.strip():
            raise ValueError("account_id must be configured")
        normalized_type = str(account_type).lower()
        if normalized_type not in {"demo", "real"}:
            raise ValueError("account_type must be demo or real")
        if isinstance(stake, bool) or not isinstance(stake, (int, float)) or float(stake) <= 0:
            raise ValueError("stake must be positive")
        if type(management_active) is not bool:
            raise TypeError("management_active must be boolean")
        self.connection = connection
        self.repository = repository
        self.account_id = account_id.strip()
        self.account_type = normalized_type
        self.stake = float(stake)
        self.management_active = management_active
        self._stake_provider = stake_provider or (lambda: self.stake)
        self._epoch_now = epoch_now
        self._proposal_manager = proposal_manager or ProposalManager(connection)
        self._ownership = ownership_coordinator or OrderOwnershipCoordinator(
            connection, repository, account_type=normalized_type,
        )
        self._executor = executor or self._ownership.executor
        self._buy_lock = buy_lock or asyncio.Lock()
        self._lane_lock = asyncio.Lock()
        self._blocked_lanes: set[str] = set()
        self._active_contracts: dict[str, int] = {}
        self._emergency_stop = False
        self._lane_context = {lane.value: LaneJournalContext() for lane in Lane}

    async def start(self) -> None:
        await self._ownership.start()

    async def close(self) -> None:
        return None

    @property
    def active_contracts(self) -> dict[str, int]:
        return dict(self._active_contracts)

    def set_emergency_stop(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise TypeError("emergency_stop must be boolean")
        self._emergency_stop = enabled

    def set_lane_context(
        self,
        lane: Lane | str,
        *,
        nexus_version_id: str | None,
        campaign_id: str | None,
    ) -> None:
        lane_value = Lane(lane).value
        self._lane_context[lane_value] = LaneJournalContext(
            nexus_version_id=nexus_version_id,
            campaign_id=campaign_id,
        )

    def restore_position(self, lane: Lane | str, contract_id: int) -> None:
        lane_value = Lane(lane).value
        if isinstance(contract_id, bool) or type(contract_id) is not int or contract_id <= 0:
            raise ValueError("contract_id must be a positive integer")
        current_owner = next(
            (
                owner_lane
                for owner_lane, owned_contract_id in self._active_contracts.items()
                if owned_contract_id == contract_id and owner_lane != lane_value
            ),
            None,
        )
        if current_owner is not None:
            raise ValueError(
                f"contract_id {contract_id} is already owned by lane {current_owner}",
            )
        self._blocked_lanes.add(lane_value)
        self._active_contracts[lane_value] = contract_id

    def restore_quarantine(self, lane: Lane | str) -> None:
        self._blocked_lanes.add(Lane(lane).value)

    async def reconcile_quarantine(
        self,
        correlation_id: str,
        decision_id: str,
    ) -> OwnershipReconciliation | None:
        intent = await self.repository.get_order_intent(correlation_id)
        if not intent:
            return None
        metadata = intent.get("metadata") or {}
        if metadata.get("correlation_id") != correlation_id:
            raise ValueError("persisted ownership correlation does not match")
        persisted_decision = intent.get("decision_id") or (
            metadata.get("entry_intent") or {}
        ).get("decision_id")
        if persisted_decision != decision_id:
            raise ValueError("persisted ownership decision does not match")
        if intent.get("state") == "owned" and intent.get("contract_id") is not None:
            contract_id = intent["contract_id"]
        else:
            contract = await self._ownership.reconciler.reconcile(intent)
            if contract is None:
                return None
            contract_id = contract.get("contract_id")
        if isinstance(contract_id, bool) or type(contract_id) is not int or contract_id <= 0:
            raise ValueError("reconciliation did not return a numeric contract_id")
        return OwnershipReconciliation(
            correlation_id=correlation_id,
            decision_id=decision_id,
            outcome="CONTRACT_FOUND",
            contract_id=contract_id,
        )

    def release_position(self, lane: Lane | str, contract_id: int) -> None:
        lane_value = Lane(lane).value
        if self._active_contracts.get(lane_value) != contract_id:
            raise ValueError("contract does not own the lane")
        self._active_contracts.pop(lane_value, None)
        self._blocked_lanes.discard(lane_value)

    async def submit(self, intent: EntryIntent) -> DispatchReceipt:
        if type(intent) is not EntryIntent:
            raise TypeError("submit requires an EntryIntent")
        if intent.status != "PENDING":
            raise ValueError("submit requires a PENDING intent")
        if intent.symbol != NEXUS_SYMBOL or intent.duration_seconds != NEXUS_DURATION_SECONDS:
            raise ValueError("intent does not use the fixed NexusTrade contract")
        lane = Lane(intent.lane).value
        await self._reserve_lane(lane)
        keep_reserved = False
        correlation_id = f"nexus-{intent.decision_id}"
        try:
            async with self._buy_lock:
                self._ensure_trading_open()
                stake = self._stake_provider()
                if (
                    isinstance(stake, bool)
                    or not isinstance(stake, (int, float))
                    or float(stake) <= 0
                ):
                    raise ValueError("managed stake must be positive")
                stake = float(stake)
                context = self._lane_context[lane]
                metadata = {
                    "correlation_id": correlation_id,
                    "order_intent_id": correlation_id,
                    "decision_id": intent.decision_id,
                    "entry_intent": intent.to_dict(),
                    "account_id": self.account_id,
                    "account_type": self.account_type,
                    "management_active": self.management_active,
                }
                await self.repository.create_order_intent({
                    "id": correlation_id,
                    "bot_id": NEXUS_TRADE_BOT_ID,
                    "account_id": self.account_id,
                    "session_id": None,
                    "proposal_id": "PENDING",
                    "symbol": NEXUS_SYMBOL,
                    "contract_type": intent.contract_type,
                    "stake": stake,
                    "price": stake,
                    "duration": NEXUS_DURATION_SECONDS,
                    "duration_unit": NEXUS_DURATION_UNIT,
                    "signal_epoch": intent.signal_epoch,
                    "metadata": metadata,
                    "lane": lane,
                    "nexus_version_id": context.nexus_version_id,
                    "campaign_id": context.campaign_id,
                    "decision_id": intent.decision_id,
                    "entry_delay_ms": None,
                })
                preflight = intent.mark_dispatched(float(self._epoch_now()))
                if preflight.status == "STALE_BEFORE_DISPATCH":
                    await self.repository.update_order_intent(
                        correlation_id,
                        "cancelled",
                        metadata={**metadata, "entry_intent": preflight.to_dict()},
                    )
                    raise StaleIntentError(preflight)
                try:
                    proposal = await self._proposal_manager.request_proposal(
                        NEXUS_SYMBOL,
                        intent.contract_type,
                        stake,
                        NEXUS_DURATION_SECONDS,
                        NEXUS_DURATION_UNIT,
                    )
                    if not proposal or not proposal.get("id"):
                        raise ValueError("proposal unavailable")
                except Exception as exc:
                    failed = intent.mark_pre_dispatch_error("PROPOSAL_ERROR")
                    await self.repository.update_order_intent(
                        correlation_id,
                        "rejected",
                        error=str(exc),
                        metadata={**metadata, "entry_intent": failed.to_dict()},
                    )
                    raise PreDispatchError(failed, exc) from exc

                ask_price = float(proposal.get("ask_price", stake))
                if hasattr(self.repository, "prepare_nexus_order_intent"):
                    await self.repository.prepare_nexus_order_intent(
                        correlation_id,
                        proposal_id=proposal["id"],
                        price=ask_price,
                        metadata=metadata,
                    )
                try:
                    self._ensure_trading_open()
                except EmergencyStopError:
                    await self.repository.update_order_intent(
                        correlation_id,
                        "cancelled",
                        error="emergency stop activated before buy",
                        metadata=metadata,
                    )
                    raise
                planned = intent.mark_dispatched(float(self._epoch_now()))
                sent_metadata = {**metadata, "entry_intent": planned.to_dict()}
                await self.repository.update_order_intent(
                    correlation_id, "submitting", metadata=sent_metadata,
                )
                # The journal write above may yield for an arbitrary amount of time.
                # Revalidate after it and make the transport call the very next await,
                # while still holding the account buy lock.
                try:
                    self._ensure_trading_open()
                    dispatch_epoch = float(self._epoch_now())
                    sent = intent.mark_dispatched(dispatch_epoch)
                    if sent.status == "STALE_BEFORE_DISPATCH":
                        raise StaleIntentError(sent)
                except (EmergencyStopError, StaleIntentError) as exc:
                    blocked_intent = exc.intent if isinstance(exc, StaleIntentError) else intent
                    await self.repository.update_order_intent(
                        correlation_id,
                        "cancelled",
                        error=(
                            "emergency stop activated before buy"
                            if isinstance(exc, EmergencyStopError)
                            else None
                        ),
                        metadata={**metadata, "entry_intent": blocked_intent.to_dict()},
                    )
                    raise
                try:
                    contract = await self._executor.buy(
                        proposal["id"],
                        ask_price,
                        passthrough={
                            "order_intent_id": correlation_id,
                            "decision_id": intent.decision_id,
                            "lane": lane,
                        },
                    )
                except asyncio.CancelledError:
                    quarantined = sent.mark_ownership_quarantine(
                        "AMBIGUOUS_BUY_RESPONSE",
                    )
                    keep_reserved = True
                    await self._persist_cancelled_quarantine(
                        correlation_id, metadata, quarantined,
                    )
                    raise
                except BaseException as exc:
                    quarantined = sent.mark_ownership_quarantine(
                        "AMBIGUOUS_BUY_RESPONSE",
                    )
                    await self.repository.update_order_intent(
                        correlation_id,
                        "reconcile_pending",
                        error=str(exc),
                        metadata={**metadata, "entry_intent": quarantined.to_dict()},
                    )
                    keep_reserved = True
                    raise OwnershipQuarantineError(
                        quarantined, correlation_id, exc,
                    ) from exc

                if contract is None:
                    await self.repository.update_order_intent(
                        correlation_id,
                        "rejected",
                        error="buy rejected",
                        metadata=sent_metadata,
                    )
                    raise BuyRejectedError("buy rejected")
                contract_id = contract.get("contract_id") if isinstance(contract, dict) else None
                if (
                    isinstance(contract_id, bool)
                    or type(contract_id) is not int
                    or contract_id <= 0
                ):
                    quarantined = sent.mark_ownership_quarantine(
                        "MALFORMED_BUY_RESPONSE",
                    )
                    await self.repository.update_order_intent(
                        correlation_id,
                        "reconcile_pending",
                        error="buy response has no numeric contract_id",
                        metadata={**metadata, "entry_intent": quarantined.to_dict()},
                    )
                    keep_reserved = True
                    raise OwnershipQuarantineError(quarantined, correlation_id)

                accepted_epoch = float(self._epoch_now())
                receipt = DispatchReceipt(
                    decision_id=intent.decision_id,
                    contract_id=contract_id,
                    dispatch_epoch=dispatch_epoch,
                    accepted_epoch=accepted_epoch,
                )
                accepted = sent.apply_receipt(receipt)
                keep_reserved = True
                self._claim_contract(lane, contract_id)
                # The response establishes ownership before durable enrichment.
                # Keep the lane blocked on any commit error; restart recovery will
                # quarantine the still-submitting journal rather than buy again.
                if hasattr(self.repository, "commit_nexus_known_ownership"):
                    await self.repository.commit_nexus_known_ownership(
                        correlation_id,
                        contract,
                        entry_intent=accepted.to_dict(),
                        entry_delay_ms=int(round(accepted.entry_delay_ms)),
                    )
                else:
                    accepted_metadata = {
                        **metadata, "entry_intent": accepted.to_dict(), **contract,
                    }
                    await self.repository.mark_order_intent_owned(correlation_id, contract)
                    await self.repository.update_order_intent(
                        correlation_id,
                        "owned",
                        metadata=accepted_metadata,
                    )
                return receipt
        finally:
            if not keep_reserved:
                async with self._lane_lock:
                    self._blocked_lanes.discard(lane)

    async def _reserve_lane(self, lane: str) -> None:
        async with self._lane_lock:
            self._ensure_trading_open()
            if lane in self._blocked_lanes:
                raise LanePositionActiveError(f"lane {lane} already owns a position")
            self._blocked_lanes.add(lane)

    def _ensure_trading_open(self) -> None:
        if self._emergency_stop:
            raise EmergencyStopError("emergency stop blocks new NexusTrade buys")

    def _claim_contract(self, lane: str, contract_id: int) -> None:
        current_owner = next(
            (
                owner_lane
                for owner_lane, owned_contract_id in self._active_contracts.items()
                if owned_contract_id == contract_id and owner_lane != lane
            ),
            None,
        )
        if current_owner is not None:
            raise ValueError(
                f"contract_id {contract_id} is already owned by lane {current_owner}",
            )
        self._active_contracts[lane] = contract_id

    async def _persist_cancelled_quarantine(
        self,
        correlation_id: str,
        metadata: dict,
        quarantined: EntryIntent,
    ) -> None:
        persistence = asyncio.create_task(
            self.repository.update_order_intent(
                correlation_id,
                "reconcile_pending",
                error="buy task cancelled after transport became possible",
                metadata={**metadata, "entry_intent": quarantined.to_dict()},
            ),
        )
        try:
            await asyncio.wait_for(asyncio.shield(persistence), timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            if not persistence.done():
                persistence.cancel()
            await asyncio.gather(persistence, return_exceptions=True)


class SharedDemoDispatcher(AccountDispatcher):
    """The one DEMO-account queue shared by Champion OFF and Trial."""

    def __init__(
        self,
        connection,
        repository,
        *,
        account_id: str,
        account_type: str = "demo",
        stake: float = NEXUS_DEMO_STAKE,
        **kwargs,
    ):
        if str(account_type).lower() != "demo":
            raise ValueError("SharedDemoDispatcher can only use a DEMO account")
        if isinstance(stake, bool) or type(stake) not in {int, float} or float(stake) != NEXUS_DEMO_STAKE:
            raise ValueError("SharedDemoDispatcher stake must be exactly 0.35")
        if kwargs.get("management_active", False) is not False:
            raise ValueError("SharedDemoDispatcher cannot enable money management")
        kwargs["management_active"] = False
        super().__init__(
            connection,
            repository,
            account_id=account_id,
            account_type="demo",
            stake=NEXUS_DEMO_STAKE,
            **kwargs,
        )


__all__ = [
    "AccountDispatcher",
    "BuyRejectedError",
    "DispatchBlockedError",
    "EmergencyStopError",
    "LanePositionActiveError",
    "OwnershipQuarantineError",
    "PreDispatchError",
    "SharedDemoDispatcher",
    "StaleIntentError",
]
