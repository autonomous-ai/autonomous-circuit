"""The cold-agent headline score uses real packet evidence, not truthiness."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from evals.agent import run as agent_run


@pytest.mark.parametrize("value", [False, "true", "false", 1, None])
def test_agent_ready_score_requires_literal_true(
    tmp_path: Path, value: object
) -> None:
    sidecar = tmp_path / "boards" / "main.board.json"
    sidecar.parent.mkdir()
    ready, error = agent_run.verified_fab_ready(
        tmp_path, sidecar, {"fab": {"ready": value}}
    )
    assert ready is False
    assert "literal fab.ready" in str(error)


def test_agent_ready_score_requires_canonical_sidecar(tmp_path: Path) -> None:
    ready, error = agent_run.verified_fab_ready(
        tmp_path,
        tmp_path / "boards" / "alternate.board.json",
        {"fab": {"ready": True}},
    )
    assert ready is False
    assert "main.board.json" in str(error)


def test_agent_ready_score_delegates_to_complete_packet_gate(tmp_path: Path) -> None:
    sidecar = tmp_path / "boards" / "main.board.json"
    sidecar.parent.mkdir()
    with mock.patch.object(agent_run, "assert_fab_ready_board") as verify:
        assert agent_run.verified_fab_ready(
            tmp_path, sidecar, {"fab": {"ready": True}}
        ) == (True, None)
    verify.assert_called_once_with(tmp_path)


def test_agent_ready_score_preserves_packet_failure(tmp_path: Path) -> None:
    sidecar = tmp_path / "boards" / "main.board.json"
    sidecar.parent.mkdir()
    with mock.patch.object(
        agent_run,
        "assert_fab_ready_board",
        side_effect=AssertionError("selected route does not match packet"),
    ):
        ready, error = agent_run.verified_fab_ready(
            tmp_path, sidecar, {"fab": {"ready": True}}
        )
    assert ready is False
    assert error == "selected route does not match packet"
