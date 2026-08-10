"""pytest fixtures for the circuitcode skill.

The real pipeline (``circuitpy.generation.build_board``) needs the pinned
Node toolchain, KiCad, and — for a full build — one network call to the
tscircuit parts engine. None of that belongs in this suite: these tests own
the **CLI layer**, so they need a pipeline whose shape is right and whose
behavior is steerable, not a real one.

So this conftest materializes a lightweight stub ``circuitpy`` into a
tempdir and injects it via ``CIRCUITCODE_TEST_CIRCUITPY_PATH`` (the hook
``common.runner.ensure_circuitpy_path`` re-hoists above every other source).
The suite then exercises the runner, the three CLIs, and the contract §3
JSON with no toolchain and no network.

The stub:

  * matches the frozen contract §1 signature
    ``build_board(source_path, output_path, *, fab=None, max_build_s=None)``
    and carries the full error hierarchy (``BuildError`` →
    ``ProjectShapeError`` / ``SpecValidationError`` / ``CompileError`` /
    ``ToolchainError`` / ``ExportError``);
  * enforces the real project shape — ``.circuit.json`` output suffix,
    ``boards/main.tsx`` for directory input, a ``product.json`` up-tree —
    so shape failures in the tests are real failures, not scripted ones;
  * writes the contract §1 artifact set: ``<stem>.circuit.json``,
    the canonical camelCase ``<stem>.board.json`` sidecar (written BEFORE
    the IR, per the ordering rule), ``<stem>_review/`` PNGs, ``<stem>_fab/``;
  * steers on ``STUB_*`` directives in the board source, so each error code
    and each timeout path is reachable from a real CLI invocation.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"


def _build_stub_circuitpy(stub_root: Path) -> None:
    """Materialize a tiny ``circuitpy`` package at ``stub_root/circuitpy/``."""
    pkg = stub_root / "circuitpy"
    pkg.mkdir(parents=True, exist_ok=True)

    (pkg / "__init__.py").write_text(dedent('''
        """Test stub for circuitpy. See tests/conftest.py for the why."""
        from circuitpy.errors import (  # noqa: F401
            BuildError,
            CompileError,
            ExportError,
            ProjectShapeError,
            SpecValidationError,
            ToolchainError,
        )
        from circuitpy.generation import build_board  # noqa: F401
    ''').lstrip())

    (pkg / "errors.py").write_text(dedent('''
        """Contract §1 error hierarchy, verbatim."""
        from __future__ import annotations


        class BuildError(Exception):
            pass


        class ProjectShapeError(BuildError):
            pass


        class SpecValidationError(BuildError):
            pass


        class CompileError(BuildError):
            pass


        class ToolchainError(BuildError):
            pass


        class ExportError(BuildError):
            pass
    ''').lstrip())

    (pkg / "review.py").write_text(dedent('''
        """Stub of circuitpy.review — writes the two PNGs the loop reads."""
        from __future__ import annotations

        from pathlib import Path

        # A valid 1x1 gray PNG.
        PNG_1PX = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e"
            "9b550000000a49444154789c636000000002000148afa4710000000049454e"
            "44ae426082"
        )


        def is_double_sided(circuit_json) -> bool:
            for element in circuit_json:
                if not isinstance(element, dict):
                    continue
                if element.get("type") in ("pcb_component", "pcb_smtpad") and (
                    str(element.get("layer") or "").lower() == "bottom"
                ):
                    return True
            return False


        def write_review(
            *,
            circuit_json_path,
            review_dir,
            built_schematic_png=None,
            built_pcb_png=None,
            double_sided: bool = False,
        ) -> dict:
            review_dir = Path(review_dir)
            review_dir.mkdir(parents=True, exist_ok=True)
            written = {}
            names = ["_schematic.png", "_pcb.png"]
            if double_sided:
                names.append("_pcb_bottom.png")
            for name in names:
                path = review_dir / name
                path.write_bytes(PNG_1PX)
                written[name] = path
            for name in ("_schematic.svg", "_pcb.svg"):
                path = review_dir / name
                path.write_text("<svg/>", encoding="utf-8")
                written[name] = path
            return written
    ''').lstrip())

    (pkg / "generation.py").write_text(dedent('''
        """Stub circuitpy.generation mirroring contract §1's build_board.

        Steering directives, written as comments in the board source so a
        test drives the pipeline exactly the way a real board would:

          STUB_SPEC_ERROR      -> SpecValidationError  (VALIDATION_FAILED)
          STUB_COMPILE_ERROR   -> CompileError         (COMPILE_ERROR)
          STUB_TOOLCHAIN_ERROR -> ToolchainError       (TOOLCHAIN_ERROR)
          STUB_EXPORT_ERROR    -> ExportError          (EXPORT_ERROR)
          STUB_PART_ERROR      -> ToolchainError naming the parts engine
                                                       (PART_ERROR)
          STUB_TIMEOUT         -> TimeoutError, the toolchain's own budget
                                                       (BUILD_TIMEOUT)
          STUB_HANG            -> sleeps past the parent's wall clock
          STUB_GARBAGE         -> writes a non-JSON last line and exits 0
          STUB_WARN_ERROR      -> one severity=error warning
          STUB_WARN_INFO       -> one severity=info warning
          STUB_DOUBLE_SIDED    -> a bottom-layer part (adds _pcb_bottom.png)
        """
        from __future__ import annotations

        import json
        import os
        import sys
        import time
        from pathlib import Path

        from circuitpy import review as review_mod
        from circuitpy.errors import (
            BuildError,
            CompileError,
            ExportError,
            ProjectShapeError,
            SpecValidationError,
            ToolchainError,
        )

        __all__ = [
            "build_board",
            "BuildError",
            "ProjectShapeError",
            "SpecValidationError",
            "CompileError",
            "ToolchainError",
            "ExportError",
        ]

        GENERATOR_NAME = "circuitpy"
        OUTPUT_SUFFIX = ".circuit.json"


        def _canonical(payload: dict) -> str:
            return json.dumps(payload, sort_keys=True, separators=(",", ":"))


        def _resolve_source(source_path: Path) -> Path:
            if source_path.is_dir():
                script = source_path / "boards" / "main.tsx"
                if not script.is_file():
                    raise ProjectShapeError(
                        f"directory input must contain boards/main.tsx: {source_path}"
                    )
                return script
            if not source_path.is_file():
                raise ProjectShapeError(f"board source not found: {source_path}")
            if source_path.suffix != ".tsx":
                raise ProjectShapeError(
                    f"board source must be a .tsx file or a project directory "
                    f"(got {source_path.name})"
                )
            return source_path


        def _project_root(script: Path) -> Path:
            for cand in (script.parent, *script.parent.parents):
                if (cand / "product.json").is_file():
                    return cand
            raise ProjectShapeError(
                f"no product.json found in any parent of {script}"
            )


        def build_board(source_path, output_path, *, fab=None, max_build_s=None):
            source_path = Path(source_path)
            output_path = Path(output_path)
            if not output_path.name.endswith(OUTPUT_SUFFIX):
                raise ProjectShapeError(
                    f"output_path must end in {OUTPUT_SUFFIX} "
                    f"(got {output_path.name})"
                )
            stem = output_path.name[: -len(OUTPUT_SUFFIX)]
            out_dir = output_path.parent
            out_dir.mkdir(parents=True, exist_ok=True)

            script = _resolve_source(source_path)
            root = _project_root(script)
            product = json.loads((root / "product.json").read_text(encoding="utf-8"))
            text = script.read_text(encoding="utf-8")

            if "STUB_GARBAGE" in text:
                sys.stdout.write("tscircuit-cli: <<< this is not json >>>\\n")
                sys.stdout.flush()
                os._exit(0)
            if "STUB_HANG" in text:
                time.sleep(600)
            if "STUB_TIMEOUT" in text:
                raise TimeoutError(
                    "tscircuit-cli build timed out after 5s: build boards/main.tsx…"
                )
            if "STUB_SPEC_ERROR" in text:
                raise SpecValidationError(
                    "safety_envelope: mains reference — no mains, ever "
                    "(boards/main.tsx:1: 'mains')"
                )
            if "STUB_COMPILE_ERROR" in text:
                raise CompileError(
                    "tscircuit eval failed for boards/main.tsx (exit 1): "
                    "Unexpected token"
                )
            if "STUB_PART_ERROR" in text:
                raise ToolchainError("parts engine unreachable while assigning LCSC parts")
            if "STUB_TOOLCHAIN_ERROR" in text:
                raise ToolchainError("tscircuit-cli not found — run scripts/setup-toolchain.sh")
            if "STUB_EXPORT_ERROR" in text:
                raise ExportError("failed to write fab packet: disk full")

            double_sided = "STUB_DOUBLE_SIDED" in text
            warnings = []
            if "STUB_WARN_ERROR" in text:
                warnings.append({
                    "part": "U3.pin7",
                    "kind": "source_trace_not_connected_error",
                    "detail": "pin 7 of U3 is not connected to any net",
                    "severity": "error",
                })
            if "STUB_WARN_INFO" in text:
                warnings.append({
                    "part": "board",
                    "kind": "kicad_unavailable",
                    "detail": "kicad-cli not installed — stage 3 skipped",
                    "severity": "info",
                })
            warnings.append({
                "part": "board",
                "kind": "unverified_gerbers",
                "detail": "gerbers were exported by tscircuit without kicad-cli",
                "severity": "warning",
            })

            width_mm, height_mm = 20.0, 12.0
            layers = int(product.get("layers") or 2)
            circuit_json = [
                {
                    "type": "pcb_board",
                    "width": width_mm,
                    "height": height_mm,
                    "num_layers": layers,
                },
                {"type": "pcb_component", "layer": "top", "name": "R1"},
            ]
            if double_sided:
                circuit_json.append(
                    {"type": "pcb_component", "layer": "bottom", "name": "LED1"}
                )

            # -- Fab packet. -------------------------------------------------
            profile = fab or os.environ.get("CIRCUIT_FAB") or "jlcpcb"
            fab_dir = out_dir / f"{stem}_fab"
            fab_dir.mkdir(parents=True, exist_ok=True)
            (fab_dir / "gerbers.zip").write_bytes(b"PK\\x05\\x06" + b"\\x00" * 18)
            (fab_dir / "bom.csv").write_text(
                "Comment,Designator,Footprint,LCSC Part #\\n"
                "1k,R1,0402,C11702\\n"
                "RED LED,LED1,0402,C2286\\n",
                encoding="utf-8",
            )
            ready = not any(w["severity"] == "error" for w in warnings)

            # -- Review images. ----------------------------------------------
            review_dir = out_dir / f"{stem}_review"
            written = review_mod.write_review(
                circuit_json_path=output_path,
                review_dir=review_dir,
                built_schematic_png=None,
                built_pcb_png=None,
                double_sided=double_sided,
            )

            bom_block = {
                "lines": 2,
                "orderable": 2,
                "basicParts": 2,
                "estimatedCostUsd": 0.42,
            }
            artifacts = {
                "schematicPng": f"{stem}_review/_schematic.png",
                "pcbPng": f"{stem}_review/_pcb.png",
                "gerbers": f"{stem}_fab/gerbers.zip",
                "bom": f"{stem}_fab/bom.csv",
            }
            validation = {"warnings": warnings} if warnings else {}
            sidecar_path = out_dir / f"{stem}.board.json"
            sidecar_path.write_text(_canonical({
                "generator": GENERATOR_NAME,
                "entryKind": "board",
                "source": {
                    "kind": "tsx",
                    "path": script.name,
                    "hash": "stubhash",
                    "fingerprint": "stubfingerprint",
                },
                "board": {
                    "path": output_path.name,
                    "name": str(product.get("name") or "stub-board"),
                    "widthMm": width_mm,
                    "heightMm": height_mm,
                    "layers": layers,
                },
                "toolchain": {"tscircuit": "0.0.0-stub", "checks": "0.0.0-stub"},
                "bom": bom_block,
                "fab": {
                    "profile": profile,
                    "ready": ready,
                    "assembly": bool(product.get("assembly", False)),
                    "gerberSource": "tscircuit",
                    "packet": f"{stem}_fab/",
                },
                "validation": validation,
                "artifacts": artifacts,
            }), encoding="utf-8")

            # Ordering rule: the IR of record lands LAST.
            output_path.write_text(json.dumps(circuit_json), encoding="utf-8")

            return {
                "circuit_json_path": str(output_path),
                "metadata_path": str(sidecar_path),
                "schematic_png": str(written["_schematic.png"]),
                "pcb_png": str(written["_pcb.png"]),
                "board": {
                    "width_mm": width_mm,
                    "height_mm": height_mm,
                    "layers": layers,
                },
                "bom": {"lines": 2, "orderable": 2, "estimated_cost_usd": 0.42},
                "fab": {
                    "profile": profile,
                    "ready": ready,
                    "packet_dir": str(fab_dir),
                },
                "warnings": warnings,
            }
    ''').lstrip())


@pytest.fixture(scope="session", autouse=True)
def circuitpy_stub():
    """Build the stub once per session; expose it to the CLIs (which read
    the env var) and to in-process helpers (sys.path)."""
    stub_root = Path(tempfile.mkdtemp(prefix="circuitcode-circuitpy-stub-"))
    _build_stub_circuitpy(stub_root)
    prev = os.environ.get("CIRCUITCODE_TEST_CIRCUITPY_PATH")
    os.environ["CIRCUITCODE_TEST_CIRCUITPY_PATH"] = str(stub_root)
    sys.path.insert(0, str(stub_root))
    try:
        yield stub_root
    finally:
        if prev is None:
            os.environ.pop("CIRCUITCODE_TEST_CIRCUITPY_PATH", None)
        else:
            os.environ["CIRCUITCODE_TEST_CIRCUITPY_PATH"] = prev
        if str(stub_root) in sys.path:
            sys.path.remove(str(stub_root))
        shutil.rmtree(stub_root, ignore_errors=True)


# -- Project scaffolding ----------------------------------------------------


GOOD_TSX = """export default () => (
  <board width="20mm" height="12mm" thickness={1.6}>
    <resistor name="R1" resistance="1k" footprint="0402" pcbX={-5} pcbY={0} />
    <led name="LED1" footprint="0402" pcbX={5} pcbY={0} />
    <trace from=".R1 > .pin2" to=".LED1 > .anode" />
  </board>
)
"""


def write_project(root: Path, *, tsx: str = GOOD_TSX, product: dict | None = None) -> Path:
    """Write a contract-shaped project: product.json + boards/main.tsx."""
    root.mkdir(parents=True, exist_ok=True)
    payload = product if product is not None else {
        "name": "stub-board",
        "description": "circuitcode CLI test board",
        "power": "usb-c-5v",
        "envelopeMm": [60, 40],
        "layers": 2,
        "fab": "jlcpcb",
        "assembly": True,
    }
    (root / "product.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    boards = root / "boards"
    boards.mkdir(parents=True, exist_ok=True)
    (boards / "main.tsx").write_text(tsx, encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A ready-to-build project rooted at ``tmp_path/proj``."""
    return write_project(tmp_path / "proj")
