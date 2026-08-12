"""Outcome-level readiness gate for the structural board evals."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from evals import run as eval_run


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    boards = project / "boards"
    boards.mkdir(parents=True)
    (project / "product.json").write_text("{}")
    (boards / "main.circuit.json").write_text("[]")
    for relative in eval_run.READY_ARTIFACTS.values():
        artifact = boards / relative
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("evidence")
    sidecar = {
        "fab": {"ready": True, "profile": "jlcpcb"},
        "validation": {"warnings": []},
        "artifacts": dict(eval_run.READY_ARTIFACTS),
        "build": {"sentinel": "delegated to retained-evidence verifier"},
    }
    (boards / "main.board.json").write_text(json.dumps(sidecar))
    return project


def _sidecar(project: Path) -> dict:
    return json.loads((project / "boards" / "main.board.json").read_text())


def _write_sidecar(project: Path, payload: dict) -> None:
    (project / "boards" / "main.board.json").write_text(json.dumps(payload))


def test_ready_gate_binds_literal_verdict_packet_and_selected_route(tmp_path: Path) -> None:
    project = _project(tmp_path)
    product = object()
    profile = object()
    with (
        mock.patch.object(eval_run, "load_product", return_value=product),
        mock.patch.object(eval_run, "get_profile", return_value=profile),
        mock.patch.object(
            eval_run, "routing_attempt_evidence_error", return_value=None
        ) as verify,
    ):
        result = eval_run.assert_fab_ready_board(project)

    assert result["fab"]["ready"] is True
    verify.assert_called_once_with(
        result["build"],
        circuit_json_path=project / "boards" / "main.circuit.json",
        final_warnings=[],
        fab_ready=True,
        product=product,
        profile=profile,
    )


@pytest.mark.parametrize("ready", [False, "true", 1, None])
def test_ready_gate_requires_literal_true(tmp_path: Path, ready: object) -> None:
    project = _project(tmp_path)
    payload = _sidecar(project)
    payload["fab"]["ready"] = ready
    _write_sidecar(project, payload)
    with pytest.raises(AssertionError, match="literal fab.ready"):
        eval_run.assert_fab_ready_board(project)


def test_ready_gate_rejects_blocker_and_incomplete_packet(tmp_path: Path) -> None:
    project = _project(tmp_path)
    payload = _sidecar(project)
    payload["validation"] = {
        "warnings": [{"severity": "error", "kind": "pcb_trace_error"}]
    }
    _write_sidecar(project, payload)
    with pytest.raises(AssertionError, match="blocking warnings"):
        eval_run.assert_fab_ready_board(project)

    payload["validation"] = {"warnings": []}
    payload["artifacts"].pop("cpl")
    _write_sidecar(project, payload)
    with pytest.raises(AssertionError, match="artifact cpl"):
        eval_run.assert_fab_ready_board(project)


def test_ready_gate_rejects_unbound_selected_route(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with (
        mock.patch.object(eval_run, "load_product", return_value=object()),
        mock.patch.object(eval_run, "get_profile", return_value=object()),
        mock.patch.object(
            eval_run,
            "routing_attempt_evidence_error",
            return_value="selected circuit hash drifted",
        ),
        pytest.raises(AssertionError, match="selected circuit hash drifted"),
    ):
        eval_run.assert_fab_ready_board(project)
