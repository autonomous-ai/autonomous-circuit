#!/usr/bin/env python3.12
"""Tests for scripts/part-terminals.py.

Runs under pytest, and standalone (`scripts/tests/test_part_terminals.py`) so a
machine without pytest can still prove it passes — this repo has been bitten by
treating "the checks did not run" as "the checks passed".
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import tempfile

_SPEC = importlib.util.spec_from_file_location(
    "part_terminals", pathlib.Path(__file__).resolve().parents[1] / "part-terminals.py"
)
pt = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pt)


def _board(wiring: dict[str, str], declared: list[list[str]]) -> str:
    """One C318884 with `wiring` pad->net and `declared` internal groups."""
    els: list[dict] = [{
        "type": "source_component", "source_component_id": "c0", "name": "SW1",
        "supplier_part_numbers": {"jlcpcb": ["C318884"]},
        "internally_connected_source_port_ids": [
            [f"p_{n}" for n in grp] for grp in declared
        ],
    }]
    for pin in ("pin1", "pin2", "pin3", "pin4"):
        els.append({"type": "source_port", "source_port_id": f"p_{pin}",
                    "name": pin, "source_component_id": "c0"})
    for i, net in enumerate(sorted(set(wiring.values()))):
        els.append({"type": "source_net", "source_net_id": f"n{i}", "name": net})
    net_id = {e["name"]: e["source_net_id"] for e in els if e.get("type") == "source_net"}
    for i, (pin, net) in enumerate(wiring.items()):
        els.append({"type": "source_trace", "source_trace_id": f"t{i}",
                    "connected_source_port_ids": [f"p_{pin}"],
                    "connected_source_net_ids": [net_id[net]]})
    d = tempfile.mkdtemp()
    path = str(pathlib.Path(d) / "main.circuit.json")
    pathlib.Path(path).write_text(json.dumps(els))
    return path


ROW_PAIRED = [["pin1", "pin4"], ["pin2", "pin3"]]     # what the part is
COLUMN_PAIRED = [["pin1", "pin2"], ["pin3", "pin4"]]  # the #26 defect


def test_the_old_wiring_shorts_when_graded_against_the_part():
    """wb-25's shape: pin1 -> signal, pin4 -> GND, bottom row unwired."""
    p = _board({"pin1": "BTN1", "pin4": "GND"}, COLUMN_PAIRED)
    lines, shorted, ungraded = pt.check_board(p)
    assert shorted == 1, lines
    assert ungraded == 0
    assert any("FLOATING" in ln for ln in lines), lines


def test_the_same_artifact_reads_clean_against_its_own_declaration():
    """The whole point of #26: the board agrees with a wrong model of the part.

    Same file, same bytes — only the basis of the grading changes.
    """
    p = _board({"pin1": "BTN1", "pin4": "GND"}, COLUMN_PAIRED)
    assert pt.check_board(p, declared=False)[1] == 1
    assert pt.check_board(p, declared=True)[1] == 0


def test_the_corrected_block_is_clean_on_both_bases():
    """Both pads of each terminal wired, paired by row: nothing to disagree on."""
    p = _board({"pin1": "BTN1", "pin4": "BTN1", "pin2": "GND", "pin3": "GND"},
               ROW_PAIRED)
    assert pt.check_board(p, declared=False)[1] == 0
    assert pt.check_board(p, declared=True)[1] == 0


def test_an_unknown_part_is_reported_not_silently_passed():
    """"We could not check" must never read as "we checked and it is fine"."""
    p = _board({"pin1": "A", "pin4": "B"}, ROW_PAIRED)
    els = json.loads(pathlib.Path(p).read_text())
    for e in els:
        if e.get("type") == "source_component":
            e["supplier_part_numbers"] = {"jlcpcb": ["C_NOT_ON_FILE"]}
    pathlib.Path(p).write_text(json.dumps(els))
    lines, shorted, ungraded = pt.check_board(p)
    assert shorted == 0
    assert ungraded == 1, lines
    assert any("NOT GRADED" in ln for ln in lines), lines


def test_a_part_with_no_internal_tie_needs_no_line():
    """An untied part has one terminal per pad; there is nothing here to get wrong."""
    p = _board({"pin1": "A", "pin4": "B"}, [])
    els = json.loads(pathlib.Path(p).read_text())
    for e in els:
        if e.get("type") == "source_component":
            e["supplier_part_numbers"] = {"jlcpcb": ["C_NOT_ON_FILE"]}
    pathlib.Path(p).write_text(json.dumps(els))
    lines, shorted, ungraded = pt.check_board(p)
    assert (lines, shorted, ungraded) == ([], 0, 0)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")
