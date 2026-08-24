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


def _wired(lcsc: str, pins, nets: dict[str, str], links=(), declared=()) -> str:
    """One `lcsc` part whose `pins` go to named `nets`, plus point-to-point `links`.

    `links` are (pin, "Comp.PIN") pairs written as a trace carrying ports and
    **no** net id — the shape `tscircuit` emits for a two-terminal hop, and the
    shape that used to read as an unconnected pad.
    """
    els: list[dict] = [{
        "type": "source_component", "source_component_id": "c0", "name": "Y1",
        "supplier_part_numbers": {"jlcpcb": [lcsc]},
        "internally_connected_source_port_ids": [
            [f"p_{n}" for n in grp] for grp in declared
        ],
    }]
    for pin in pins:
        els.append({"type": "source_port", "source_port_id": f"p_{pin}",
                    "name": pin, "source_component_id": "c0"})
    for i, net in enumerate(sorted(set(nets.values()))):
        els.append({"type": "source_net", "source_net_id": f"n{i}", "name": net})
    net_id = {e["name"]: e["source_net_id"] for e in els if e.get("type") == "source_net"}
    t = 0
    for pin, net in nets.items():
        els.append({"type": "source_trace", "source_trace_id": f"t{t}",
                    "connected_source_port_ids": [f"p_{pin}"],
                    "connected_source_net_ids": [net_id[net]]})
        t += 1
    for pin, peer in links:
        comp, peer_pin = peer.split(".")
        cid = f"c_{comp}"
        if not any(e.get("source_component_id") == cid for e in els):
            els.append({"type": "source_component", "source_component_id": cid,
                        "name": comp, "supplier_part_numbers": {}})
        els.append({"type": "source_port", "source_port_id": f"p_{comp}_{peer_pin}",
                    "name": peer_pin, "source_component_id": cid})
        els.append({"type": "source_trace", "source_trace_id": f"t{t}",
                    "connected_source_port_ids": [f"p_{pin}", f"p_{comp}_{peer_pin}"],
                    "connected_source_net_ids": []})
        t += 1
    d = tempfile.mkdtemp()
    path = str(pathlib.Path(d) / "main.circuit.json")
    pathlib.Path(path).write_text(json.dumps(els))
    return path


CRYSTAL = "C20625731"
CRYSTAL_PINS = ("pin1", "pin2", "pin3", "pin4")


def test_a_pad_on_an_unnamed_net_is_not_reported_as_floating():
    """`Y1.pin1 -> U3.XIN` carries ports and no net id. It is still soldered.

    Reporting it as FLOATING is how a wired pad reads as an unwired one — the
    crystal is wired exactly this way on all 31 boards in the corpus.
    """
    p = _wired(CRYSTAL, CRYSTAL_PINS, {"pin2": "GND", "pin4": "GND"},
               links=[("pin1", "U3.XIN"), ("pin3", "U3.XOUT")])
    lines, shorted, ungraded = pt.check_board(p)
    assert (shorted, ungraded) == (0, 0), lines
    assert not any("FLOATING" in ln for ln in lines), lines
    assert any("U3.XIN" in ln for ln in lines), lines


def test_a_pad_reaching_no_trace_at_all_is_still_floating():
    """The distinction the label exists to make: unnamed is not unconnected."""
    p = _wired(CRYSTAL, CRYSTAL_PINS, {"pin2": "GND", "pin4": "GND"})
    lines, shorted, ungraded = pt.check_board(p)
    assert (shorted, ungraded) == (0, 0), lines
    assert sum("FLOATING" in ln for ln in lines) == 2, lines


def test_a_short_visible_only_through_an_unnamed_net_is_counted():
    """The +60 this change found: signal on an unnamed net, ground named.

    SW2/SW3's shape on 15 boards — one pad of each terminal on the button net
    and the other on GND, with the button net never given a name.
    """
    p = _wired("C318884", ("pin1", "pin2", "pin3", "pin4"),
               {"pin3": "GND", "pin4": "GND"},
               links=[("pin1", "R13.pin2"), ("pin2", "R13.pin2")])
    lines, shorted, _ = pt.check_board(p)
    assert shorted == 2, lines


def test_the_crystal_grounds_both_case_pads_or_it_is_a_short():
    """What grouping pin2+pin4 buys: it argues with a board that splits them."""
    ok = _wired(CRYSTAL, CRYSTAL_PINS, {"pin2": "GND", "pin4": "GND"},
                links=[("pin1", "U3.XIN"), ("pin3", "U3.XOUT")])
    assert pt.check_board(ok)[1] == 0
    split = _wired(CRYSTAL, CRYSTAL_PINS, {"pin2": "GND", "pin4": "AGND"},
                   links=[("pin1", "U3.XIN"), ("pin3", "U3.XOUT")])
    assert pt.check_board(split)[1] == 1


def test_the_crystal_terminals_are_graded_one_pad_each():
    """pin1 and pin3 are separate terminals; joining them shorts the oscillator."""
    p = _wired(CRYSTAL, CRYSTAL_PINS, {"pin1": "XIN", "pin3": "XIN",
                                       "pin2": "GND", "pin4": "GND"})
    lines, shorted, ungraded = pt.check_board(p)
    assert ungraded == 0, lines
    # One net on each of pin1 and pin3 — the part cannot see that they are the
    # same net, and should not: that is the router's business, not the part's.
    assert shorted == 0, lines
    assert sum(1 for ln in lines if ln.split()[1] in ("pin1", "pin3")) == 2, lines


def _pads(lcsc: str, pads, nets: dict[str, str]) -> str:
    """One `lcsc` part whose pads carry explicit (name, hints), wired to `nets`.

    `nets` is keyed by pad *name*. Used to prove the table resolves a pad the
    datasheet calls "pin 4" on a part whose block calls it "VOUT2".
    """
    els: list[dict] = [{
        "type": "source_component", "source_component_id": "c0", "name": "U2",
        "supplier_part_numbers": {"jlcpcb": [lcsc]},
        "internally_connected_source_port_ids": [],
    }]
    for name, hints in pads:
        els.append({"type": "source_port", "source_port_id": f"p_{name}",
                    "name": name, "port_hints": list(hints),
                    "source_component_id": "c0"})
    for i, net in enumerate(sorted(set(nets.values()))):
        els.append({"type": "source_net", "source_net_id": f"n{i}", "name": net})
    net_id = {e["name"]: e["source_net_id"] for e in els if e.get("type") == "source_net"}
    for i, (name, net) in enumerate(nets.items()):
        els.append({"type": "source_trace", "source_trace_id": f"t{i}",
                    "connected_source_port_ids": [f"p_{name}"],
                    "connected_source_net_ids": [net_id[net]]})
    d = tempfile.mkdtemp()
    path = str(pathlib.Path(d) / "main.circuit.json")
    pathlib.Path(path).write_text(json.dumps(els))
    return path


#: How weather-badge-27's block names the AMS1117's four lands. The datasheet
#: names them 1..4; the block does not, which is the whole point.
AMS1117_PADS = (
    ("GND", ("GND", "pin1", "1")),
    ("VOUT1", ("VOUT1", "VOUT", "pin2", "2")),
    ("VIN", ("VIN", "pin3", "3")),
    ("VOUT2", ("VOUT2", "TAB", "pin4", "4")),
)


def test_a_table_entry_resolves_by_pin_number_not_by_the_blocks_names():
    """The entry is written `pin2`/`pin4`; the block calls those `VOUT1`/`VOUT2`.

    Keying on names alone would match nothing and report every terminal as
    floating — a check that silently grades air. The pad's name is the block's
    account of the part, and the block is what is under test.
    """
    p = _pads("C6186", AMS1117_PADS,
              {"GND": "GND", "VOUT1": "V3_3", "VIN": "V5", "VOUT2": "V3_3"})
    lines, shorted, ungraded = pt.check_board(p)
    assert (shorted, ungraded) == (0, 0), lines
    assert not any("FLOATING" in ln for ln in lines), lines
    assert len(lines) == 3, lines          # three terminals across four lands


def test_grounding_the_regulator_tab_is_a_short():
    """The defect the entry exists for: the tab is VOUT, not a heatsink pad.

    Tying it to GND for thermals shorts the 3.3V rail through the regulator.
    """
    p = _pads("C6186", AMS1117_PADS,
              {"GND": "GND", "VOUT1": "V3_3", "VIN": "V5", "VOUT2": "GND"})
    lines, shorted, _ = pt.check_board(p)
    assert shorted == 1, lines
    assert any("V3_3" in ln and "GND" in ln and "SHORTED" in ln for ln in lines), lines


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
            passed += 1
    print(f"\n{passed} passed")
