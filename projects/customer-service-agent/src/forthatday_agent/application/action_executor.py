from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

from forthatday_agent.domain.action_jobs import ActionJob
from forthatday_agent.domain.dialogue import ActionType


class ActionStore(Protocol):
    def claim_action(self, worker_id: str, now: datetime) -> ActionJob | None: ...

    def execute_time_confirmation_request(self, job: ActionJob) -> None: ...

    def reject_action(self, job: ActionJob, reason: str) -> None: ...

    def retry_action(
        self,
        job: ActionJob,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> None: ...


class ActionExecutor:
    """Executes validated model actions without giving the model direct write access."""

    def __init__(self, store: ActionStore, *, max_attempts: int = 5) -> None:
        self.store = store
        self.max_attempts = max_attempts

    def process_once(self, worker_id: str, now: datetime | None = None) -> bool:
        current_time = now or datetime.now(timezone.utc)
        job = self.store.claim_action(worker_id, current_time)
        if job is None:
            return False

        try:
            if job.action_type is ActionType.REQUEST_TIME_CONFIRMATION:
                self.store.execute_time_confirmation_request(job)
            else:
                self.store.reject_action(
                    job,
                    f"action executor not implemented: {job.action_type.value}",
                )
        except ValueError as error:
            self.store.reject_action(job, _safe_error(error))
        except Exception as error:
            self.store.retry_action(
                job,
                _safe_error(error),
                current_time + _retry_delay(job.attempt_count),
                self.max_attempts,
            )
        return True


def _retry_delay(attempt_count: int) -> timedelta:
    return timedelta(seconds=min(300, 2 ** max(0, attempt_count - 1)))


def _safe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"[:1000]
