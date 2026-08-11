"""The parallel build runner.

These tests do not build real boards — that is ``test_build_board_e2e``'s job
and it takes minutes. What matters here is the contract the composition
matrix and the eval harness lean on: every job produces an outcome, a crash
in one job does not lose the other thirty-five, results come back in the
order they were submitted, and the compute-vs-wall-clock numbers are real
rather than decorative.

Contract tests run the pool on threads: ``monkeypatch`` cannot cross a
process-spawn boundary, so a patched build in process mode would silently run
the real toolchain. The process path gets its own test at the bottom, which
needs no patching because it asserts on how a genuine failure comes back.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from circuitpy import batch as batch_mod
from circuitpy.batch import BatchReport, BuildJob, BuildOutcome, build_many


def _job(tmp_path: Path, name: str, **kw) -> BuildJob:
    return BuildJob(
        source=tmp_path / f"{name}.tsx",
        output=tmp_path / f"{name}.circuit.json",
        **kw,
    )


# ---------------------------------------------------------------- outcomes


def test_every_job_gets_an_outcome_even_when_some_crash(monkeypatch, tmp_path):
    """A matrix that dies on cell 2 teaches nothing about cells 3..n."""

    def fake_build(source, output, *, fab=None, max_build_s=None):
        if "boom" in Path(output).name:
            raise RuntimeError("router gave up")
        return {"fab": {"ready": True}}

    monkeypatch.setattr("circuitpy.generation.build_board", fake_build)

    jobs = [
        _job(tmp_path, "ok-one"),
        _job(tmp_path, "boom"),
        _job(tmp_path, "ok-two"),
    ]
    report = build_many(jobs, workers=2, use_processes=False)

    assert len(report.outcomes) == 3
    assert len(report.crashed) == 1
    assert len(report.ready) == 2
    crashed = report.crashed[0]
    assert "router gave up" in (crashed.error or "")
    assert "RuntimeError" in (crashed.error or "")


def test_built_but_not_fab_ready_is_its_own_bucket(monkeypatch, tmp_path):
    """A crash is a tooling bug; an unorderable board is a design defect.
    Collapsing them hides which one you have."""

    def fake_build(source, output, *, fab=None, max_build_s=None):
        ready = "good" in Path(output).name
        return {"fab": {"ready": ready}}

    monkeypatch.setattr("circuitpy.generation.build_board", fake_build)

    report = build_many(
        [_job(tmp_path, "good"), _job(tmp_path, "bad")],
        workers=2,
        use_processes=False,
    )

    assert len(report.crashed) == 0
    assert [o.job.resolved_label() for o in report.ready] == [
        "good.circuit.json"
    ]
    assert [o.job.resolved_label() for o in report.not_ready] == [
        "bad.circuit.json"
    ]


def test_missing_fab_block_is_not_ready(monkeypatch, tmp_path):
    """Absent evidence is not evidence of readiness."""
    monkeypatch.setattr(
        "circuitpy.generation.build_board",
        lambda source, output, **kw: {"warnings": []},
    )
    report = build_many([_job(tmp_path, "quiet")], workers=1, use_processes=False)
    assert report.outcomes[0].ok
    assert not report.outcomes[0].fab_ready


# ------------------------------------------------------------------ order


def test_outcomes_come_back_in_submission_order(monkeypatch, tmp_path):
    """Jobs finish out of order; a report that reorders them silently
    misaligns every table built from it."""

    def fake_build(source, output, *, fab=None, max_build_s=None):
        # Earlier jobs finish last.
        idx = int(Path(output).name.split("-")[1].split(".")[0])
        time.sleep(0.05 * (5 - idx))
        return {"fab": {"ready": True}}

    monkeypatch.setattr("circuitpy.generation.build_board", fake_build)

    jobs = [_job(tmp_path, f"job-{i}") for i in range(5)]
    report = build_many(jobs, workers=5, use_processes=False)

    assert [o.job.resolved_label() for o in report.outcomes] == [
        f"job-{i}.circuit.json" for i in range(5)
    ]


# ------------------------------------------------------------- concurrency


def test_jobs_actually_run_at_the_same_time(monkeypatch, tmp_path):
    """The whole point. Four one-second builds on four workers must take
    about a second, not four."""

    def fake_build(source, output, *, fab=None, max_build_s=None):
        time.sleep(0.6)
        return {"fab": {"ready": True}}

    monkeypatch.setattr("circuitpy.generation.build_board", fake_build)

    jobs = [_job(tmp_path, f"slow-{i}") for i in range(4)]
    report = build_many(jobs, workers=4, use_processes=False)

    assert report.compute_s > 2.0, "each job really did take its time"
    assert report.wall_s < 2.0, f"jobs serialised: {report.wall_s:.1f}s wall"
    assert report.speedup > 1.8


def test_speedup_is_compute_over_wall_clock(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "circuitpy.generation.build_board",
        lambda source, output, **kw: {"fab": {"ready": True}},
    )
    report = build_many([_job(tmp_path, "a")], workers=1, use_processes=False)
    assert report.speedup == pytest.approx(
        report.compute_s / report.wall_s, rel=1e-6
    )


def test_empty_batch_is_not_an_error(tmp_path):
    report = build_many([])
    assert report.outcomes == ()
    assert report.speedup == 1.0
    assert "0/0 fab-ready" in report.summary()


# -------------------------------------------------------------- collisions


def test_two_jobs_writing_one_output_is_refused(tmp_path):
    """Same output from two workers is a race that would report whichever
    finished last as both results."""
    same = tmp_path / "clash.circuit.json"
    jobs = [
        BuildJob(source=tmp_path / "a.tsx", output=same, label="a"),
        BuildJob(source=tmp_path / "b.tsx", output=same, label="b"),
    ]
    with pytest.raises(ValueError, match="same output"):
        build_many(jobs)


# ---------------------------------------------------------------- progress


def test_on_done_fires_per_job_with_running_count(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "circuitpy.generation.build_board",
        lambda source, output, **kw: {"fab": {"ready": True}},
    )
    seen: list[tuple[int, int]] = []
    build_many(
        [_job(tmp_path, f"j{i}") for i in range(3)],
        workers=2,
        use_processes=False,
        on_done=lambda outcome, done, total: seen.append((done, total)),
    )
    assert sorted(seen) == [(1, 3), (2, 3), (3, 3)]


def test_a_throwing_progress_callback_never_loses_results(monkeypatch, tmp_path):
    """Losing a 36-board matrix to a typo in a progress line would be
    a genuinely bad trade."""
    monkeypatch.setattr(
        "circuitpy.generation.build_board",
        lambda source, output, **kw: {"fab": {"ready": True}},
    )

    def bad_progress(outcome, done, total):
        raise ValueError("bad format string")

    report = build_many(
        [_job(tmp_path, f"k{i}") for i in range(3)],
        workers=2,
        use_processes=False,
        on_done=bad_progress,
    )
    assert len(report.ready) == 3


# ----------------------------------------------------------------- workers


def test_worker_count_never_exceeds_the_job_count(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "circuitpy.generation.build_board",
        lambda source, output, **kw: {"fab": {"ready": True}},
    )
    report = build_many([_job(tmp_path, "only")], workers=32, use_processes=False)
    assert report.workers == 1


def test_default_workers_leaves_a_core_for_the_session(monkeypatch):
    monkeypatch.delenv("CIRCUIT_BATCH_WORKERS", raising=False)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    assert batch_mod.default_workers() == 7


@pytest.mark.parametrize("value,expected", [("3", 3), ("0", 1), ("junk", 7)])
def test_worker_override_from_the_environment(monkeypatch, value, expected):
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    monkeypatch.setenv("CIRCUIT_BATCH_WORKERS", value)
    assert batch_mod.default_workers() == expected


# ------------------------------------------------------------------- meta


def test_caller_context_survives_the_round_trip(monkeypatch, tmp_path):
    """The composition matrix puts its block pair in ``meta`` and needs it
    back on the far side to label the failing cell."""
    monkeypatch.setattr(
        "circuitpy.generation.build_board",
        lambda source, output, **kw: {"fab": {"ready": True}},
    )
    job = _job(tmp_path, "pair", meta={"pair": ["ldo-3v3", "i2c-bus"]})
    report = build_many([job], workers=1, use_processes=False)
    assert report.outcomes[0].job.meta["pair"] == ["ldo-3v3", "i2c-bus"]


def test_summary_reports_compute_and_waiting_separately(monkeypatch, tmp_path):
    """Both numbers, because the doctrine is a claim about their ratio."""
    monkeypatch.setattr(
        "circuitpy.generation.build_board",
        lambda source, output, **kw: {"fab": {"ready": True}},
    )
    report = build_many([_job(tmp_path, f"s{i}") for i in range(2)], workers=2, use_processes=False)
    text = report.summary()
    assert "2/2 fab-ready" in text
    assert "of compute in" in text
    assert "of waiting" in text


# --------------------------------------------------------- the process path


def test_real_process_pool_returns_structured_failures(tmp_path):
    """The production path, unpatched.

    A source file that does not exist makes the real ``build_board`` fail in a
    child process. That exercises what the thread tests cannot: pickling jobs
    out, pickling outcomes back, and capturing a child-side exception as text
    instead of hanging or killing the pool.
    """
    jobs = [
        BuildJob(
            source=tmp_path / f"nope-{i}.tsx",
            output=tmp_path / f"nope-{i}.circuit.json",
            meta={"i": i},
        )
        for i in range(3)
    ]
    report = build_many(jobs, workers=3, use_processes=True)

    assert len(report.outcomes) == 3
    assert len(report.crashed) == 3, report.summary()
    for i, outcome in enumerate(report.outcomes):
        assert outcome.error, "a failing child must report why"
        assert outcome.job.meta["i"] == i
        assert not outcome.fab_ready
