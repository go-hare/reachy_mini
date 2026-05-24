"""Motor and device lock arbitration for v4 robot actions."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Iterable

from .action import CancelToken
from .errors import LockBusyError, LockTimeoutError

LOGGER = logging.getLogger(__name__)

LOCK_NAMES = frozenset(
    {
        "head",
        "head_yaw",
        "head_pitch",
        "head_roll",
        "body_yaw",
        "antenna_left",
        "antenna_right",
        "speaker",
        "camera_focus",
        "full_body",
    }
)

HEAD_CHILD_LOCKS = frozenset({"head_yaw", "head_pitch", "head_roll"})


@dataclass
class LockLease:
    """A held lock lease."""

    lock_name: str
    owner_id: str
    action_id: str
    request_id: str
    priority: int
    interruptible: bool
    acquired_at: float
    expires_at: float | None
    renew_count: int


@dataclass
class _LeaseRecord:
    lease: LockLease
    cancel_token: CancelToken


@dataclass(frozen=True)
class LockDecision:
    """Structured lock decision for tests and logs."""

    decision: str
    lock_name: str
    previous_owner: str | None
    new_owner: str
    request_id: str = ""
    action_id: str = ""
    previous_request_id: str | None = None
    previous_action_id: str | None = None
    reason: str = ""


class MotorLockManager:
    """Arbitrate exclusive access to robot resources."""

    def __init__(self, *, cancel_grace_ms: int = 300) -> None:
        """Create an empty lock manager."""
        self.cancel_grace_ms = cancel_grace_ms
        self._leases: dict[str, _LeaseRecord] = {}
        self._guard = asyncio.Lock()
        self.decisions: list[LockDecision] = []

    async def acquire(
        self,
        *,
        required_locks: Iterable[str],
        owner_id: str,
        action_id: str,
        request_id: str,
        priority: int,
        interruptible: bool,
        cancel_token: CancelToken,
        ttl_s: float | None = None,
        wait_timeout_s: float = 0.0,
    ) -> dict[str, LockLease]:
        """Acquire all required locks or raise a deterministic conflict error."""
        deadline = time.monotonic() + wait_timeout_s
        normalized = self._normalize_required(required_locks)
        while True:
            try:
                return await self._try_acquire(
                    required_locks=normalized,
                    owner_id=owner_id,
                    action_id=action_id,
                    request_id=request_id,
                    priority=priority,
                    interruptible=interruptible,
                    cancel_token=cancel_token,
                    ttl_s=ttl_s,
                )
            except LockBusyError:
                if wait_timeout_s <= 0:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockTimeoutError("Timed out waiting for action locks.")
                await asyncio.sleep(min(0.02, remaining))

    async def release_all(self, leases: dict[str, LockLease]) -> None:
        """Release all leases still owned by the action."""
        async with self._guard:
            for lock_name, lease in list(leases.items()):
                record = self._leases.get(lock_name)
                if record is None:
                    continue
                if record.lease.action_id == lease.action_id:
                    del self._leases[lock_name]

    async def cancel_owner(self, owner_id: str) -> None:
        """Cancel all leases currently held by one owner."""
        async with self._guard:
            for record in self._leases.values():
                if record.lease.owner_id == owner_id:
                    record.cancel_token.cancel()

    async def cancel_request(self, request_id: str) -> None:
        """Cancel all leases currently held for one request."""
        async with self._guard:
            for record in self._leases.values():
                if record.lease.request_id == request_id:
                    record.cancel_token.cancel()

    def active_leases(self) -> dict[str, LockLease]:
        """Return a snapshot of currently held leases."""
        return {key: record.lease for key, record in self._leases.items()}

    async def _try_acquire(
        self,
        *,
        required_locks: frozenset[str],
        owner_id: str,
        action_id: str,
        request_id: str,
        priority: int,
        interruptible: bool,
        cancel_token: CancelToken,
        ttl_s: float | None,
    ) -> dict[str, LockLease]:
        async with self._guard:
            self._drop_expired_locked()
            conflicts = self._find_conflicts(required_locks)
            blocking = [record for record in conflicts if not self._can_preempt(record, priority)]
            if blocking:
                record = blocking[0]
                reason = (
                    "uninterruptible_holding"
                    if not record.lease.interruptible
                    else "priority_not_higher"
                )
                self._record_decision(
                    LockDecision(
                        decision="rejected",
                        lock_name=record.lease.lock_name,
                        previous_owner=record.lease.owner_id,
                        new_owner=owner_id,
                        request_id=request_id,
                        action_id=action_id,
                        previous_request_id=record.lease.request_id,
                        previous_action_id=record.lease.action_id,
                        reason=reason,
                    )
                )
                raise LockBusyError(
                    f"Lock {record.lease.lock_name} is held by "
                    f"{record.lease.owner_id}."
                )

            for record in conflicts:
                record.cancel_token.cancel()
                self._record_decision(
                    LockDecision(
                        decision="preempted",
                        lock_name=record.lease.lock_name,
                        previous_owner=record.lease.owner_id,
                        new_owner=owner_id,
                        request_id=request_id,
                        action_id=action_id,
                        previous_request_id=record.lease.request_id,
                        previous_action_id=record.lease.action_id,
                    )
                )
                if self.cancel_grace_ms > 0:
                    await asyncio.sleep(self.cancel_grace_ms / 1000.0)
                self._remove_action_leases_locked(record.lease.action_id)

            acquired_at = time.monotonic()
            expires_at = acquired_at + ttl_s if ttl_s is not None else None
            leases: dict[str, LockLease] = {}
            for lock_name in required_locks:
                lease = LockLease(
                    lock_name=lock_name,
                    owner_id=owner_id,
                    action_id=action_id,
                    request_id=request_id,
                    priority=priority,
                    interruptible=interruptible,
                    acquired_at=acquired_at,
                    expires_at=expires_at,
                    renew_count=0,
                )
                self._leases[lock_name] = _LeaseRecord(
                    lease=lease,
                    cancel_token=cancel_token,
                )
                leases[lock_name] = lease
                self._record_decision(
                    LockDecision(
                        decision="granted",
                        lock_name=lock_name,
                        previous_owner=None,
                        new_owner=owner_id,
                        request_id=request_id,
                        action_id=action_id,
                    )
                )
            return leases

    def _normalize_required(self, required_locks: Iterable[str]) -> frozenset[str]:
        lock_names = frozenset(required_locks)
        unknown = lock_names - LOCK_NAMES
        if unknown:
            raise LockBusyError(f"Unknown action lock(s): {sorted(unknown)}")
        return lock_names

    def _find_conflicts(self, required_locks: frozenset[str]) -> list[_LeaseRecord]:
        records: list[_LeaseRecord] = []
        seen: set[str] = set()
        for lock_name in required_locks:
            for held_name, record in self._leases.items():
                if record.lease.action_id in seen:
                    continue
                if self._locks_conflict(lock_name, held_name):
                    records.append(record)
                    seen.add(record.lease.action_id)
        return records

    def _locks_conflict(self, requested: str, held: str) -> bool:
        if requested == held:
            return True
        if requested == "full_body" or held == "full_body":
            return True
        if requested == "head" and held in HEAD_CHILD_LOCKS:
            return True
        if held == "head" and requested in HEAD_CHILD_LOCKS:
            return True
        return False

    def _can_preempt(self, record: _LeaseRecord, new_priority: int) -> bool:
        if record.lease.interruptible is False and new_priority < 100:
            return False
        return record.lease.priority < new_priority or new_priority == 100

    def _remove_action_leases_locked(self, action_id: str) -> None:
        for lock_name, record in list(self._leases.items()):
            if record.lease.action_id == action_id:
                del self._leases[lock_name]

    def _drop_expired_locked(self) -> None:
        now = time.monotonic()
        for lock_name, record in list(self._leases.items()):
            if record.lease.expires_at is not None and record.lease.expires_at <= now:
                del self._leases[lock_name]

    def _record_decision(self, decision: LockDecision) -> None:
        self.decisions.append(decision)
        LOGGER.info(
            "motor_lock_decision",
            extra={
                "decision": decision.decision,
                "lock_name": decision.lock_name,
                "owner": decision.new_owner,
                "request_id": decision.request_id,
                "action_id": decision.action_id,
                "previous_owner": decision.previous_owner,
                "previous_request_id": decision.previous_request_id,
                "previous_action_id": decision.previous_action_id,
                "preempted_by": decision.new_owner
                if decision.decision == "preempted"
                else None,
                "new_owner": decision.new_owner,
                "reason": decision.reason,
            },
        )
