"""Run many board builds at once.

The verification doctrine (``projects/circuit/north-star.md``) is
**exhaustive in compute, fast in wall-clock**: a missed defect costs a
two-week fab round trip that cannot be parallelised, while a check costs
compute that can. So the budget for verification is days of compute — but
only if that compute runs concurrently, because the two weeks we are buying
back are time-to-market, not idle waiting.

Nothing here knows what a board *is*. It takes jobs, runs
:func:`circuitpy.generation.build_board` on as many as the machine will
carry, and reports how much compute it spent against how long anyone waited.
That ratio is the number the doctrine actually cares about, so it is a
first-class field rather than something a caller derives.

Processes, not threads: a build's mirror workspace is
``.circuit/build/<stem>-<pid>/``, so separate pids already get separate work
dirs. Threads would share a pid and collide in that directory.

A failing job never takes the batch down. Every job produces an outcome —
success, an exception, or a timeout — because a composition matrix that dies
on cell 7 of 36 tells you nothing about cells 8 through 36, and those are
exactly the cells you ran the matrix to learn about.
"""

from __future__ import annotations

import os
import time
import traceback
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

__all__ = [
    "BuildJob",
    "BuildOutcome",
    "BatchReport",
    "build_many",
    "default_workers",
]


def default_workers() -> int:
    """How many builds to run at once.

    One less than the core count, so an interactive session on the same
    machine stays responsive — a batch that makes the tool unusable while it
    runs is a batch people skip. ``CIRCUIT_BATCH_WORKERS`` overrides.
    """
    override = os.environ.get("CIRCUIT_BATCH_WORKERS", "").strip()
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return max(1, (os.cpu_count() or 4) - 1)


@dataclass(frozen=True)
class BuildJob:
    """One board to build.

    ``label`` is what reports call this job. It defaults to the output stem,
    which is unique within a batch by construction — two jobs writing the same
    output would race whatever the label said.
    """

    source: Path
    output: Path
    fab: str | None = None
    max_build_s: float | None = None
    label: str = ""
    #: Free-form caller context, carried through untouched. A composition
    #: matrix puts its block pair here; an eval puts the brief id.
    meta: dict[str, object] = field(default_factory=dict)

    def resolved_label(self) -> str:
        return self.label or Path(self.output).name


@dataclass(frozen=True)
class BuildOutcome:
    """What happened to one job. Exactly one of ``result``/``error`` is set."""

    job: BuildJob
    seconds: float
    result: dict[str, object] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        """The build completed. Says nothing about whether the board is good."""
        return self.error is None

    @property
    def fab_ready(self) -> bool:
        """The board can be ordered — the only success that counts."""
        if self.result is None:
            return False
        fab = self.result.get("fab")
        return bool(isinstance(fab, dict) and fab.get("ready"))


@dataclass(frozen=True)
class BatchReport:
    outcomes: tuple[BuildOutcome, ...]
    wall_s: float
    workers: int

    @property
    def compute_s(self) -> float:
        """Total build time summed across jobs — what this would have cost
        serially."""
        return sum(o.seconds for o in self.outcomes)

    @property
    def speedup(self) -> float:
        """Compute spent per second waited. The doctrine's actual metric."""
        return self.compute_s / self.wall_s if self.wall_s > 0 else 1.0

    @property
    def built(self) -> tuple[BuildOutcome, ...]:
        return tuple(o for o in self.outcomes if o.ok)

    @property
    def crashed(self) -> tuple[BuildOutcome, ...]:
        return tuple(o for o in self.outcomes if not o.ok)

    @property
    def ready(self) -> tuple[BuildOutcome, ...]:
        return tuple(o for o in self.outcomes if o.fab_ready)

    @property
    def not_ready(self) -> tuple[BuildOutcome, ...]:
        """Built, but not orderable. The interesting bucket — a crash is a
        tooling bug, this is a design defect."""
        return tuple(o for o in self.outcomes if o.ok and not o.fab_ready)

    def summary(self) -> str:
        n = len(self.outcomes)
        return (
            f"{len(self.ready)}/{n} fab-ready "
            f"({len(self.not_ready)} built-not-ready, {len(self.crashed)} crashed) "
            f"— {self.compute_s / 60:.0f} min of compute in "
            f"{self.wall_s / 60:.0f} min of waiting, {self.speedup:.1f}x on "
            f"{self.workers} workers"
        )


def _run_one(job: BuildJob) -> BuildOutcome:
    """Worker body. Runs in a child process; must never raise."""
    # Imported here so the parent does not pay for the toolchain import when
    # it only wants the dataclasses.
    from circuitpy.generation import build_board

    started = time.monotonic()
    try:
        result = build_board(
            job.source,
            job.output,
            fab=job.fab,
            max_build_s=job.max_build_s,
        )
        return BuildOutcome(
            job=job, seconds=time.monotonic() - started, result=result
        )
    except BaseException as exc:  # noqa: BLE001 — a crashed worker must report
        return BuildOutcome(
            job=job,
            seconds=time.monotonic() - started,
            error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
        )


def build_many(
    jobs: Iterable[BuildJob],
    *,
    workers: int | None = None,
    on_done: Callable[[BuildOutcome, int, int], None] | None = None,
    use_processes: bool = True,
) -> BatchReport:
    """Build every job concurrently and report compute spent vs time waited.

    ``on_done`` is called in the parent as each job finishes, with the
    outcome, the count finished so far and the total — enough to draw a
    progress line without the caller tracking state. It runs on the collecting
    thread, so keep it cheap; an exception in it is swallowed rather than
    losing the batch's results.

    ``use_processes=False`` runs the pool on threads instead. Real builds want
    processes: the mirror workspace is keyed by pid, so threads sharing a pid
    would build into the same directory. Threads are for callers whose work is
    already subprocess-bound or stubbed, and for tests, which cannot patch
    across a spawn boundary.

    Never raises for a failing build. Check :attr:`BatchReport.crashed`.
    """
    job_list: Sequence[BuildJob] = tuple(jobs)
    if not job_list:
        return BatchReport(outcomes=(), wall_s=0.0, workers=0)

    seen: dict[Path, BuildJob] = {}
    for job in job_list:
        out = Path(job.output).expanduser().resolve()
        if out in seen:
            raise ValueError(
                f"two jobs write the same output {out} "
                f"({seen[out].resolved_label()} and {job.resolved_label()}); "
                "concurrent builds would race"
            )
        seen[out] = job

    n_workers = max(1, min(workers or default_workers(), len(job_list)))
    started = time.monotonic()
    outcomes: list[BuildOutcome] = []

    pool_cls = ProcessPoolExecutor if use_processes else ThreadPoolExecutor
    with pool_cls(max_workers=n_workers) as pool:
        futures = {pool.submit(_run_one, job): job for job in job_list}
        for future in as_completed(futures):
            job = futures[future]
            try:
                outcome = future.result()
            except BaseException as exc:  # noqa: BLE001 — pool-level death
                outcome = BuildOutcome(
                    job=job,
                    seconds=0.0,
                    error=f"worker died: {type(exc).__name__}: {exc}",
                )
            outcomes.append(outcome)
            if on_done is not None:
                try:
                    on_done(outcome, len(outcomes), len(job_list))
                except Exception:  # noqa: BLE001 — progress must not lose results
                    pass

    # Outcomes arrive in completion order and their ``job`` is a pickled copy
    # of the original, so identity comparison would not work. Output paths are
    # unique across the batch (checked above), which makes them the key.
    order = {
        Path(job.output).expanduser().resolve(): i
        for i, job in enumerate(job_list)
    }
    outcomes.sort(
        key=lambda o: order.get(Path(o.job.output).expanduser().resolve(), 0)
    )
    return BatchReport(
        outcomes=tuple(outcomes),
        wall_s=time.monotonic() - started,
        workers=n_workers,
    )
