from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import threading

@dataclass
class ToolJob:
    index: int
    name: str
    call_id: str
    arguments: dict
    concurrency_safe: bool


@dataclass(frozen=True)
class ToolOutcome:
    index: int
    call_id: str
    content: str

class ToolScheduler:
    def __init__(self, max_workers: int = 4) -> None:
        self.max_workers = max(1, max_workers)

    def execute(
        self,
        jobs: list[ToolJob],
        execute_one: Callable[[ToolJob], str],
        cancelled: threading.Event,
    ) -> list[ToolOutcome]:
        outcomes: dict[int, ToolOutcome] = {}
        safe_batch: list[ToolJob] = []

        def run_job(job: ToolJob) -> ToolOutcome:
            if cancelled.is_set():
                return ToolOutcome(
                    index=job.index,
                    call_id=job.call_id,
                    content="Cancelled before tool execution.",
                )
            try:
                content = execute_one(job)
            except Exception as exc:
                content = f"Error: {type(exc).__name__}: {exc}"
            return ToolOutcome(
                index=job.index,
                call_id=job.call_id,
                content=content,
            )

        def flush_safe_batch() -> None:
            if not safe_batch:
                return

            if len(safe_batch) == 1:
                outcome = run_job(safe_batch[0])
                outcomes[outcome.index] = outcome
                safe_batch.clear()
                return

            workers = min(self.max_workers, len(safe_batch))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(run_job, job): job
                    for job in safe_batch
                }
                for future in as_completed(futures):
                    outcome = future.result()
                    outcomes[outcome.index] = outcome
            safe_batch.clear()

        for job in jobs:
            if job.concurrency_safe:
                safe_batch.append(job)
                continue

            flush_safe_batch()
            outcome = run_job(job)
            outcomes[outcome.index] = outcome

        flush_safe_batch()
        return [outcomes[index] for index in sorted(outcomes)]
