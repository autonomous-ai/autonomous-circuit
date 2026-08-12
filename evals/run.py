#!/usr/bin/env python3
"""Autonomous Circuit structural evals.

Unit tests assert code behaviour. These assert **maker-visible outcomes**: a
well-formed board produces a complete, orderable packet; the verifiers
actually fire on bad boards; the safety envelope actually refuses; the cache
actually makes iteration cheap.

The distinction that matters: a test can pass while the product is broken.
An eval fails when the *thing a person receives* is wrong.

Usage:  python evals/run.py [case-name ...]     (default: all)
Exit:   non-zero if any case fails. One scorecard line per case.
Offline: CIRCUIT_PARTS_ENGINE=off is forced — no network, no surprises.

Extending: add a Case to CASES. Keep every assertion about an outcome
(artifact exists, warning fired, packet is/is not orderable), never about an
internal call.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "circuitpy" / "src"))
sys.path.insert(0, str(REPO / "skills" / "circuitcode"))  # circuitlib

os.environ["CIRCUIT_PARTS_ENGINE"] = "off"

from circuitlib import golden as circuit_golden  # noqa: E402
from circuitlib import safety  # noqa: E402
from circuitpy.errors import BuildError  # noqa: E402
from circuitpy.fab import get_profile  # noqa: E402
from circuitpy.generation import (  # noqa: E402
    build_board,
    routing_attempt_evidence_error,
)
from circuitpy.spec import load_product  # noqa: E402
from scripts.sync_golden_blocks import sync_project  # noqa: E402

SKELETON = REPO / "skills" / "circuitcode" / "templates" / "project_skeleton"
BLOCKS = REPO / "packages" / "golden-blocks" / "blocks"

BOARD_PROPS = (
    'thickness={1.6} minTraceWidth="0.2mm" '
    'minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm"'
)


# ---------------------------------------------------------------------------
# Project scaffolding
# ---------------------------------------------------------------------------


def build_project(tmp: Path, tsx: str, *, product: dict | None = None) -> Path:
    """A real project on disk: skeleton config + the frozen block library."""
    proj = tmp / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    for name in ("tsconfig.json", "tscircuit.config.json"):
        shutil.copy(SKELETON / name, proj / name)
    selected = sorted(
        block_id
        for block_id in (entry.name for entry in BLOCKS.iterdir() if entry.is_dir())
        if f'../blocks/{block_id}/{block_id}' in tsx
    )
    if selected:
        sync_project(
            proj,
            blocks=selected,
            source=BLOCKS,
            source_label="packages/golden-blocks/blocks",
        )
    payload = {
        "name": "eval-board",
        "description": "structural eval board",
        "power": "usb-c-5v",
        "envelopeMm": [80, 60],
        "layers": 2,
        "fab": "jlcpcb",
        "assembly": True,
    }
    payload.update(product or {})
    (proj / "product.json").write_text(json.dumps(payload, indent=2))
    boards = proj / "boards"
    boards.mkdir(exist_ok=True)
    (boards / "main.tsx").write_text(tsx)
    return proj


def run_board(proj: Path) -> dict:
    return build_board(
        proj / "boards" / "main.tsx", proj / "boards" / "main.circuit.json"
    )


def sidecar(proj: Path) -> dict:
    return json.loads((proj / "boards" / "main.board.json").read_text())


def warning_kinds(proj: Path) -> list[str]:
    return [
        w["kind"] for w in sidecar(proj).get("validation", {}).get("warnings", [])
    ]


def blocking(proj: Path) -> list[dict]:
    return [
        w for w in sidecar(proj).get("validation", {}).get("warnings", [])
        if w.get("severity") == "error"
    ]


READY_ARTIFACTS = {
    "gerbers": "main_fab/gerbers.zip",
    "bom": "main_fab/bom.csv",
    "cpl": "main_fab/cpl.csv",
    "kicadProject": "main_fab/kicad-project.zip",
    "glb": "main_fab/board.glb",
    "schematicPng": "main_review/_schematic.png",
    "pcbPng": "main_review/_pcb.png",
    "order": "main_fab/ORDER.md",
}


def assert_fab_ready_board(proj: Path) -> dict:
    """Prove that the selected board is the complete orderable packet.

    Structural evals are product-outcome tests.  A successful CLI call, a
    clean warning list, or files with plausible names is not enough: the
    literal readiness verdict, canonical packet manifest, and retained route
    evidence must all describe the same selected Circuit JSON.
    """

    meta = sidecar(proj)
    fab = meta.get("fab")
    assert isinstance(fab, dict) and fab.get("ready") is True, (
        "structural eval completed without literal fab.ready == true"
    )
    warnings = (meta.get("validation") or {}).get("warnings", [])
    assert isinstance(warnings, list), "validation.warnings is not a list"
    errors = [
        warning for warning in warnings
        if isinstance(warning, dict) and warning.get("severity") == "error"
    ]
    assert not errors, f"fab-ready sidecar contains blocking warnings: {errors}"

    artifacts = meta.get("artifacts")
    assert isinstance(artifacts, dict), "fab-ready sidecar has no artifact manifest"
    boards = proj / "boards"
    for key, expected in READY_ARTIFACTS.items():
        assert artifacts.get(key) == expected, (
            f"fab-ready artifact {key} must be {expected!r}, "
            f"got {artifacts.get(key)!r}"
        )
        artifact = boards / expected
        assert artifact.is_file() and not artifact.is_symlink(), (
            f"fab-ready artifact is missing or unsafe: {artifact}"
        )

    profile_id = fab.get("profile")
    assert isinstance(profile_id, str) and profile_id, (
        "fab-ready sidecar has no fabrication profile"
    )
    circuit_json_path = boards / "main.circuit.json"
    assert circuit_json_path.is_file() and not circuit_json_path.is_symlink(), (
        "fab-ready sidecar has no regular selected Circuit JSON"
    )
    evidence_error = routing_attempt_evidence_error(
        meta.get("build"),
        circuit_json_path=circuit_json_path,
        final_warnings=warnings,
        fab_ready=True,
        product=load_product(proj),
        profile=get_profile(profile_id),
    )
    assert evidence_error is None, (
        f"fab-ready packet is not bound to retained routing evidence: {evidence_error}"
    )
    return meta


# ---------------------------------------------------------------------------
# Board sources
# ---------------------------------------------------------------------------

CLEAN_TSX = f"""
import {{ UsbCPower }} from "../blocks/usb-c-power/usb-c-power"
import {{ Ldo3v3 }} from "../blocks/ldo-3v3/ldo-3v3"
import {{ StatusLed }} from "../blocks/status-led/status-led"

export default () => (
  <board width="60mm" height="36mm" {BOARD_PROPS}>
    {{/* USB-C on the bottom edge: the connector's natural orientation already
        faces y-, so no rotation is needed and the accessibility check passes. */}}
    <UsbCPower pcbX={{0}} pcbY={{-13}} schX={{-6}} schY={{0}} />
    <Ldo3v3 pcbX={{-16}} pcbY={{8}} schX={{0}} schY={{0}} />
    <StatusLed rail="V3_3" pcbX={{18}} pcbY={{8}} schX={{6}} schY={{0}} />
    <hole name="H1" diameter="3.2mm" pcbX={{-26}} pcbY={{-14}} />
    <hole name="H2" diameter="3.2mm" pcbX={{26}} pcbY={{14}} />
  </board>
)
"""

BAD_PORT_TSX = f"""
export default () => (
  <board width="20mm" height="20mm" {BOARD_PROPS}>
    <resistor name="R1" resistance="1k" footprint="0402" pcbX={{-5}} pcbY={{0}} schX={{-2}} schY={{0}} />
    <led name="LED1" footprint="0402" pcbX={{5}} pcbY={{0}} schX={{2}} schY={{0}} />
    <trace name="T1" from=".R1 > .pin9" to=".LED1 > .anode" />
    <trace name="T2" from=".LED1 > .cathode" to="net.GND" />
  </board>
)
"""

OVERSIZE_TSX = f"""
export default () => (
  <board width="120mm" height="90mm" {BOARD_PROPS}>
    <resistor name="R1" resistance="1k" footprint="0402" pcbX={{0}} pcbY={{0}} schX={{0}} schY={{0}} />
    <trace name="T1" from=".R1 > .pin1" to="net.GND" />
  </board>
)
"""

DENSE_TSX_HEAD = f"""
import {{ SwTact }} from "../blocks/sw-tact/sw-tact"

export default () => (
  <board width="80mm" height="40mm" {BOARD_PROPS}>
"""


def dense_tsx(keys: int = 12) -> str:
    rows = []
    for i in range(keys):
        x = -33 + (i % 6) * 12
        y = 10 if i < 6 else -10
        rows.append(
            f'    <SwTact name="SW{i + 1}" signal="KEY{i + 1}" '
            f"pcbX={{{x}}} pcbY={{{y}}} schX={{{(i % 6) * 3}}} schY={{{0 if i < 6 else -4}}} />"
        )
    return DENSE_TSX_HEAD + "\n".join(rows) + "\n  </board>\n)\n"


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class Case:
    def __init__(self, name, fn):
        self.name, self.fn = name, fn


def eval_clean_board(tmp: Path):
    """A well-formed board produces the complete artifact set a maker needs."""
    proj = build_project(tmp, CLEAN_TSX)
    result = run_board(proj)
    boards = proj / "boards"
    for rel in (
        "main.circuit.json", "main.board.json",
        "main_review/_schematic.png", "main_review/_pcb.png",
        "main_fab/gerbers.zip", "main_fab/bom.csv",
    ):
        assert (boards / rel).exists(), f"missing artifact {rel}"

    meta = sidecar(proj)
    assert meta["generator"] == "circuitpy"
    assert meta["board"]["layers"] == 2
    assert meta["bom"]["lines"] > 0, "a board with parts must produce BOM lines"

    # Every BOM row on an assembled board needs a real orderable part number,
    # or the fab cannot build it.
    assert meta["bom"]["orderable"] == meta["bom"]["lines"], (
        f"unorderable BOM rows: {meta['bom']}"
    )

    blockers = blocking(proj)
    assert not blockers, f"clean board has blocking warnings: {blockers}"
    assert_fab_ready_board(proj)
    # The pipeline returns the board facts; `ok` is the skill CLI's framing.
    assert result.get("board") or result.get("circuit_json_path"), (
        f"pipeline returned nothing useful: {sorted(result)}"
    )


def eval_packet_is_honest(tmp: Path):
    """`fab.ready` is earned, and ORDER.md only exists when it is.

    This is the eval that protects a user's $85: a packet must never present
    as orderable unless the gerbers were independently verified and nothing
    is blocking.
    """
    proj = build_project(tmp, CLEAN_TSX)
    run_board(proj)
    meta = sidecar(proj)
    ready = meta["fab"]["ready"]
    order_md = proj / "boards" / "main_fab" / "ORDER.md"

    if ready:
        assert meta["fab"]["gerberSource"] == "kicad-cli", (
            "ready packets must ship independently verified gerbers"
        )
        assert not blocking(proj), "ready packet with blocking warnings"
        assert order_md.exists(), "ready packet without an order walkthrough"
    else:
        assert not order_md.exists(), (
            "ORDER.md written for a packet that is not fab-ready — a user "
            "could follow it and pay for an unverified board"
        )


def eval_safety_sentinel(tmp: Path):
    """The envelope refuses mains, bare RF, and loose battery charging — and
    refuses them *for a safety reason*, not by accident."""
    for description in (
        "230VAC mains lamp dimmer with a triac",
        "nRF24L01 bare die with a pi matching network",
        "wearable charged by a TP4056",
    ):
        verdict = safety.safety_gate(description=description)
        assert verdict.status == safety.REFUSE, f"not refused: {description}"
        assert verdict.reasons, "a refusal must say why"

    # And the gate must never call an unexamined design safe.
    assert not safety.safety_gate().ok, "unscreened must not read as a pass"

    failures = circuit_golden.invariants()
    assert not failures, f"golden set broke: {failures}"


def eval_verifier_fires(tmp: Path):
    """A trace to a port that does not exist must be caught and localized."""
    proj = build_project(tmp, BAD_PORT_TSX)
    try:
        run_board(proj)
    except BuildError:
        return  # a hard failure is an acceptable way to catch this
    kinds = warning_kinds(proj)
    assert any("not_connected" in k or "error" in k for k in kinds), (
        f"bad port slipped through: {kinds}"
    )
    assert blocking(proj), "a nonexistent port must block, not advise"


def eval_envelope_enforced(tmp: Path):
    """A board bigger than the declared enclosure envelope blocks — the case
    is already sized, so this is a real product failure, not a nit."""
    proj = build_project(tmp, OVERSIZE_TSX, product={"envelopeMm": [40, 30]})
    run_board(proj)
    kinds = warning_kinds(proj)
    assert "board_exceeds_envelope" in kinds, f"envelope not enforced: {kinds}"


def eval_dense_board(tmp: Path):
    """A 12-switch board still compiles and routes inside the time budget."""
    proj = build_project(tmp, dense_tsx(12))
    started = time.time()
    run_board(proj)
    elapsed = time.time() - started
    meta = sidecar(proj)
    assert meta["bom"]["lines"] >= 12, f"expected >=12 BOM lines: {meta['bom']}"
    assert_fab_ready_board(proj)
    assert elapsed < 420, f"dense board took {elapsed:.0f}s"


def eval_cache_iteration(tmp: Path):
    """Rebuilding an unchanged board is cheap — this is what makes a review
    round affordable, and review rounds are what make boards good."""
    proj = build_project(tmp, CLEAN_TSX)
    first = time.time()
    run_board(proj)
    first_s = time.time() - first
    assert_fab_ready_board(proj)

    second = time.time()
    result = run_board(proj)
    second_s = time.time() - second
    assert_fab_ready_board(proj)

    assert result.get("unchanged") is True or second_s < first_s, (
        f"re-run was not cheaper: {first_s:.1f}s then {second_s:.1f}s"
    )


CASES = [
    Case("clean-board", eval_clean_board),
    Case("packet-is-honest", eval_packet_is_honest),
    Case("safety-sentinel", eval_safety_sentinel),
    Case("verifier-fires", eval_verifier_fires),
    Case("envelope-enforced", eval_envelope_enforced),
    Case("dense-board", eval_dense_board),
    Case("cache-iteration", eval_cache_iteration),
]


def main(argv: list[str]) -> int:
    wanted = set(argv[1:])
    cases = [c for c in CASES if not wanted or c.name in wanted]
    if wanted and not cases:
        print(f"no such case; have: {', '.join(c.name for c in CASES)}")
        return 2

    passed = 0
    for case in cases:
        tmp = Path(tempfile.mkdtemp(prefix=f"circuit-eval-{case.name}-"))
        started = time.time()
        try:
            case.fn(tmp)
        except Exception as exc:  # noqa: BLE001 - the scorecard is the report
            elapsed = time.time() - started
            print(f"FAIL {case.name} {elapsed:.1f}s\n     {type(exc).__name__}: {exc}"
                  f"\n     kept: {tmp}", flush=True)
            continue
        elapsed = time.time() - started
        shutil.rmtree(tmp, ignore_errors=True)
        passed += 1
        print(f"ok   {case.name} {elapsed:.1f}s", flush=True)

    print(f"{passed}/{len(cases)} evals passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
