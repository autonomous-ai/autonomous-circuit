"""The same board source must produce the same bytes.

These are the unit-level guards under the determinism work. The end-to-end
proof is `toolchain/determinism/run_repeat.py`, which builds a board three
times and compares the copper; that measurement is minutes long and needs a
routable board, so it does not live here. What lives here is everything that
can go quietly wrong between two of those measurements:

* the canonical order the parts-engine race is folded into,
* the byte conservatism that keeps the toolchain's own number formatting,
* the refusals that stop canonicalisation touching anything it does not own,
* and the seeded RNG preload — including the marker string the pipeline reads
  to decide whether the seed *reached* the process, which is the one fact a
  boolean flag set on the Python side cannot know.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import generation, toolchain  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
PRELOAD = REPO / "toolchain" / "determinism" / "deterministic-run.mjs"


# ---------------------------------------------------------------------------
# The canonical order
# ---------------------------------------------------------------------------


def _warning(minted: str, part: str) -> dict:
    """One element shaped like what the parts engine appends: a random id."""
    return {
        "type": "supplier_footprint_mismatch_warning",
        "supplier_footprint_mismatch_warning_id": minted,
        "message": f"footprint for {part} does not match",
        "source_component_id": part,
    }


def test_race_order_is_the_same_whatever_order_the_network_answered_in():
    """Two builds that resolve the same parts in different orders converge."""
    a = [
        _warning("supplier_footprint_mismatch_warning_aB3dE5gH7j", "C2"),
        _warning("supplier_footprint_mismatch_warning_Zy9Xw8Vu7T", "R1"),
        _warning("supplier_footprint_mismatch_warning_kL4mN6pQ8r", "U3"),
    ]
    b = [
        _warning("supplier_footprint_mismatch_warning_kL4mN6pQ8r", "R1"),
        _warning("supplier_footprint_mismatch_warning_aB3dE5gH7j", "U3"),
        _warning("supplier_footprint_mismatch_warning_Zy9Xw8Vu7T", "C2"),
    ]
    assert generation._canonicalise_race_order(a)
    assert generation._canonicalise_race_order(b)
    assert a == b
    # And the ids are the same three ids — canonicalisation permutes, it never
    # mints. (The seeded RNG is what makes the id *strings* reproducible; this
    # function only decides who gets which.)
    assert sorted(
        e["supplier_footprint_mismatch_warning_id"] for e in a
    ) == sorted(e["supplier_footprint_mismatch_warning_id"] for e in b)


def test_canonicalising_twice_is_canonicalising_once():
    """Idempotent, so a second pass anywhere in the pipeline is free."""
    elements = [
        _warning("supplier_footprint_mismatch_warning_aB3dE5gH7j", "C2"),
        _warning("supplier_footprint_mismatch_warning_Zy9Xw8Vu7T", "R1"),
    ]
    generation._canonicalise_race_order(elements)
    once = json.dumps(elements, sort_keys=True)
    generation._canonicalise_race_order(elements)
    assert json.dumps(elements, sort_keys=True) == once


def test_counter_ids_are_never_reordered():
    """A type the toolchain numbered itself is a type it already ordered."""
    elements = [
        {"type": "pcb_via", "pcb_via_id": "pcb_via_1", "x": 2},
        {"type": "pcb_via", "pcb_via_id": "pcb_via_0", "x": 1},
    ]
    frozen = json.dumps(elements, sort_keys=True)
    assert generation._canonicalise_race_order(elements) == {}
    assert json.dumps(elements, sort_keys=True) == frozen


def test_a_referenced_id_makes_the_group_off_limits():
    """Reseating an id that something points at would break the reference."""
    elements = [
        _warning("supplier_footprint_mismatch_warning_aB3dE5gH7j", "C2"),
        _warning("supplier_footprint_mismatch_warning_Zy9Xw8Vu7T", "R1"),
        {"type": "note", "refers_to": "supplier_footprint_mismatch_warning_Zy9Xw8Vu7T"},
    ]
    frozen = json.dumps(elements, sort_keys=True)
    assert generation._canonicalise_race_order(elements) == {}
    assert json.dumps(elements, sort_keys=True) == frozen


def test_a_mixed_group_is_skipped_whole():
    """One counter id among minted ones means the toolchain owns the order."""
    elements = [
        _warning("supplier_footprint_mismatch_warning_aB3dE5gH7j", "C2"),
        {
            "type": "supplier_footprint_mismatch_warning",
            "supplier_footprint_mismatch_warning_id": "supplier_footprint_mismatch_warning_0",
            "message": "footprint for R1 does not match",
            "source_component_id": "R1",
        },
    ]
    frozen = json.dumps(elements, sort_keys=True)
    assert generation._canonicalise_race_order(elements) == {}
    assert json.dumps(elements, sort_keys=True) == frozen


# ---------------------------------------------------------------------------
# Byte conservatism
# ---------------------------------------------------------------------------


def test_canonicalise_file_keeps_the_toolchains_own_number_formatting(tmp_path: Path):
    """A Python round-trip would rewrite `1e-7` to `1e-07` on every board.

    tscircuit writes JSON with `JSON.stringify`, whose float formatting differs
    from `json.dumps`. If canonicalisation re-serialised the document it would
    change numbers on boards it had no business touching, and every downstream
    byte comparison would blame the wrong thing.
    """
    path = tmp_path / "circuit.json"
    path.write_text(
        "[\n"
        '{"type":"pcb_trace","pcb_trace_id":"pcb_trace_0","width":1e-7,'
        '"y":0.00001,"route":[{"x":1.5,"y":-2.25}]},\n'
        '{"type":"supplier_footprint_mismatch_warning",'
        '"supplier_footprint_mismatch_warning_id":'
        '"supplier_footprint_mismatch_warning_zzzzzzzzzz","message":"b"},\n'
        '{"type":"supplier_footprint_mismatch_warning",'
        '"supplier_footprint_mismatch_warning_id":'
        '"supplier_footprint_mismatch_warning_aaaaaaaaaa","message":"a"}\n'
        "]\n",
        encoding="utf-8",
    )
    moved = generation._canonicalise_file(path)
    assert moved == {"supplier_footprint_mismatch_warning": 2}
    text = path.read_text(encoding="utf-8")
    assert "1e-7" in text and "1e-07" not in text
    assert "0.00001" in text and "1e-05" not in text
    # The warnings swapped: "a" now carries the lexically first id.
    elements = json.loads(text)
    assert [e.get("message") for e in elements[1:]] == ["a", "b"]
    assert (
        elements[1]["supplier_footprint_mismatch_warning_id"]
        == "supplier_footprint_mismatch_warning_aaaaaaaaaa"
    )


def test_canonicalise_file_writes_nothing_when_there_is_nothing_to_move(
    tmp_path: Path,
):
    path = tmp_path / "circuit.json"
    body = '[\n{"type":"pcb_via","pcb_via_id":"pcb_via_0","x":1e-7}\n]\n'
    path.write_text(body, encoding="utf-8")
    stamp = path.stat().st_mtime_ns
    assert generation._canonicalise_file(path) == {}
    assert path.read_text(encoding="utf-8") == body
    assert path.stat().st_mtime_ns == stamp


def test_canonicalise_file_survives_a_file_it_cannot_parse(tmp_path: Path):
    path = tmp_path / "circuit.json"
    path.write_text("not json", encoding="utf-8")
    assert generation._canonicalise_file(path) == {}
    assert path.read_text(encoding="utf-8") == "not json"


# ---------------------------------------------------------------------------
# The packet, not just the route
# ---------------------------------------------------------------------------


def test_a_zip_of_the_same_content_is_the_same_bytes(tmp_path: Path):
    """`ZipFile.write` stamps mtime, so identical geometry zipped an hour
    apart is a different file — and a packet nobody can compare is a packet
    nobody can cache or golden-file."""
    first = tmp_path / "a"
    second = tmp_path / "b"
    for root, stamp in ((first, 1_600_000_000), (second, 1_700_000_000)):
        root.mkdir()
        (root / "board.kicad_pcb").write_text("(kicad_pcb)\n", encoding="utf-8")
        (root / "board.kicad_sch").write_text("(kicad_sch)\n", encoding="utf-8")
        for name in ("board.kicad_pcb", "board.kicad_sch"):
            os.utime(root / name, (stamp, stamp))

    out = []
    for root in (first, second):
        dest = root / "bundle.zip"
        # Members listed in different orders on purpose.
        members = [
            (root / "board.kicad_pcb", "board.kicad_pcb"),
            (root / "board.kicad_sch", "board.kicad_sch"),
        ]
        generation._zip_deterministic(dest, members if root is first else members[::-1])
        out.append(dest.read_bytes())
    assert out[0] == out[1]


# ---------------------------------------------------------------------------
# The seeded preload
# ---------------------------------------------------------------------------


def test_the_marker_python_greps_for_is_the_marker_the_preload_writes():
    """If these drift, `determinism.seeded` is false forever and silently."""
    source = PRELOAD.read_text(encoding="utf-8")
    assert (
        f'export const DETERMINISM_MARKER = "{generation.DETERMINISM_MARKER}"'
        in source
    )


def _node_random(seed: str | None, count: int = 6) -> list[float]:
    env = dict(os.environ)
    env["NODE_OPTIONS"] = f"--import {PRELOAD.as_uri()}"
    if seed is None:
        env.pop("CIRCUIT_DETERMINISTIC_SEED", None)
    else:
        env["CIRCUIT_DETERMINISTIC_SEED"] = seed
    proc = subprocess.run(
        [
            toolchain.node_exe(),
            "-e",
            f"console.log(JSON.stringify(Array.from({{length:{count}}},Math.random)))",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])


def test_the_same_seed_gives_the_same_stream_in_a_real_node_process():
    assert _node_random("board-fingerprint-abc") == _node_random(
        "board-fingerprint-abc"
    )


def test_a_different_seed_gives_a_different_stream():
    assert _node_random("board-a") != _node_random("board-b")


def test_the_preload_does_nothing_without_a_seed():
    """Importing it is never a behaviour change on its own."""
    first = _node_random(None)
    second = _node_random(None)
    assert first != second  # unseeded Math.random is still random
    assert all(0.0 <= v < 1.0 for v in first)


def test_seeding_announces_itself_on_stderr():
    env = dict(os.environ)
    env["NODE_OPTIONS"] = f"--import {PRELOAD.as_uri()}"
    env["CIRCUIT_DETERMINISTIC_SEED"] = "abcdef0123456789"
    proc = subprocess.run(
        [toolchain.node_exe(), "-e", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=60,
    )
    assert generation.DETERMINISM_MARKER in proc.stderr.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# The environment seam
# ---------------------------------------------------------------------------


def test_deterministic_env_appends_to_an_existing_node_options(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=8192")
    with generation._deterministic_env("seed-1") as active:
        assert active is True
        assert os.environ["NODE_OPTIONS"].startswith("--max-old-space-size=8192 ")
        assert "deterministic-run.mjs" in os.environ["NODE_OPTIONS"]
        assert os.environ[generation.SEED_ENV] == "seed-1"
    assert os.environ["NODE_OPTIONS"] == "--max-old-space-size=8192"
    assert generation.SEED_ENV not in os.environ


def test_deterministic_env_restores_an_absent_node_options(monkeypatch):
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    with generation._deterministic_env("seed-2") as active:
        assert active is True
    assert "NODE_OPTIONS" not in os.environ


def test_determinism_can_be_switched_off(monkeypatch):
    monkeypatch.setenv(generation.DETERMINISM_ENV, "off")
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    with generation._deterministic_env("seed-3") as active:
        assert active is False
        assert "NODE_OPTIONS" not in os.environ


# ---------------------------------------------------------------------------
# The wall clock is a safety valve, not a budget
# ---------------------------------------------------------------------------


def test_build_timeouts_are_sized_for_a_loaded_machine():
    """A timeout a busy machine blows through decides the board by luck.

    Measured on this hardware: the same board takes 5.2x longer under load, so
    a 600s budget killed builds that a quiet machine finished in 100s, and a
    1500s escalation budget killed the `5x` retry on the largest board — after
    which the cheaper default-effort route shipped. Both numbers are sized for
    the worst case now; a fab round trip is two weeks, a long build is minutes.
    """
    assert generation.DEFAULT_BUILD_TIMEOUT_S >= 5400.0
    assert generation.ROUTING_ESCALATION_TIMEOUT_S >= 5400.0


@pytest.mark.skipif(
    not (REPO / "toolchain" / "node_modules" / "@tscircuit" / "cli").is_dir(),
    reason="pinned toolchain not installed",
)
def test_the_router_search_is_bounded_by_iterations_not_by_the_clock():
    """The diagnosis, pinned so a toolchain bump cannot quietly reverse it.

    `tscircuit-cli` runs one self-contained bundle; the standalone
    `@tscircuit/capacity-autorouter` package is never loaded. Inside the bundle
    every solver loop is `while (!solved && !failed) step()` against
    MAX_ITERATIONS, and every clock read around it is telemetry. The one
    genuine wall-clock budget in the whole bundle belongs to PackSolver2 and is
    read from `platform.pcbPackSolverTimeoutMs`, which nothing sets — with it
    undefined the wrapper degenerates to a plain `solve()`.

    If a bump introduces a real time budget, this test fails and the fix is to
    pin the budget to an iteration count, not to widen the test.
    """
    bundle = (
        REPO / "toolchain" / "node_modules" / "@tscircuit" / "cli" / "dist" / "cli" / "main.js"
    )
    text = bundle.read_text(encoding="utf-8", errors="replace")
    # No part of the router is loaded from node_modules.
    assert 'require("@tscircuit/' not in text
    # Every solve loop is iteration-bounded.
    assert "for(;!this.solved&&!this.failed;)this.step()" in text
    # The only time-budgeted solver in the bundle, and the only place its
    # budget can come from.
    assert text.count("pcbPackSolverTimeoutMs") == 1
