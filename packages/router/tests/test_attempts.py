"""The attempt log — day 1 of the flywheel.

These tests exist because the data cannot be collected retroactively. A
tournament run that fails to log is a few thousand labelled examples gone, so
the logger's contract is: never raise, never lose a row that was written, and
never let two different rulers be mistaken for one measurement.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from routerlib import attempts


@dataclass(frozen=True)
class FakeRuler:
    hash: str = "ruler-abc"
    scorer_version: str = "1"


@dataclass(frozen=True)
class FakeScore:
    instance: str = "i1"
    router: str = "maze"
    completeness: float = 1.0
    errors: int = 0
    ruler: FakeRuler = field(default_factory=FakeRuler)


@dataclass
class FakeNet:
    name: str
    pads: list
    net_class: str = "signal"


@dataclass
class FakeProblem:
    nets: list
    obstacles: list = field(default_factory=list)
    width_mm: float = 100.0
    height_mm: float = 80.0
    layer_count: int = 2


def test_a_row_survives_a_round_trip(tmp_path: Path) -> None:
    log = tmp_path / "attempts.jsonl"
    assert attempts.record(
        instance="i1", router="maze", score=FakeScore(), seed=7,
        params={"grid_mm": 0.1}, path=log,
    )
    rows = list(attempts.read(log))
    assert len(rows) == 1
    assert rows[0]["router"] == "maze"
    assert rows[0]["seed"] == 7
    assert rows[0]["params"]["grid_mm"] == 0.1
    assert rows[0]["score"]["completeness"] == 1.0


def test_the_ruler_hash_is_hoisted_out_of_the_score(tmp_path: Path) -> None:
    """A reader must be able to bucket by ruler without parsing the whole row —
    mixing rulers is the one thing that silently corrupts a trained selector."""
    log = tmp_path / "a.jsonl"
    attempts.record(instance="i1", router="maze", score=FakeScore(), path=log)
    assert list(attempts.read(log))[0]["ruler_hash"] == "ruler-abc"


def test_rows_are_grouped_by_ruler_and_never_merged(tmp_path: Path) -> None:
    log = tmp_path / "a.jsonl"
    attempts.record(instance="i1", router="maze", score=FakeScore(), path=log)
    attempts.record(
        instance="i1", router="maze",
        score=FakeScore(ruler=FakeRuler(hash="ruler-xyz")), path=log,
    )
    groups = attempts.comparable(list(attempts.read(log)))
    assert set(groups) == {"ruler-abc", "ruler-xyz"}
    assert all(len(v) == 1 for v in groups.values())


def test_appending_never_loses_an_earlier_row(tmp_path: Path) -> None:
    log = tmp_path / "a.jsonl"
    for i in range(5):
        attempts.record(instance=f"i{i}", router="maze", score=FakeScore(), path=log)
    assert [r["instance"] for r in attempts.read(log)] == [f"i{i}" for i in range(5)]


def test_a_truncated_last_line_is_skipped_not_fatal(tmp_path: Path) -> None:
    """The normal state of a file being appended to while it is read."""
    log = tmp_path / "a.jsonl"
    attempts.record(instance="i1", router="maze", score=FakeScore(), path=log)
    with log.open("a", encoding="utf-8") as handle:
        handle.write('{"schema": 1, "instance": "hal')
    rows = list(attempts.read(log))
    assert len(rows) == 1 and rows[0]["instance"] == "i1"


def test_logging_never_raises_and_never_blocks_a_run(tmp_path: Path) -> None:
    """A logger that can break a router is a logger that gets switched off, and
    a switched-off logger collects nothing."""
    class Exploding:
        @property
        def ruler(self):  # pragma: no cover - the point is that it is caught
            raise RuntimeError("boom")

    assert attempts.record(
        instance="i", router="r", score=Exploding(),
        path=tmp_path / "nope" / "\0bad" / "a.jsonl",
    ) is False


def test_missing_log_reads_as_empty_not_an_error(tmp_path: Path) -> None:
    assert list(attempts.read(tmp_path / "never-written.jsonl")) == []


# ------------------------------------------------------------------ features


def test_features_describe_the_problem_not_the_solution(tmp_path: Path) -> None:
    """A selector must choose a router *before* running one, so every feature
    has to be available the moment a board is placed."""
    problem = FakeProblem(
        nets=[
            FakeNet("GND", pads=[1, 2, 3, 4]),
            FakeNet("V3_3", pads=[1, 2], net_class="power"),
            FakeNet("USB_DP", pads=[1, 2], net_class="diff-pair"),
        ]
    )
    f = attempts.features_of(problem)
    assert f["net_count"] == 3
    assert f["layer_count"] == 2
    assert f["area_mm2"] == 8000.0
    assert f["gnd_pad_fraction"] == 0.5
    assert f["has_diff_pair"] is True
    assert f["max_net_degree"] == 4


def test_features_degrade_rather_than_raise_on_an_unknown_shape() -> None:
    """Never the reason an attempt goes unlogged."""
    class Sparse:
        nets = []

    f = attempts.features_of(Sparse())
    assert f["net_count"] == 0
    assert f["area_mm2"] is None


def test_features_are_captured_from_the_problem_when_not_supplied(tmp_path: Path) -> None:
    log = tmp_path / "a.jsonl"
    attempts.record(
        instance="i1", router="maze", score=FakeScore(),
        problem=FakeProblem(nets=[FakeNet("GND", pads=[1, 2])]), path=log,
    )
    assert list(attempts.read(log))[0]["features"]["net_count"] == 1


def test_provenance_travels_with_every_row(tmp_path: Path) -> None:
    """git head and dirty flag: a score measured on a work-in-progress tree is
    normal, hiding that it was is not."""
    log = tmp_path / "a.jsonl"
    attempts.record(instance="i1", router="maze", score=FakeScore(), path=log)
    row = list(attempts.read(log))[0]
    assert "git_head" in row and "git_dirty" in row and "at" in row
    assert row["schema"] == attempts.SCHEMA_VERSION
