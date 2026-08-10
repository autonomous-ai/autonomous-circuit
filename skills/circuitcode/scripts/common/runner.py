"""circuitpy-backed runner — invoked once per agent tool call.

The agent runs ``python scripts/circuit boards/main.tsx`` (or a project
dir containing ``boards/main.tsx``). That spawns a fresh subprocess with a
minimal environment, sets rlimit ceilings as a runaway backstop, and hands
the board source to ``circuitpy.generation.build_board``, which writes the
contract §1 artifact set next to the output path:

  <stem>.circuit.json  <stem>.board.json  <stem>_review/  <stem>_fab/

The parent (:func:`run_sandboxed_sync`) owns the wall clock and the JSON
unwrapping for the CLIs. It ALWAYS returns a dict and never raises, so the
CLI can print one JSON line no matter how badly the child died.

No user-code import sandbox lives here, unlike the donor. The board source
is **TSX**, evaluated by the pinned Node toolchain in its own process
(contract §1, "the ffmpeg posture") — there is no user Python to confine,
so the donor's ``__import__`` allow-list and caller-attribution machinery
would guard nothing. What remains is the part that still earns its keep:
a minimal environment, a wall clock the parent enforces, rlimit ceilings,
and last-line JSON parsing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import resource  # Unix-only; absent on Windows.
except ImportError:  # pragma: no cover - platform-dependent
    resource = None


SCRIPTS_DIR = Path(__file__).resolve().parents[1]
SKILL_ROOT = SCRIPTS_DIR.parent
TEST_PATH_ENV = "CIRCUITCODE_TEST_CIRCUITPY_PATH"

OUTPUT_SUFFIX = ".circuit.json"
SIDECAR_SUFFIX = ".board.json"

# Contract §3's closed error-code set.
ERROR_CODES = (
    "VALIDATION_FAILED",
    "COMPILE_ERROR",
    "TOOLCHAIN_ERROR",
    "EXPORT_ERROR",
    "BUILD_TIMEOUT",
    "RUNTIME_ERROR",
    "PART_ERROR",
)

# Environment the child needs, and the ONLY circuit-owned variables that
# cross the boundary (contract §3). PATH/HOME ride along because the
# toolchain resolves `node`/`bun` from PATH and caches under HOME.
CIRCUIT_ENV_VARS = (
    "CIRCUIT_PARTS_ENGINE",
    "CIRCUIT_FAB",
    "CIRCUIT_TOOLCHAIN",
    "CIRCUIT_WALL_CLOCK_S",
    "CIRCUIT_PYTHON",
    "CIRCUIT_FORCE_REGEN",
)
# Plus the test-stub injection hook, which must reach the child or the
# suite would exercise the real toolchain.
PASS_THROUGH_VARS = CIRCUIT_ENV_VARS + (TEST_PATH_ENV,)

KEYS_ENV_FILE = Path(os.environ.get("HOME", "/tmp")) / ".autonomous-circuit" / "keys.env"


# -- Wall clock -------------------------------------------------------------


def _default_wall_clock_s() -> float:
    """Wall-clock budget for one ``build_board`` run.

    A full build compiles TSX, runs @tscircuit/checks, converts to KiCad,
    runs ERC/DRC, exports gerbers and renders two images — minutes, not
    seconds, on a cold toolchain cache. 300s is the contract §3 default;
    ``CIRCUIT_WALL_CLOCK_S`` overrides it explicitly.
    """
    override = os.environ.get("CIRCUIT_WALL_CLOCK_S", "").strip()
    if override:
        try:
            return max(10.0, float(override))
        except ValueError:
            pass
    return 300.0


WALL_CLOCK_TIMEOUT_S = _default_wall_clock_s()
CPU_TIMEOUT_S = 300
# Gerber zips and review PNGs are kilobytes; a .glb is low single-digit MiB.
# 512 MiB only ever catches a runaway writer.
OUTPUT_FILE_LIMIT_BYTES = 512 * 1024 * 1024


def _enforce_rlimits(wall_clock_s: float = WALL_CLOCK_TIMEOUT_S) -> None:
    """Runaway backstop only — the parent's wall clock is the real deadline.

    Two donor ceilings are deliberately NOT set here: ``RLIMIT_AS`` (V8
    reserves a huge virtual address space at startup, so a 1 GiB cap kills
    node outright) and ``RLIMIT_NOFILE`` (the Node toolchain opens far more
    than 64 descriptors). Both are inherited by every toolchain child, so a
    wrong value breaks real builds instead of catching runaways.
    """
    if resource is None:  # pragma: no cover - platform-dependent
        return
    ncores = os.cpu_count() or 1
    # CPU-seconds are counted across all threads and inherited by the node
    # children, which compile in parallel — budget the wall deadline x cores.
    cpu_limit = max(CPU_TIMEOUT_S, int(wall_clock_s * ncores))
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    except (ValueError, OSError):  # pragma: no cover
        pass
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_FILE_LIMIT_BYTES,) * 2)
    except (ValueError, OSError):  # pragma: no cover
        pass


# -- Skill-runtime resolution ------------------------------------------------


def ensure_circuitpy_path() -> None:
    """Put circuitpy on ``sys.path``, highest-priority source last-inserted.

    Resolution order (contract §3):

    1. ``$CIRCUITCODE_TEST_CIRCUITPY_PATH`` — the test stub. It must ALWAYS
       win: both it and the vendored package are ordinary packages, so path
       order alone decides. Re-hoisted (removed then re-inserted at 0) in
       case an earlier bootstrap already appended it lower down.
    2. ``scripts/packages/`` — the vendored copy the skill ships.
    3. ``<repo>/packages/circuitpy/src`` — dev-before-vendoring fallback,
       found by walking up from this file.
    """
    repo_src = None
    for ancestor in SKILL_ROOT.resolve().parents:
        candidate = ancestor / "packages" / "circuitpy" / "src"
        if (candidate / "circuitpy" / "__init__.py").is_file():
            repo_src = candidate
            break
    if repo_src is not None and str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))

    packages_dir = SCRIPTS_DIR / "packages"
    if str(packages_dir) not in sys.path:
        sys.path.insert(0, str(packages_dir))

    test_path = os.environ.get(TEST_PATH_ENV)
    if test_path:
        while test_path in sys.path:
            sys.path.remove(test_path)
        sys.path.insert(0, test_path)


# -- Paths -------------------------------------------------------------------


def workspace_relative(path: Path | str) -> str:
    """Contract §3: paths in the stdout JSON are workspace-relative.

    The CLI runs with cwd = the user's workspace, so relpath against cwd is
    workspace-relative; a path that escapes the workspace stays absolute
    rather than growing ``../`` chains.
    """
    p = Path(path)
    try:
        rel = os.path.relpath(p, Path.cwd())
    except ValueError:  # different drive (Windows)
        return str(p)
    if rel.startswith(".."):
        return str(p)
    return rel


def _drain_oversized_outputs(out_dir: Path) -> list[str]:
    """Remove and name any artifact pinned at the RLIMIT_FSIZE cap.

    A write killed by RLIMIT_FSIZE leaves a truncated file at exactly the
    cap; a successful export is strictly smaller, so ``size >= cap``
    uniquely flags corrupt partials (donor rule).
    """
    removed: list[str] = []
    try:
        for p in out_dir.rglob("*"):
            try:
                if p.is_file() and p.stat().st_size >= OUTPUT_FILE_LIMIT_BYTES:
                    p.unlink()
                    removed.append(p.name)
            except OSError:
                pass
    except OSError:
        pass
    return removed


# -- Subprocess (in-process) entrypoint --------------------------------------


def in_subprocess_main(
    source_path: str,
    out_dir: str,
    stem: str,
    fab: str = "",
    wall_clock_s: float = WALL_CLOCK_TIMEOUT_S,
) -> int:
    """Entry point invoked inside the subprocess.

    ``source_path`` is ``boards/<stem>.tsx`` or a directory containing
    ``boards/main.tsx`` — ``build_board`` accepts both (§1).
    """
    import traceback

    ensure_circuitpy_path()
    import circuitpy  # noqa: F401
    import circuitpy.generation as _gen

    source_p = Path(source_path).resolve()
    out_dir_p = Path(out_dir).resolve()
    out_dir_p.mkdir(parents=True, exist_ok=True)
    output_path = out_dir_p / f"{stem}{OUTPUT_SUFFIX}"

    _enforce_rlimits(wall_clock_s)

    try:
        result = _gen.build_board(
            source_path=source_p,
            output_path=output_path,
            fab=(fab or None),
            # Leave the child a slice of the budget so its own timeout fires
            # first and reports WHICH stage stalled; the parent's kill is the
            # backstop for a child that never returns at all.
            max_build_s=max(5.0, wall_clock_s * 0.9),
        )
    except Exception as e:
        code = _map_error_code(e, _gen)
        oversized = _drain_oversized_outputs(out_dir_p)
        if oversized:
            cap_mib = OUTPUT_FILE_LIMIT_BYTES // (1024 * 1024)
            message = (
                f"output too large: ({', '.join(sorted(oversized))}) hit the "
                f"{cap_mib} MiB file cap and was discarded"
            )
            code = "EXPORT_ERROR"
        else:
            message = f"{type(e).__name__}: {e}"
        _emit({
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "traceback": traceback.format_exc(limit=6),
            },
        })
        return 1

    _emit(_build_success_payload(result, out_dir_p, stem))
    return 0


def _map_error_code(e: Exception, gen_mod: Any) -> str:
    """Contract §3's closed error-code set, from §1's error hierarchy.

    Mapped by type against the generation module (which re-exports the whole
    hierarchy), getattr-guarded so a stub or an older vendored copy missing a
    class degrades to RUNTIME_ERROR instead of blowing up here.
    """
    def _t(name: str):
        cls = getattr(gen_mod, name, None)
        return cls if isinstance(cls, type) else ()

    message = str(e).lower()

    # A toolchain subprocess that blew its budget is a BUILD_TIMEOUT, not a
    # generic TOOLCHAIN_ERROR: circuitpy.toolchain raises TimeoutError
    # ("… timed out after 600s: …") and generation wraps it into
    # ToolchainError, so the distinction only survives in the message.
    if isinstance(e, TimeoutError) or "timed out" in message:
        return "BUILD_TIMEOUT"
    # No circuitpy error maps to PART_ERROR in v1 (unorderable parts are
    # warnings, not failures). Reserved for a future PartError, plus the one
    # real failure that is squarely the parts subsystem's.
    if isinstance(e, _t("PartError")):
        return "PART_ERROR"
    if "parts engine" in message or "parts-engine" in message:
        return "PART_ERROR"
    if isinstance(e, (_t("SpecValidationError"), _t("ProjectShapeError"))):
        return "VALIDATION_FAILED"
    if isinstance(e, _t("CompileError")):
        return "COMPILE_ERROR"
    if isinstance(e, _t("ToolchainError")):
        return "TOOLCHAIN_ERROR"
    if isinstance(e, _t("ExportError")):
        return "EXPORT_ERROR"
    if isinstance(e, SyntaxError):
        return "RUNTIME_ERROR"
    if isinstance(e, (TypeError, ValueError, AssertionError)):
        return "VALIDATION_FAILED"
    return "RUNTIME_ERROR"


def _build_success_payload(
    pipeline_result: Any,
    out_dir: Path,
    stem: str,
) -> dict[str, Any]:
    """Normalize ``build_board``'s return into contract §3 ``CircuitcodeResult``.

    Three sources, in order: the pipeline's own snake_case return dict, then
    the camelCase ``.board.json`` sidecar (the frozen §1 contract), then
    filesystem inference — so a partial return still yields a full verdict.
    """
    payload: dict[str, Any] = {"ok": True}
    src: dict[str, Any] = pipeline_result if isinstance(pipeline_result, dict) else {}

    sidecar_path = out_dir / f"{stem}{SIDECAR_SUFFIX}"
    sidecar: dict[str, Any] = {}
    if sidecar_path.is_file():
        try:
            loaded = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                sidecar = loaded
        except (OSError, ValueError):
            sidecar = {}

    artifacts = sidecar.get("artifacts") if isinstance(sidecar.get("artifacts"), dict) else {}
    review_dir = out_dir / f"{stem}_review"
    fab_dir = out_dir / f"{stem}_fab"

    def _from_artifacts(key: str) -> Path | None:
        rel = artifacts.get(key)
        return out_dir / str(rel) if isinstance(rel, str) and rel else None

    inferred: dict[str, Path | None] = {
        "circuit_json_path": out_dir / f"{stem}{OUTPUT_SUFFIX}",
        "metadata_path": sidecar_path,
        "schematic_png": _from_artifacts("schematicPng") or review_dir / "_schematic.png",
        "pcb_png": _from_artifacts("pcbPng") or review_dir / "_pcb.png",
    }
    for key, cand in inferred.items():
        value = src.get(key)
        if value is not None:
            payload[key] = workspace_relative(value)
        elif cand is not None and cand.exists():
            payload[key] = workspace_relative(cand)

    # board {width_mm, height_mm, layers}
    board = src.get("board")
    if not isinstance(board, dict):
        meta = sidecar.get("board") if isinstance(sidecar.get("board"), dict) else {}
        board = {
            "width_mm": meta.get("widthMm"),
            "height_mm": meta.get("heightMm"),
            "layers": meta.get("layers"),
        }
    if any(v is not None for v in board.values()):
        payload["board"] = {
            "width_mm": float(board.get("width_mm") or 0.0),
            "height_mm": float(board.get("height_mm") or 0.0),
            "layers": int(board.get("layers") or 0),
        }

    # bom {lines, orderable, estimated_cost_usd?}
    bom = src.get("bom")
    if not isinstance(bom, dict):
        meta = sidecar.get("bom") if isinstance(sidecar.get("bom"), dict) else {}
        bom = {
            "lines": meta.get("lines"),
            "orderable": meta.get("orderable"),
            "estimated_cost_usd": meta.get("estimatedCostUsd"),
        }
    if any(v is not None for v in bom.values()):
        block: dict[str, Any] = {
            "lines": int(bom.get("lines") or 0),
            "orderable": int(bom.get("orderable") or 0),
        }
        cost = bom.get("estimated_cost_usd")
        if cost is not None:
            block["estimated_cost_usd"] = float(cost)
        payload["bom"] = block

    # fab {profile, ready, packet_dir?}
    fab = src.get("fab")
    if not isinstance(fab, dict):
        meta = sidecar.get("fab") if isinstance(sidecar.get("fab"), dict) else {}
        fab = {
            "profile": meta.get("profile"),
            "ready": meta.get("ready"),
            "packet_dir": str(fab_dir) if meta.get("packet") else None,
        }
    if any(v is not None for v in fab.values()):
        block = {
            "profile": str(fab.get("profile") or ""),
            "ready": bool(fab.get("ready")),
        }
        packet = fab.get("packet_dir")
        if packet is None and fab_dir.is_dir():
            packet = fab_dir
        if packet is not None:
            block["packet_dir"] = workspace_relative(packet)
        payload["fab"] = block

    warnings = src.get("warnings")
    if warnings is None:
        validation = sidecar.get("validation")
        if isinstance(validation, dict):
            warnings = validation.get("warnings")
    if warnings:
        payload["warnings"] = [
            {
                "part": str(w.get("part", "")),
                "kind": str(w.get("kind", "")),
                "detail": str(w.get("detail", "")),
                "severity": str(w.get("severity", "warning")),
            }
            for w in warnings
            if isinstance(w, dict)
        ]

    if src.get("unchanged"):
        payload["unchanged"] = True
    return payload


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


# -- Parent-side helper ------------------------------------------------------


def _child_env() -> dict[str, str]:
    """Minimal environment for the worker.

    Two sources for the circuit variables, the real environment winning:
    ``~/.autonomous-circuit/keys.env`` (the paste-a-setting file) first,
    then anything the server or the shell actually exported.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(SCRIPTS_DIR),
    }
    if KEYS_ENV_FILE.is_file():
        try:
            for line in KEYS_ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key in CIRCUIT_ENV_VARS and value and key not in os.environ:
                    env[key] = value
        except OSError:
            pass  # an unreadable keys file must never break a build
    for var in PASS_THROUGH_VARS:
        if var in os.environ:
            env[var] = os.environ[var]
    return env


def run_sandboxed_sync(
    source_path: Path,
    out_dir: Path,
    stem: str,
    *,
    fab: str | None = None,
    wall_clock_s: float = WALL_CLOCK_TIMEOUT_S,
) -> dict[str, Any]:
    """Spawn the worker subprocess and return the parsed JSON result.

    Always returns a dict — never raises — so the CLI can always print one
    JSON line. Parses ``stdout.splitlines()[-1]`` (last-line tolerance: the
    toolchain chatters above the JSON line).
    """
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys, os; "
            # Tests inject a fast stub circuitpy via this env var (see
            # tests/conftest.py); production uses the vendored copy at
            # scripts/packages/circuitpy/.
            "_p = os.environ.get('CIRCUITCODE_TEST_CIRCUITPY_PATH'); "
            "_p and sys.path.insert(0, _p); "
            "from common.runner import in_subprocess_main; "
            "sys.exit(in_subprocess_main(sys.argv[1], sys.argv[2], sys.argv[3], "
            "sys.argv[4], float(sys.argv[5])))"
        ),
        str(source_path),
        str(out_dir),
        stem,
        fab or "",
        str(wall_clock_s),
    ]

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            env=_child_env(),
            capture_output=True,
            text=True,
            timeout=wall_clock_s,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "error": {
                "code": "BUILD_TIMEOUT",
                "message": (
                    f"build timed out after {wall_clock_s:g}s — a toolchain "
                    "stage ran past the wall clock. Re-run with a higher "
                    "--wall-clock-s, or simplify the board."
                ),
            },
            "timed_out": True,
            "stdout": (e.stdout or "")[-2000:] if isinstance(e.stdout, str) else "",
            "stderr": (e.stderr or "")[-2000:] if isinstance(e.stderr, str) else "",
        }

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if not stdout:
        # Likely killed — SIGXCPU from the CPU rlimit, or the OOM killer.
        return {
            "ok": False,
            "error": {
                "code": "BUILD_TIMEOUT",
                "message": "worker produced no output (likely CPU rlimit kill)",
            },
            "stderr": stderr[-2000:],
        }

    last_line = stdout.splitlines()[-1]
    try:
        payload = json.loads(last_line)
    except json.JSONDecodeError:
        return {
            "ok": False,
            "error": {
                "code": "RUNTIME_ERROR",
                "message": "worker emitted non-JSON output",
            },
            "stdout": stdout[-2000:],
            "stderr": stderr[-2000:],
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": {
                "code": "RUNTIME_ERROR",
                "message": "worker emitted a non-object JSON line",
            },
            "stdout": stdout[-2000:],
        }
    return payload
