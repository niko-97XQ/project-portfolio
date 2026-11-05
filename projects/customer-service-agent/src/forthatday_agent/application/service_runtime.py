from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class SingleStepWorker(Protocol):
    def process_once(self, worker_id: str, now: datetime | None = None) -> bool: ...


class MessagePipelineWorker(SingleStepWorker, Protocol):
    def dispatch_once(self, worker_id: str, now: datetime | None = None) -> bool: ...

    def recover_stale_work(
        self,
        *,
        stale_after: timedelta = timedelta(minutes=2),
        now: datetime | None = None,
    ) -> tuple[int, int]: ...


class ServiceRuntime:
    """Runs one fair cycle across callback, dialogue, action, and delivery queues."""

    def __init__(
        self,
        callback_worker: SingleStepWorker,
        message_pipeline: MessagePipelineWorker,
        action_worker: SingleStepWorker,
        *,
        recovery_interval: timedelta = timedelta(minutes=1),
    ) -> None:
        self.callback_worker = callback_worker
        self.message_pipeline = message_pipeline
        self.action_worker = action_worker
        self.recovery_interval = recovery_interval
        self._last_recovery_at: datetime | None = None

    def process_cycle(self, worker_id: str, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        processed = False
        processed |= self.callback_worker.process_once(f"{worker_id}:callback", current_time)
        processed |= self.message_pipeline.process_once(f"{worker_id}:dialogue", current_time)
        processed |= self.action_worker.process_once(f"{worker_id}:action", current_time)
        processed |= self.message_pipeline.dispatch_once(f"{worker_id}:outbox", current_time)
        if (
            self._last_recovery_at is None
            or current_time - self._last_recovery_at >= self.recovery_interval
        ):
            self.message_pipeline.recover_stale_work(now=current_time)
            self._last_recovery_at = current_time
        return processed
