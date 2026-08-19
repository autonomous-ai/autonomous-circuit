"""The single entry point: every check, one JSON line, never a crash."""

from __future__ import annotations

import json
import zipfile

import fixtures
import pytest

from verifylib import cli


@pytest.fixture()
def project(tmp_path):
    boards = tmp_path / "boards"
    boards.mkdir()
    elements = fixtures.clean_board()
    elements.append(fixtures.net(0, "V3_3", is_power=True))
    elements.append(fixtures.net(1, "GND", is_ground=True))
    (boards / "main.circuit.json").write_text(json.dumps(elements), encoding="utf-8")
    return tmp_path


def test_a_project_directory_resolves_to_its_board(project):
    circuit_json, gerbers = cli.resolve(project)
    assert circuit_json.name == "main.circuit.json"
    assert gerbers is None


def test_a_fab_packet_beside_the_board_is_found(project):
    fab = project / "boards" / "main_fab"
    fab.mkdir()
    with zipfile.ZipFile(fab / "gerbers.zip", "w") as archive:
        archive.writestr("board-F_Cu.gtl", "%FSLAX46Y46*%\n%MOMM*%\nM02*\n")
    _, gerbers = cli.resolve(project)
    assert gerbers is not None and gerbers.name == "gerbers.zip"


def test_every_check_runs_and_reports_its_own_time(project):
    report = cli.verify(project, trials=0, parallel=False)
    names = {c["name"].split("[")[0] for c in report["checks"]}
    assert names == {
        "assembly", "netclass", "dc", "corners", "review", "thermal", "crystal",
        "pour", "gerber_truth",
    }
    assert all("seconds" in c for c in report["checks"])


def test_a_missing_packet_is_said_out_loud_not_skipped(project):
    """No gerbers means the packet was not verified. That has to appear in the
    output, or an absent check reads as a passing one."""
    report = cli.verify(project, trials=0, parallel=False, only=["gerber"])
    kinds = {f["kind"] for f in report["findings"]}
    assert "gerber_absent" in kinds


def test_the_summary_counts_match_the_findings(project):
    report = cli.verify(project, trials=0, parallel=False)
    assert report["summary"]["error"] == sum(
        1 for f in report["findings"] if f["severity"] == "error"
    )
    assert report["ok"] == (report["summary"]["error"] == 0)


def test_a_broken_board_yields_a_finding_rather_than_an_exception(tmp_path):
    boards = tmp_path / "boards"
    boards.mkdir()
    (boards / "main.circuit.json").write_text('[{"type": 5}]', encoding="utf-8")
    report = cli.verify(tmp_path, trials=0, parallel=False)
    assert isinstance(report["findings"], list)


def test_a_target_with_no_board_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        cli.resolve(tmp_path)


def test_the_json_line_is_a_single_parseable_line(project, capsys):
    assert cli.main([str(project), "--trials", "0", "--serial"]) == 0
    out = capsys.readouterr().out.strip()
    assert "\n" not in out
    assert json.loads(out)["ok"] in (True, False)


def test_parallel_and_serial_agree(project):
    serial = cli.verify(project, trials=0, parallel=False)
    parallel = cli.verify(project, trials=0, parallel=True)
    assert [f["kind"] for f in serial["findings"]] == [
        f["kind"] for f in parallel["findings"]
    ]
