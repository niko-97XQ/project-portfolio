from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from forthatday_agent.application.service_runtime import ServiceRuntime  # noqa: E402


class FakeStepWorker:
    def __init__(self, result: bool = False) -> None:
        self.result = result
        self.calls: list[str] = []

    def process_once(self, worker_id: str, now: datetime | None = None) -> bool:
        self.calls.append(worker_id)
        return self.result


class FakePipeline(FakeStepWorker):
    def __init__(self, process_result: bool = False, dispatch_result: bool = False) -> None:
        super().__init__(process_result)
        self.dispatch_result = dispatch_result
        self.dispatch_calls: list[str] = []
        self.recovery_calls = 0

    def dispatch_once(self, worker_id: str, now: datetime | None = None) -> bool:
        self.dispatch_calls.append(worker_id)
        return self.dispatch_result

    def recover_stale_work(self, *, stale_after=timedelta(minutes=2), now=None):
        self.recovery_calls += 1
        return (0, 0)


class ServiceRuntimeTests(unittest.TestCase):
    def test_cycle_gives_every_queue_a_turn(self) -> None:
        callback = FakeStepWorker(result=True)
        pipeline = FakePipeline()
        action = FakeStepWorker()
        runtime = ServiceRuntime(callback, pipeline, action)

        processed = runtime.process_cycle("worker")

        self.assertTrue(processed)
        self.assertEqual(callback.calls, ["worker:callback"])
        self.assertEqual(pipeline.calls, ["worker:dialogue"])
        self.assertEqual(action.calls, ["worker:action"])
        self.assertEqual(pipeline.dispatch_calls, ["worker:outbox"])

    def test_stale_recovery_runs_at_most_once_per_interval(self) -> None:
        pipeline = FakePipeline()
        runtime = ServiceRuntime(FakeStepWorker(), pipeline, FakeStepWorker())
        now = datetime.now(timezone.utc)

        runtime.process_cycle("worker", now)
        runtime.process_cycle("worker", now + timedelta(seconds=30))
        runtime.process_cycle("worker", now + timedelta(seconds=61))

        self.assertEqual(pipeline.recovery_calls, 2)

    def test_dispatch_work_counts_as_progress(self) -> None:
        pipeline = FakePipeline(dispatch_result=True)
        runtime = ServiceRuntime(FakeStepWorker(), pipeline, FakeStepWorker())

        self.assertTrue(runtime.process_cycle("worker"))


if __name__ == "__main__":
    unittest.main()
