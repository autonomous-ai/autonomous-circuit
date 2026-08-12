"""``build_board()`` — the public §1 entry point of circuitpy.

Board TSX -> verified artifacts, per ``docs/circuit-interfaces.md`` §1:

  0. compile   — ``tscircuit-cli build`` (mirror copy of the project inside
                 ``.circuit/build/``, so ``dist/`` and the CLI's caches never
                 pollute the workspace the snapshotter watches)
  1. scan      — ``*_error`` / ``*_warning`` elements in circuit.json
  2. re-check  — @tscircuit/checks over the same JSON (independent codepath)
  3. substrate — kicad_sch/kicad_pcb conversion + kicad-cli ERC/DRC
                 (kicad absent -> one ``kicad_unavailable`` info)
  4. DFM+BOM   — the fab profile's limit table + orderability + lock drift
  5. fab       — gerbers (kicad-cli when available, else tscircuit +
                 ``unverified_gerbers``), bom.csv/cpl.csv, ORDER.md when
                 fab-ready, board.glb best-effort
  6. review    — ``_review/`` images, the ``.board.json`` sidecar, and the
                 circuit.json artifact of record moved into place LAST

Standing rules (frozen): every gate parses produced artifacts, never exit
codes; the sidecar lands BEFORE the artifact of record; checks never raise;
severity is the driver's only gate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Callable, Sequence

from circuitpy import checks
from circuitpy import enclosure as enclosure_mod
from circuitpy import export_cache
from circuitpy import fab as fab_mod
from circuitpy import kicad_normalize
from circuitpy import review as review_mod
from circuitpy import spec as spec_mod
from circuitpy import status as status_mod
from circuitpy import toolchain
from circuitpy import verify_bridge
from circuitpy.errors import (
    BuildError,
    CompileError,
    ExportError,
    ProjectShapeError,
    SpecValidationError,
    ToolchainError,
)
from circuitpy.source_hash import BoardSourceHash, board_source_hash
from circuitpy.block_snapshot import validate_project_snapshot

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
DEFAULT_FAB = "jlcpcb"
FAB_ENV = "CIRCUIT_FAB"
FORCE_ENV = "CIRCUIT_FORCE_REGEN"
PARTS_ENGINE_ENV = "CIRCUIT_PARTS_ENGINE"

DEFAULT_BUILD_TIMEOUT_S = 600.0
EXPORT_TIMEOUT_S = 180.0
KICAD_TIMEOUT_S = 120.0

OUTPUT_SUFFIX = ".circuit.json"


def _kicad_gerber_export_args(gerber_dir: Path, kicad_pcb: Path) -> list[str]:
    """Return the shipping Gerber command with silk clipped to mask openings.

    KiCad's ``--subtract-soldermask`` plots no silkscreen where the board has a
    solder-mask opening.  Keeping this in the exporter (rather than repairing
    individual footprints) makes the shipped packet safe even when a library
    courtyard/reference stroke crosses an exposed pad.
    """

    return [
        "pcb",
        "export",
        "gerbers",
        "--subtract-soldermask",
        "-o",
        str(gerber_dir) + os.sep,
        str(kicad_pcb),
    ]


@lru_cache(maxsize=1)
def pipeline_revision() -> str:
    """Content identity of the code that grades and exports a board.

    Source fingerprints answer "did the user's design change?". They do not
    answer "did the compiler driver or independent verifier change?". The
    latter omission reused old pre-normalizer/pre-check artifacts after a
    pipeline fix, which made a stale board look current. Hash the runnable
    Python implementation so any pipeline change invalidates both the build
    short-circuit and export cache without relying on a human version bump.
    """

    package_dir = Path(__file__).resolve().parent
    roots: list[tuple[str, Path]] = [("circuitpy", package_dir)]
    verify_candidates = [
        package_dir.parent / "verifylib",  # vendored skill runtime
        *(
            ancestor / "packages" / "verify" / "src" / "verifylib"
            for ancestor in Path(__file__).resolve().parents
        ),
    ]
    verify_root = next((path for path in verify_candidates if path.is_dir()), None)
    if verify_root is not None:
        roots.append(("verifylib", verify_root))

    digest = hashlib.sha256()
    digest.update(b"circuitpy-pipeline-revision-v1\0")
    for label, root in roots:
        for path in sorted(root.rglob("*.py"), key=lambda item: item.as_posix()):
            relative = path.relative_to(root).as_posix()
            digest.update(label.encode("utf-8") + b"/" + relative.encode("utf-8") + b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    if verify_root is None:
        digest.update(b"verifylib/unavailable\0")
    return digest.hexdigest()

_MIRROR_SKIP_DIRS = {
    ".circuit",
    ".claude",
    ".git",
    ".tscircuit",
    "__pycache__",
    "dist",
    "inputs",
    "node_modules",
}
_MIRROR_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".json"}

# tscircuit's render loop catches rejected async effects, logs them, and then
# continues the build.  The CLI can consequently exit 0 and write a partial
# circuit.json.  Usually it also serializes a ``pcb_autorouting_error``, but
# that serialization is a second async path and is not guaranteed.  Treat the
# CLI log as a cross-check on the artifact, never as a replacement for it.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ASYNC_EFFECT_ERROR_RE = re.compile(
    r"Async effect error in (?P<phase>[^\n]*?)\s+[\"'](?P<effect>[^\"']+)[\"']\s*:",
    re.IGNORECASE,
)


def _async_effect_failures(output: str) -> list[tuple[str, str, str]]:
    """Return every rejected tscircuit async effect in CLI order.

    tscircuit catches these promise rejections, logs them, and can still print
    ``Build complete`` with exit code zero. Autorouting has a recoverable
    artifact-reconciliation path below; every other effect is a compile
    failure because its partial output has no schema-safe generic error element.
    """

    clean = _ANSI_ESCAPE_RE.sub("", output).replace("\r", "\n")
    lines = clean.splitlines()
    failures: list[tuple[str, str, str]] = []
    for index, line in enumerate(lines):
        match = _ASYNC_EFFECT_ERROR_RE.search(line)
        if match is None:
            continue
        detail = "the async effect rejected without an error message"
        for candidate in lines[index + 1 :]:
            if _ASYNC_EFFECT_ERROR_RE.search(candidate):
                break
            candidate = candidate.strip()
            if not candidate or candidate.startswith("at "):
                continue
            if candidate.startswith("Error:"):
                candidate = candidate[len("Error:") :].strip()
            detail = candidate[:800]
            break
        failures.append(
            (match.group("phase").strip(), match.group("effect").strip(), detail)
        )
    return failures


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def _parts_engine_off() -> bool:
    return (os.environ.get(PARTS_ENGINE_ENV) or "").strip().lower() in {
        "off",
        "0",
        "false",
        "no",
    }


def _async_autorouting_failure_detail(output: str) -> str | None:
    """Return the first rejected autorouting effect from CLI output.

    The pinned tscircuit core logs ``Async effect error in PcbTraceRender
    \"autorouting\":`` followed by the rejected promise's stack, but catches
    the rejection and lets the process complete successfully.  ANSI and
    carriage-return progress rendering are normalized before matching.
    """

    for _phase, effect, detail in _async_effect_failures(output):
        if effect.lower() == "autorouting":
            return detail
    return None


def _refuse_non_routing_async_failures(output: str, entry: str) -> None:
    """Fail a build whose non-router async stage rejected.

    There is no generic circuit-json error element that survives the exporter,
    so persisting a made-up element would trade one silent partial artifact for
    another invalid one. Autorouting is reconciled separately using its real
    schema element; every other rejected effect stops before export.
    """

    failures = [
        failure
        for failure in _async_effect_failures(output)
        if failure[1].lower() != "autorouting"
    ]
    if not failures:
        return
    phase, effect, detail = failures[0]
    extra = len(failures) - 1
    suffix = f" (+{extra} more async failure(s))" if extra else ""
    raise CompileError(
        "tscircuit swallowed a non-routing asynchronous failure while "
        f"building {entry}: {phase} / {effect}: {detail}{suffix}. "
        "Refusing the partial circuit.json"
    )


def _serialize_missing_async_autorouting_error(
    elements: list, cli_output: str
) -> bool:
    """Reconcile swallowed async routing failures with ``circuit.json``.

    Returns ``True`` after appending a blocking ``pcb_autorouting_error``.
    Existing serialized copies are left alone, so the normal tscircuit error
    path is unchanged and validation does not report the same failure twice.
    """

    detail = _async_autorouting_failure_detail(cli_output)
    if detail is None:
        return False

    normalized_output = _ANSI_ESCAPE_RE.sub("", cli_output).replace("\r", "\n")
    existing_ids: set[str] = set()
    for element in elements:
        if not isinstance(element, dict):
            continue
        element_id = element.get("pcb_autorouting_error_id")
        if isinstance(element_id, str):
            existing_ids.add(element_id)
        if element.get("type") != "pcb_autorouting_error":
            continue
        message = element.get("message")
        if isinstance(message, str) and message.strip() in normalized_output:
            return False

    index = 0
    while f"pcb_autorouting_error_circuitpy_{index}" in existing_ids:
        index += 1
    error_id = f"pcb_autorouting_error_circuitpy_{index}"
    elements.append(
        {
            "type": "pcb_autorouting_error",
            "pcb_autorouting_error_id": error_id,
            "pcb_error_id": f"pcb_error_circuitpy_async_autorouting_{index}",
            "error_type": "pcb_autorouting_error",
            "message": (
                "tscircuit reported an asynchronous autorouting failure but "
                "omitted it from circuit.json; the generated artifact may be "
                f"partial. {detail}"
            ),
        }
    )
    return True


def _reconcile_async_autorouting_failure(
    elements: list, cli_output: str, circuit_json_path: Path
) -> bool:
    """Persist an output-only autorouting failure into the compiled artifact."""

    if not _serialize_missing_async_autorouting_error(elements, cli_output):
        return False
    # Downstream checks and exporters independently reopen this path. Persist
    # the blocker so every consumer sees the same fail-closed artifact, rather
    # than only the in-memory scan.
    try:
        circuit_json_path.write_text(
            json.dumps(elements, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        raise CompileError(
            "tscircuit swallowed an asynchronous autorouting failure and its "
            f"blocking error could not be serialized: {exc}"
        ) from exc
    return True


# ---------------------------------------------------------------------------
# Autorouter effort escalation.
# ---------------------------------------------------------------------------
#
# The router has an effort dial — ``<board autorouterEffortLevel>``, one of
# "1x" | "2x" | "5x" | "10x" | "100x". An early terminal-keyboard experiment
# appeared to improve at ``5x``, but the core route-cache key omitted effort
# and pipeline configuration, so that comparison was not controlled and is
# explicitly withdrawn in docs/lessons.md. The patched toolchain now keys all
# routing inputs and this stage clears its private cache as defense in depth.
#
# The wrong fix is a higher fixed default: it would tax every simple
# three-block board with twelve wasted minutes for a routing problem it does
# not have. The right one is a ladder — build at the normal effort, and only
# when the verdict comes back with *routing-class* errors, rebuild once at a
# higher effort before admitting defeat. The user gets one shot; the pipeline
# is allowed to try harder internally first.
#
# Two things this must not become:
#
# * **Silent.** A seventeen-minute build with no explanation is its own kind of
#   bad, so the attempt is reported in the sidecar (``build.autorouterEffort``,
#   ``build.blockingByAttempt``) and in the stdout result.
# * **Unbounded.** Exactly one escalation, one level, one timeout. If it does
#   not help, the cheaper result stands.
#
# The retry remains useful only as a bounded alternate candidate: it is kept
# when its independently compiled, parsed artifact has strictly fewer blocking
# errors. Effort is not a quality guarantee and may be slower or worse.

ROUTING_ESCALATION_ENV = "CIRCUIT_ROUTING_ESCALATION"
#: One bounded alternate candidate. 10x/100x exist, but their cold-route time
#: cost is not appropriate for an automatic chat build.
ROUTING_ESCALATION_EFFORT = "5x"
#: A hard subprocess bound; a solver that cannot finish is a failed candidate.
ROUTING_ESCALATION_TIMEOUT_S = 1500.0

#: Errors whose fix is "route it differently" — the only class a harder router
#: pass can address. A placement overlap or a missing footprint is not here:
#: escalating on those would burn twelve minutes to reproduce the same verdict.
ROUTING_ERROR_KINDS = frozenset(
    {
        "pcb_autorouting_error",
        "pcb_trace_error",
        "pcb_trace_missing_error",
        "pcb_port_not_connected_error",
        "pcb_port_not_matched_error",
        "pcb_trace_not_connected_error",
        "pcb_trace_clearance_error",
        # A track threaded 0.115mm past a drill is a routing choice: the router
        # does not model holes and takes the shortest path through the gap.
        # This is the finding that blocked all three example boards.
        "dfm_hole_clearance",
        "dfm_trace_width",
        "dfm_trace_clearance",
    }
)

_BOARD_TAG = re.compile(r"<board\b")
_EFFORT_LITERAL = re.compile(
    r"\bautorouterEffortLevel\s*=\s*['\"](1x|2x|5x|10x|100x)['\"]"
)
_ROUTING_EFFORT_VALUES = frozenset(
    {"default", "disabled", "authored", "1x", "2x", "5x", "10x", "100x"}
)


def _routing_escalation_off() -> bool:
    return (os.environ.get(ROUTING_ESCALATION_ENV) or "").strip().lower() in {
        "off",
        "0",
        "false",
        "no",
    }


def _routing_blockers(warnings: Sequence[dict]) -> list[dict]:
    """The blocking warnings a harder routing pass could plausibly clear."""
    return [
        w
        for w in warnings
        if w.get("severity") == "error" and w.get("kind") in ROUTING_ERROR_KINDS
    ]


def _board_opening_tag(text: str) -> str | None:
    """Return the first complete JSX ``<board ...>`` opening tag.

    A regex ending at the first ``>`` is not sufficient for JSX: comparison
    operators and arrow functions are legal inside braced prop expressions.
    Scan quotes and brace depth so effort policy is derived from the actual
    board tag rather than an accidentally truncated prefix.
    """

    match = _BOARD_TAG.search(text)
    if match is None:
        return None
    brace_depth = 0
    quote: str | None = None
    escaped = False
    for index in range(match.start(), len(text)):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == ">" and brace_depth == 0:
            return text[match.start() : index + 1]
    return None


def _routing_disabled_control(tag: str) -> str | None:
    """Classify an authored ``routingDisabled`` board prop.

    ``disabled`` is reserved for a definite true value. Literal false and the
    supported component-wrapper idiom ``props.routingDisabled ?? false`` are
    routed by the canonical zero-prop default export, so they leave the normal
    bounded effort policy enabled. Other dynamic expressions are ``authored``:
    the pipeline cannot prove their runtime value and must not override them.
    """

    match = re.search(r"\broutingDisabled\b", tag)
    if match is None:
        return None
    value = tag[match.end() :].lstrip()
    if not value.startswith("="):
        return "disabled"
    value = value[1:].lstrip()
    if re.match(r"(?:\{\s*true\s*\}|['\"]true['\"])", value):
        return "disabled"
    if re.match(r"(?:\{\s*false\s*\}|['\"]false['\"])", value):
        return None
    if re.match(r"\{[^{}]*\?\?\s*false\s*\}", value, re.S):
        return None
    return "authored"


def _source_routing_effort(board_source: Path) -> str:
    """Describe the effort already authored on the board's opening tag."""

    try:
        text = board_source.read_text(encoding="utf-8")
    except OSError:
        return "default"
    tag = _board_opening_tag(text)
    if tag is None:
        return "default"
    disabled = _routing_disabled_control(tag)
    if disabled is not None:
        return disabled
    literal = _EFFORT_LITERAL.search(tag)
    if literal is not None:
        return literal.group(1)
    if "autorouterEffortLevel" in tag:
        return "authored"
    # A spread can supply either control after the injected prop. Its runtime
    # value is unknowable from static source, so preserve the author's policy.
    if re.search(r"\{\s*\.\.\.", tag):
        return "authored"
    return "default"


def _routing_attempt_evidence(
    *,
    effort: str,
    warnings: Sequence[dict],
    circuit_json_path: Path,
) -> dict[str, object]:
    """Content-addressed evidence for one completed routing candidate.

    Counts alone cannot prove that a nominally different effort produced an
    independently compiled artifact.  The exact circuit hash and parsed kind
    histogram make the comparison reviewable without retaining a second full
    manufacturing packet.  Failed/timed-out candidates are recorded by the
    caller with ``status=failed`` because they have no trustworthy artifact.
    """

    blocking = [warning for warning in warnings if warning.get("severity") == "error"]
    counts: dict[str, int] = {}
    for warning in blocking:
        kind = str(warning.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "effort": effort,
        "status": "completed",
        "circuitSha256": export_cache.sha256_file(circuit_json_path),
        "blocking": len(blocking),
        "routingBlocking": len(_routing_blockers(blocking)),
        "blockingKinds": dict(sorted(counts.items())),
    }


def routing_attempt_evidence_error(
    build: object,
    *,
    circuit_json_path: Path | None = None,
) -> str | None:
    """Return why sidecar routing-attempt evidence is not trustworthy.

    This is shared by the example ratchet and review-packet publisher.  It
    accepts a failed alternate candidate (there is intentionally no artifact
    for one), but every completed candidate is content-addressed and its
    parsed blocker summary must agree with ``blockingByAttempt``.  When the
    selected board artifact is available, its bytes must equal the completed
    record for ``autorouterEffort``.
    """

    if not isinstance(build, dict):
        return "build (missing or not an object)"
    selected_effort = build.get("autorouterEffort")
    if not isinstance(selected_effort, str) or not selected_effort:
        return "build.autorouterEffort (missing or invalid)"
    attempts = build.get("attempts")
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or attempts not in {1, 2}
    ):
        return "build.attempts (must be one primary and at most one retry)"
    blockers = build.get("blockingByAttempt")
    if not isinstance(blockers, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in blockers
    ):
        return "build.blockingByAttempt (missing or invalid)"
    evidence = build.get("attemptEvidence")
    if not isinstance(evidence, list) or len(evidence) != attempts:
        return "build.attemptEvidence (must contain one record per attempt)"

    completed: list[dict[str, object]] = []
    for index, record in enumerate(evidence):
        prefix = f"build.attemptEvidence[{index}]"
        if not isinstance(record, dict):
            return f"{prefix} (not an object)"
        effort = record.get("effort")
        if effort not in _ROUTING_EFFORT_VALUES:
            return f"{prefix}.effort (missing or invalid)"
        status = record.get("status")
        if status == "failed":
            completed_only = {
                "circuitSha256",
                "blocking",
                "routingBlocking",
                "blockingKinds",
            }
            if completed_only & record.keys():
                return f"{prefix} failed record carries completed-artifact fields"
            continue
        if status != "completed":
            return f"{prefix}.status (must be completed or failed)"
        digest = record.get("circuitSha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return f"{prefix}.circuitSha256 (missing or invalid)"
        blocking = record.get("blocking")
        routing_blocking = record.get("routingBlocking")
        if (
            isinstance(blocking, bool)
            or not isinstance(blocking, int)
            or blocking < 0
        ):
            return f"{prefix}.blocking (missing or invalid)"
        if (
            isinstance(routing_blocking, bool)
            or not isinstance(routing_blocking, int)
            or routing_blocking < 0
            or routing_blocking > blocking
        ):
            return f"{prefix}.routingBlocking (missing or invalid)"
        kinds = record.get("blockingKinds")
        if not isinstance(kinds, dict) or any(
            not isinstance(kind, str)
            or not kind
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            for kind, count in kinds.items()
        ):
            return f"{prefix}.blockingKinds (missing or invalid)"
        if sum(kinds.values()) != blocking:
            return f"{prefix}.blockingKinds (counts do not equal blocking)"
        expected_routing_blocking = sum(
            count for kind, count in kinds.items() if kind in ROUTING_ERROR_KINDS
        )
        if routing_blocking != expected_routing_blocking:
            return f"{prefix}.routingBlocking does not match blockingKinds"
        completed.append(record)

    primary = evidence[0]
    if primary.get("status") != "completed":
        return "build.attemptEvidence[0] must be the completed primary attempt"
    if attempts == 2:
        alternate = evidence[1]
        if primary.get("effort") != "default":
            return "a retry is permitted only after the default primary attempt"
        if int(primary["routingBlocking"]) <= 0:
            return "a retry requires routing blockers in the primary attempt"
        if alternate.get("effort") != ROUTING_ESCALATION_EFFORT:
            return f"the sole retry must use {ROUTING_ESCALATION_EFFORT}"
        if alternate.get("status") == "completed":
            expected_selected = (
                ROUTING_ESCALATION_EFFORT
                if int(alternate["blocking"]) < int(primary["blocking"])
                else "default"
            )
        else:
            expected_selected = "default"
    else:
        expected_selected = str(primary.get("effort"))
    if selected_effort != expected_selected:
        return "build.autorouterEffort does not select the bounded winning attempt"

    if [record["blocking"] for record in completed] != blockers:
        return "build.blockingByAttempt does not match completed attempt evidence"
    selected = [
        record for record in completed if record.get("effort") == selected_effort
    ]
    if len(selected) != 1:
        return "build.autorouterEffort does not select exactly one completed attempt"
    if circuit_json_path is not None:
        try:
            selected_sha = export_cache.sha256_file(circuit_json_path)
        except OSError as exc:
            return f"selected circuit artifact is unreadable: {exc}"
        if selected[0].get("circuitSha256") != selected_sha:
            return "selected circuit artifact does not match routing attempt evidence"
    return None


def _set_autorouter_effort(board_source: Path, effort: str) -> bool:
    """Add ``autorouterEffortLevel`` to the mirrored board source.

    Only ever touches the copy inside ``.circuit/build/`` — the user's file is
    never rewritten, so "generated files are never hand-edited, nothing is
    overwritten" still holds. Returns False when the author already set the
    prop (their choice wins) or when the file has no ``<board>`` tag.
    """
    try:
        text = board_source.read_text(encoding="utf-8")
    except OSError:
        return False
    if _source_routing_effort(board_source) != "default":
        return False
    patched, count = _BOARD_TAG.subn(
        f'<board autorouterEffortLevel="{effort}"', text, count=1
    )
    if not count:
        return False
    try:
        board_source.write_text(patched, encoding="utf-8")
    except OSError:
        return False
    return True


def _stash_completed_build_for_retry(built_dir: Path) -> Path:
    """Move the next compile onto an empty artifact path while preserving attempt 1.

    ``tscircuit-cli`` may return nonzero without raising, so leaving the first
    ``circuit.json`` in place lets a failed retry look completed. Copy the
    whole output for restoration, then remove the live directory before the
    retry process starts.
    """

    kept = built_dir.with_name(built_dir.name + "__attempt1")
    staged = built_dir.with_name(built_dir.name + "__attempt1_staged")
    try:
        shutil.rmtree(kept, ignore_errors=True)
        shutil.rmtree(staged, ignore_errors=True)
        shutil.copytree(built_dir, staged)
        os.replace(staged, kept)
        shutil.rmtree(built_dir)
    except OSError as exc:
        shutil.rmtree(staged, ignore_errors=True)
        raise ToolchainError(
            f"could not isolate the first routing artifact before retry: {exc}"
        ) from exc
    if built_dir.exists():
        raise ToolchainError(
            "could not isolate the first routing artifact before retry: "
            f"{built_dir} still exists"
        )
    return kept


def _clear_tscircuit_route_cache(build_work_dir: Path) -> bool:
    """Remove the CLI's derived local-router cache before a changed retry.

    The pinned core used to key this cache from SimpleRouteJson alone. Router
    strategy, effort, and several solver parameters were omitted, so a retry
    could silently receive attempt 1's copper. The toolchain patch fixes the
    key, but clearing this private build cache is cheap defense in depth and
    also protects any future retry dimension that upstream forgets to key.

    Only ``<private build mirror>/.tscircuit/cache`` is touched. Project-local
    caches are excluded by :func:`_mirror_project` and are never removed.
    """

    cache_dir = build_work_dir / ".tscircuit" / "cache"
    if not cache_dir.exists() and not cache_dir.is_symlink():
        return False
    try:
        if cache_dir.is_symlink() or cache_dir.is_file():
            cache_dir.unlink()
        else:
            shutil.rmtree(cache_dir)
    except OSError as exc:
        raise ToolchainError(
            f"could not clear derived tscircuit route cache before retry: {exc}"
        ) from exc
    return True


# ---------------------------------------------------------------------------
# Project discovery.
# ---------------------------------------------------------------------------


def _resolve_source(source_path: Path) -> Path:
    if source_path.is_dir():
        script_path = source_path / "boards" / "main.tsx"
        if not script_path.is_file():
            raise ProjectShapeError(
                f"directory input must contain boards/main.tsx: {source_path}"
            )
        return script_path
    if not source_path.is_file():
        raise ProjectShapeError(f"board source not found: {source_path}")
    if source_path.suffix != ".tsx":
        raise ProjectShapeError(
            f"board source must be a .tsx file or a project directory "
            f"(got {source_path.name})"
        )
    return source_path


def _find_project_root(script_path: Path) -> Path:
    """The nearest ancestor directory containing ``product.json``."""
    for candidate in (script_path.parent, *script_path.parent.parents):
        if (candidate / "product.json").is_file():
            return candidate
    raise ProjectShapeError(
        f"no product.json found in any parent of {script_path} — a circuit "
        "project root must contain the product definition"
    )


# ---------------------------------------------------------------------------
# Canonical sidecar JSON.
# ---------------------------------------------------------------------------


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _current_toolchain_block() -> dict[str, str]:
    """Sidecar ``toolchain`` block: kicadCli omitted when absent."""
    return {
        key: value for key, value in toolchain.versions().items() if value is not None
    }


# ---------------------------------------------------------------------------
# Mirror-copy build workspace (.circuit/build/<stem>-<pid>/).
# ---------------------------------------------------------------------------


def _mirror_project(project_root: Path, work: Path) -> None:
    """Copy the project's source surface (TSX/TS/JSON incl. blocks/, minus
    built artifacts) into the work dir so the CLI's ``dist/`` and caches stay
    inside ``.circuit/`` — the workspace the snapshotter watches never grows
    build litter."""
    for root, dirs, files in os.walk(project_root):
        root_path = Path(root)
        dirs[:] = [
            d
            for d in dirs
            if d not in _MIRROR_SKIP_DIRS
            and not d.endswith("_review")
            and not d.endswith("_fab")
        ]
        for name in files:
            source = root_path / name
            if source.suffix not in _MIRROR_SUFFIXES:
                continue
            if name.endswith(OUTPUT_SUFFIX) or name.endswith(".board.json"):
                continue
            rel = source.relative_to(project_root)
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _cli_export(
    work: Path, circuit_json_path: Path, fmt: str, out_name: str
) -> Path:
    """``tscircuit-cli export`` writes ``-o`` **next to the input file** (a
    verified CLI behavior — absolute paths get mangled), so we pass a bare
    filename and collect the artifact from the input's directory."""
    result = toolchain.run_cli(
        ["export", str(circuit_json_path), "-f", fmt, "-o", out_name],
        cwd=work,
        timeout=EXPORT_TIMEOUT_S,
        check=False,
    )
    produced = circuit_json_path.parent / out_name
    if not produced.is_file():
        tail = result.output.strip()[-800:]
        raise RuntimeError(
            f"export -f {fmt} produced no {out_name} (exit {result.returncode}): {tail}"
        )
    return produced


# ---------------------------------------------------------------------------
# The public entry point.
# ---------------------------------------------------------------------------


def build_board(
    source_path: Path | str,
    output_path: Path | str,
    *,
    fab: str | None = None,
    max_build_s: float | None = None,
) -> dict[str, object]:
    """Build one board per contract §1 and return the snake_case dict
    mirroring the CircuitcodeResult success fields (§3)."""
    source_p = Path(source_path).expanduser().resolve()
    output_p = Path(output_path).expanduser().resolve()
    if not output_p.name.endswith(OUTPUT_SUFFIX):
        raise ProjectShapeError(
            f"output_path must end in {OUTPUT_SUFFIX} (got {output_p.name})"
        )
    stem = output_p.name[: -len(OUTPUT_SUFFIX)]

    script_path = _resolve_source(source_p)
    project_root = _find_project_root(script_path)
    try:
        rel_entry = script_path.resolve().relative_to(project_root)
    except ValueError as exc:
        raise ProjectShapeError(
            f"board source {script_path} is outside its project root {project_root}"
        ) from exc

    product = spec_mod.load_product(project_root)
    parts = spec_mod.load_parts(project_root)
    fab_id = (
        fab
        if fab is not None
        else (os.environ.get(FAB_ENV, "").strip() or DEFAULT_FAB)
    )
    profile = fab_mod.get_profile(fab_id)

    identity = board_source_hash(script_path, project_root)

    # A generated board imports frozen golden blocks by relative path. Refuse
    # a missing, partial, locally modified, or unselected snapshot before any
    # tscircuit process runs; otherwise an unlocked block copy could still
    # produce a source-fresh sidecar and be mistaken for reproducible evidence.
    validate_project_snapshot(
        project_root,
        imported_paths=(source_file.path for source_file in identity.files),
    )

    # Safety envelope — refused at spec time, before any toolchain process.
    source_files = [
        project_root / f.path if not f.path.startswith("/") else Path(f.path)
        for f in identity.files
    ]
    spec_mod.preflight_safety(source_files, project_root, product)

    boards_dir = output_p.parent
    review_dir = boards_dir / f"{stem}_review"
    fab_dir = boards_dir / f"{stem}_fab"
    sidecar_path = boards_dir / f"{stem}.board.json"

    if not _truthy(os.environ.get(FORCE_ENV)):
        prior = _unchanged_prior_result(
            sidecar_path=sidecar_path,
            identity=identity,
            output_p=output_p,
            boards_dir=boards_dir,
            fab_dir=fab_dir,
        )
        if prior is not None:
            return prior

    progress = status_mod.BuildStatus(project_root, stem=stem)
    progress.stage("compile")

    # -- Stage 0: compile in the mirror work dir. ----------------------------
    work = project_root / ".circuit" / "build" / f"{stem}-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    _mirror_project(project_root, work)
    # Anchor the CLI's output resolution. `tscircuit-cli` places `dist/` beside
    # the nearest ancestor package.json; with none in the work dir it can pick
    # a directory far outside the project (observed: /Users/d/code), leaving us
    # to report a COMPILE_ERROR for a board that built perfectly. A private
    # stub stops the search here. Board projects intentionally carry no
    # node_modules — the toolchain is repo-level — so this file exists purely
    # as a boundary marker.
    anchor = work / "package.json"
    if not anchor.exists():
        anchor.write_text(
            json.dumps(
                {"name": "circuit-build-workspace", "private": True, "version": "0.0.0"},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    build_args = ["build", rel_entry.as_posix(), "--schematic-png", "--pcb-png"]
    if _parts_engine_off():
        build_args.append("--disable-parts-engine")
    built_dir = work / "dist" / rel_entry.parent / rel_entry.stem
    built_circuit_json = built_dir / "circuit.json"

    def _compile_once(timeout_s: float) -> list:
        try:
            build_result = toolchain.run_cli(
                build_args, cwd=work, timeout=timeout_s, check=False
            )
        except TimeoutError as exc:
            raise ToolchainError(str(exc)) from exc
        except RuntimeError as exc:
            raise ToolchainError(str(exc)) from exc

        if not built_circuit_json.is_file():
            tail = build_result.output.strip()[-800:]
            # A build that logged success but left nothing here means the CLI
            # resolved `dist/` somewhere else — it walks up to the nearest
            # package.json, so without an anchor it can escape the work dir
            # entirely and we would report a compile failure for a board that
            # compiled fine. Say which of the two actually happened.
            if "✓" in build_result.output or "Done" in build_result.output:
                raise CompileError(
                    f"tscircuit reported success for {rel_entry.as_posix()} but "
                    f"wrote no circuit.json under {built_dir} — the CLI resolved "
                    f"its output directory outside the work dir (it walks up to "
                    f"the nearest package.json). Output tail: {tail or 'none'}"
                )
            raise CompileError(
                f"tscircuit eval failed for {rel_entry.as_posix()} "
                f"(exit {build_result.returncode}): {tail or 'no output'}"
            )
        try:
            elements = json.loads(built_circuit_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CompileError(f"built circuit.json unreadable: {exc}") from exc
        if not isinstance(elements, list):
            raise CompileError(
                f"built circuit.json is not an element array "
                f"(got {type(elements).__name__})"
            )
        _reconcile_async_autorouting_failure(
            elements, build_result.output, built_circuit_json
        )
        _refuse_non_routing_async_failures(
            build_result.output, rel_entry.as_posix()
        )
        return elements

    def _scan(elements: list) -> list[dict]:
        """Stages 1, 2 and 4a — everything judgeable straight off the geometry,
        and therefore everything the escalation decision can be based on."""
        found: list[dict] = []
        found.extend(checks.harvest_circuit_json(elements))
        found.extend(checks.run_tscircuit_checks(built_circuit_json))
        found.extend(checks.iou_warnings(elements, profile))
        found.extend(checks.dfm_warnings(elements, product, profile))
        return found

    first_timeout = (
        max_build_s if max_build_s is not None else DEFAULT_BUILD_TIMEOUT_S
    )
    circuit_json = _compile_once(first_timeout)

    progress.stage("scan")
    warnings: list[dict] = _scan(circuit_json)

    # -- Stage 0b: escalate the router once, if routing is what is wrong. ----
    # See the ROUTING_ESCALATION notes at the top of this module. One rung, one
    # rebuild, and the cheaper result stands unless the harder one is strictly
    # better — escalation may never make a board worse.
    entry_copy = work / rel_entry
    primary_effort = _source_routing_effort(entry_copy)
    routing_effort = primary_effort
    blocking_by_attempt = [
        sum(1 for w in warnings if w.get("severity") == "error")
    ]
    attempt_evidence: list[dict[str, object]] = [
        _routing_attempt_evidence(
            effort=primary_effort,
            warnings=warnings,
            circuit_json_path=built_circuit_json,
        )
    ]
    escalation_note: dict | None = None
    if _routing_blockers(warnings) and not _routing_escalation_off():
        if _set_autorouter_effort(entry_copy, ROUTING_ESCALATION_EFFORT):
            retry_evidence: dict[str, object] = {
                "effort": ROUTING_ESCALATION_EFFORT,
                "status": "started",
            }
            attempt_evidence.append(retry_evidence)
            # Keep attempt 1's whole output directory — circuit.json *and* the
            # review PNGs. Every downstream stage reads from built_dir, so
            # "keep the better attempt" has to mean the files too, not just
            # the parsed elements. Copying beats rebuilding: a third compile
            # to undo a retry would cost more than the retry did.
            kept = built_dir.with_name(built_dir.name + "__attempt1")

            progress.stage("compile")
            budget = max(
                ROUTING_ESCALATION_TIMEOUT_S,
                max_build_s if max_build_s is not None else 0.0,
            )
            retry_json: list | None = None
            retry_warnings: list[dict] = []
            try:
                _stash_completed_build_for_retry(built_dir)
                _clear_tscircuit_route_cache(work)
                retry_json = _compile_once(budget)
                progress.stage("scan")
                retry_warnings = _scan(retry_json)
            except (ToolchainError, CompileError) as exc:
                # Escalation is best-effort by construction: attempt 1 is
                # already a real answer, so a failed retry is information,
                # never a build failure.
                escalation_note = checks.check_failed(
                    f"the {ROUTING_ESCALATION_EFFORT} routing retry did not "
                    f"finish ({exc}); reporting the default-effort build"
                )
                retry_evidence["status"] = "failed"

            keep_retry = False
            if retry_json is not None:
                retry_evidence.clear()
                retry_evidence.update(
                    _routing_attempt_evidence(
                        effort=ROUTING_ESCALATION_EFFORT,
                        warnings=retry_warnings,
                        circuit_json_path=built_circuit_json,
                    )
                )
                retry_blocking = sum(
                    1 for w in retry_warnings if w.get("severity") == "error"
                )
                blocking_by_attempt.append(retry_blocking)
                keep_retry = retry_blocking < blocking_by_attempt[0]

            if keep_retry:
                circuit_json = retry_json  # type: ignore[assignment]
                warnings = retry_warnings
                routing_effort = ROUTING_ESCALATION_EFFORT
                shutil.rmtree(kept, ignore_errors=True)
            else:
                # Escalation may never make a board worse: put attempt 1's
                # artifacts back and report its verdict.
                if kept.exists():
                    shutil.rmtree(built_dir, ignore_errors=True)
                    shutil.move(str(kept), str(built_dir))

    progress.stage("dfm")
    if escalation_note is not None:
        warnings.append(escalation_note)

    build_block: dict[str, object] = {
        "autorouterEffort": routing_effort,
        "attempts": len(attempt_evidence),
        "blockingByAttempt": blocking_by_attempt,
        "attemptEvidence": attempt_evidence,
    }

    tool_versions = toolchain.versions()
    circuit_json_sha = export_cache.sha256_file(built_circuit_json)

    def _cached(fmt: str, out_name: str, suffix: str) -> Path:
        key = export_cache.export_key(
            circuit_json_sha=circuit_json_sha,
            kind=fmt,
            versions=tool_versions,
            fab=profile.id,
            pipeline_revision=pipeline_revision(),
        )
        hit = export_cache.lookup(project_root, key, suffix)
        if hit is not None:
            target = built_circuit_json.parent / out_name
            shutil.copy2(hit, target)
            return target
        produced = _cli_export(work, built_circuit_json, fmt, out_name)
        export_cache.store(project_root, key, suffix, produced)
        return produced

    # -- Exporter packet (BOM/CPL source; gerber fallback). ------------------
    ts_gerbers_zip: Path | None = None
    bom_rows: list[dict] = []
    cpl_text = ""
    try:
        ts_gerbers_zip = _cached("gerbers", "ts-gerbers.zip", ".gerbers.zip")
        with zipfile.ZipFile(ts_gerbers_zip) as packet:
            names = set(packet.namelist())
            if "bom.csv" in names:
                bom_rows = fab_mod.parse_exporter_bom(
                    packet.read("bom.csv").decode("utf-8", "replace")
                )
            if "pick_and_place.csv" in names:
                cpl_text = packet.read("pick_and_place.csv").decode("utf-8", "replace")
    except (RuntimeError, TimeoutError, OSError, zipfile.BadZipFile) as exc:
        has_errors = any(w.get("severity") == "error" for w in warnings)
        if not has_errors:
            raise ExportError(f"gerber/BOM export failed: {exc}") from exc
        warnings.append(
            checks.check_failed(f"fab packet export skipped (board has errors): {exc}")
        )
    dnp_designators = fab_mod.do_not_place_designators(circuit_json)
    bom_rows = fab_mod.exclude_designators_from_bom(bom_rows, dnp_designators)
    cpl_text = fab_mod.exclude_designators_from_cpl(cpl_text, dnp_designators)
    bom_rows = fab_mod.merge_parts_lock(bom_rows, parts)

    # -- Stage 4b: BOM gate. -------------------------------------------------
    if bom_rows:
        warnings.extend(checks.bom_gate(bom_rows, assembly=product.assembly))
    elif product.assembly:
        warnings.append(
            checks.check_failed("assembly requested but no BOM rows were produced")
        )

    # -- Stage 4c: the standalone checks (packages/verify). ------------------
    # Assembly/DFA, net-class current capacity, the DC operating point, the
    # electrical design review and thermal dissipation. All five read the
    # compiled circuit.json, share no code with anything above them, and cost
    # about a second between them on a 130-part board. The fab profile decides
    # which of their findings block `fab.ready` — see fab.apply_verify_policy.
    warnings.extend(
        verify_bridge.check_circuit_json(
            built_circuit_json,
            profile=profile,
            assembly_order=product.assembly,
            assembly_tier=product.assembly_tier,
            layout_intent=product.layout,
            power_intent=product.power_budget,
        )
    )

    progress.stage("substrate")

    # -- Stage 3 + 5: second substrate + shipping gerbers. -------------------
    gerber_source = "tscircuit"
    kicad_gerbers_zip: Path | None = None
    # These are consumed again while assembling the fab packet.  Keep their
    # lifetime independent of the optional KiCad branch: on a machine without
    # kicad-cli the branch is skipped, but the packet still has to finish and
    # report `unverified_gerbers` instead of crashing after the expensive
    # compile/router run.
    kicad_sch: Path | None = None
    kicad_pcb: Path | None = None
    if toolchain.kicad_cli_exe() is not None:
        try:
            kicad_sch = _cached("kicad_sch", "board.kicad_sch", ".kicad_sch")
            kicad_pcb = _cached("kicad_pcb", "board.kicad_pcb", ".kicad_pcb")
        except (RuntimeError, TimeoutError) as exc:
            warnings.append(checks.check_failed(f"kicad conversion failed: {exc}"))
        if kicad_pcb is not None:
            # Stage 3a: hold the converted board to the fab's floors before
            # anything reads it. `circuit-json-to-kicad` emits silkscreen text
            # at 0.2-0.67mm against JLCPCB's 1.0mm minimum and leaves the
            # stroke implicit, so KiCad plots it at 0.033mm — on every board it
            # has ever produced. This is the one place every board passes
            # through between the converter we do not own and the plotter, so
            # one change here fixes the whole catalogue. Idempotent.
            normalization = kicad_normalize.normalize_for_fab(kicad_pcb, profile)
            if normalization.changed:
                warnings.append(
                    {
                        "part": "board",
                        "kind": "kicad_normalized",
                        "detail": (
                            "the converted board was held to the fab's "
                            f"silkscreen floors before plotting: "
                            f"{normalization.summary()}"
                        ),
                        "severity": "info",
                    }
                )
            for note in normalization.notes:
                warnings.append(checks.check_failed(note))
            warnings.extend(normalization.unreadable_findings(profile))
        if kicad_sch is not None:
            erc_json = built_dir / "erc.json"
            try:
                toolchain.run_kicad(
                    [
                        "sch",
                        "erc",
                        "--format",
                        "json",
                        "--severity-all",
                        "--exit-code-violations",
                        "-o",
                        str(erc_json),
                        str(kicad_sch),
                    ],
                    timeout=KICAD_TIMEOUT_S,
                    ok_codes=(0, 5),
                )
                warnings.extend(
                    checks.parse_kicad_report(erc_json, kind="erc_violation")
                )
            except (RuntimeError, TimeoutError) as exc:
                warnings.append(checks.check_failed(f"kicad ERC failed: {exc}"))
        if kicad_pcb is not None:
            # Give kicad this fab's design rules before asking its opinion.
            # Without the project file KiCad grades the board against its own
            # stock defaults and buries the real findings (see
            # fab.kicad_project_json for the measured before/after).
            try:
                declared_clearance = product.layout.get("minCopperClearanceMm")
                fab_mod.write_kicad_project(
                    kicad_pcb,
                    profile,
                    min_clearance_mm=(
                        float(declared_clearance)
                        if isinstance(declared_clearance, (int, float))
                        else None
                    ),
                )
            except OSError as exc:
                warnings.append(
                    checks.check_failed(f"kicad project file not written: {exc}")
                )
            drc_json = built_dir / "drc.json"
            try:
                toolchain.run_kicad(
                    [
                        "pcb",
                        "drc",
                        "--schematic-parity",
                        "--all-track-errors",
                        "--format",
                        "json",
                        "--severity-all",
                        "--exit-code-violations",
                        "-o",
                        str(drc_json),
                        str(kicad_pcb),
                    ],
                    timeout=KICAD_TIMEOUT_S,
                    ok_codes=(0, 5),
                )
                warnings.extend(
                    checks.parse_kicad_report(drc_json, kind="drc_violation")
                )
            except (RuntimeError, TimeoutError) as exc:
                warnings.append(checks.check_failed(f"kicad DRC failed: {exc}"))
            # Shipping gerbers come from the converted board (the verified path).
            gerber_dir = built_dir / "kicad-gerbers"
            try:
                gerber_dir.mkdir(parents=True, exist_ok=True)
                toolchain.run_kicad(
                    _kicad_gerber_export_args(gerber_dir, kicad_pcb),
                    timeout=KICAD_TIMEOUT_S,
                )
                toolchain.run_kicad(
                    ["pcb", "export", "drill", "-o", str(gerber_dir) + os.sep, str(kicad_pcb)],
                    timeout=KICAD_TIMEOUT_S,
                )
                kicad_gerbers_zip = fab_mod.zip_directory_gerbers(
                    gerber_dir, built_dir / "kicad-gerbers.zip"
                )
                gerber_source = "kicad-cli"
            except (RuntimeError, TimeoutError, OSError) as exc:
                warnings.append(
                    checks.check_failed(f"kicad gerber export failed: {exc}")
                )
    else:
        warnings.append(checks.kicad_unavailable_warning())

    if gerber_source != "kicad-cli":
        warnings.append(
            {
                "part": "board",
                "kind": "unverified_gerbers",
                "detail": "gerbers were exported by tscircuit without kicad-cli "
                "verification — do not ship this packet (install KiCad and rebuild)",
                "severity": "warning",
            }
        )

    warnings = checks.dedupe(warnings)

    progress.stage("export")

    # -- Stage 5: write the fab packet. --------------------------------------
    shutil.rmtree(fab_dir, ignore_errors=True)
    glb_path: Path | None = None
    cpl_path: Path | None = None
    bom_path: Path | None = None
    gerbers_path: Path | None = None
    try:
        if kicad_gerbers_zip is not None:
            fab_dir.mkdir(parents=True, exist_ok=True)
            gerbers_path = fab_dir / profile.zip_name
            shutil.copy2(kicad_gerbers_zip, gerbers_path)
        elif ts_gerbers_zip is not None:
            gerbers_path = fab_mod.repackage_gerbers(
                ts_gerbers_zip, fab_dir / profile.zip_name
            )
        if bom_rows:
            bom_path = fab_mod.write_bom_csv(bom_rows, fab_dir / profile.bom_name, profile)
        if product.assembly and cpl_text:
            cpl_path = fab_mod.write_cpl_csv(cpl_text, fab_dir / profile.cpl_name, profile)
    except OSError as exc:
        raise ExportError(f"failed to write fab packet: {exc}") from exc
    try:
        glb_path = _cached("glb", profile.glb_name, ".glb")
        fab_dir.mkdir(parents=True, exist_ok=True)
        target = fab_dir / profile.glb_name
        shutil.copy2(glb_path, target)
        glb_path = target
    except (RuntimeError, TimeoutError, OSError):
        glb_path = None  # best-effort by contract

    # The enclosure brief: the exact facts the printed body needs, taken from
    # The KiCad project, zipped into the packet. We already convert the board
    # to `.kicad_sch`/`.kicad_pcb` to run the second-substrate check and were
    # throwing them away — but they are the only artifact in the packet a
    # person can open in a real EDA tool to review or edit the design. Gerbers
    # are for the fab; this is for the engineer.
    kicad_zip_path: Path | None = None
    if kicad_pcb is not None:
        try:
            kicad_zip_path = fab_dir / "kicad-project.zip"
            fab_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(kicad_zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(kicad_pcb, f"{stem}.kicad_pcb")
                project_file = kicad_pcb.with_suffix(".kicad_pro")
                if project_file.is_file():
                    bundle.write(project_file, f"{stem}.kicad_pro")
                if kicad_sch is not None and kicad_sch.is_file():
                    bundle.write(kicad_sch, f"{stem}.kicad_sch")
        except (OSError, zipfile.BadZipFile) as exc:
            kicad_zip_path = None
            warnings.append(
                checks.check_failed(f"kicad project not bundled: {exc}")
            )

    # the same geometry that produced the gerbers so the two cannot disagree.
    enclosure_path: Path | None = None
    try:
        enclosure_path = enclosure_mod.write_enclosure_spec(
            circuit_json, fab_dir / "enclosure.json", board_name=stem
        )
    except (OSError, ValueError) as exc:
        warnings.append(checks.check_failed(f"enclosure spec not written: {exc}"))

    # -- Stage 5b: the packet the fab actually receives. ---------------------
    # This cannot run any earlier: the zip does not exist until stage 5. It is
    # also the only check in the whole pipeline that inspects what JLCPCB is
    # sent rather than what we meant to send, so a bug in the export itself —
    # a missing layer, a drill in the wrong units, a footprint dropped between
    # the board and the plot — has nowhere else to be caught.
    if gerbers_path is not None and gerbers_path.is_file():
        warnings.extend(
            verify_bridge.check_packet(
                built_circuit_json,
                gerbers_path,
                profile=profile,
                assembly_order=product.assembly,
            )
        )

    warnings = checks.dedupe(warnings)
    ready = fab_mod.fab_ready(warnings, gerber_source)
    order_path: Path | None = None
    board_el = next(
        (
            e
            for e in circuit_json
            if isinstance(e, dict) and e.get("type") == "pcb_board"
        ),
        None,
    )
    width_mm = float((board_el or {}).get("width") or 0)
    height_mm = float((board_el or {}).get("height") or 0)
    layers = int((board_el or {}).get("num_layers") or product.layers)
    bom_block = fab_mod.bom_summary(bom_rows)
    if ready:
        try:
            order_path = fab_mod.write_order_md(
                fab_dir / profile.order_name,
                product_name=product.name,
                assembly=product.assembly,
                assembly_tier=product.assembly_tier,
                profile=profile,
                board_width_mm=width_mm,
                board_height_mm=height_mm,
                layers=layers,
                bom=bom_block,
            )
        except OSError as exc:
            raise ExportError(f"failed to write ORDER.md: {exc}") from exc

    progress.stage("render")

    # -- Stage 6: review images. ---------------------------------------------
    shutil.rmtree(review_dir, ignore_errors=True)
    try:
        review_written = review_mod.write_review(
            circuit_json_path=built_circuit_json,
            review_dir=review_dir,
            built_schematic_png=built_dir / "schematic.png",
            built_pcb_png=built_dir / "pcb.png",
            double_sided=review_mod.is_double_sided(circuit_json),
        )
    except (RuntimeError, TimeoutError) as exc:
        raise ExportError(f"failed to write review images: {exc}") from exc

    # -- Sidecar (camelCase canonical JSON), then the IR lands LAST. ---------
    artifacts: dict[str, str] = {
        "schematicPng": f"{stem}_review/_schematic.png",
        "pcbPng": f"{stem}_review/_pcb.png",
    }
    if gerbers_path is not None:
        artifacts["gerbers"] = f"{stem}_fab/{profile.zip_name}"
    if bom_path is not None:
        artifacts["bom"] = f"{stem}_fab/{profile.bom_name}"
    if cpl_path is not None:
        artifacts["cpl"] = f"{stem}_fab/{profile.cpl_name}"
    if order_path is not None:
        artifacts["order"] = f"{stem}_fab/{profile.order_name}"
    if glb_path is not None:
        artifacts["glb"] = f"{stem}_fab/{profile.glb_name}"
    if enclosure_path is not None:
        artifacts["enclosure"] = f"{stem}_fab/enclosure.json"
    if kicad_zip_path is not None:
        artifacts["kicadProject"] = f"{stem}_fab/kicad-project.zip"

    validation: dict[str, object] = {}
    if warnings:
        validation["warnings"] = warnings
    sidecar_payload: dict[str, object] = {
        "generator": GENERATOR_NAME,
        "generatorRevision": pipeline_revision(),
        "entryKind": "board",
        "source": {
            "kind": "tsx",
            "path": identity.source_path,
            "hash": identity.source_hash,
            "fingerprint": identity.source_fingerprint,
        },
        "board": {
            "path": output_p.name,
            "name": product.name,
            "widthMm": width_mm,
            "heightMm": height_mm,
            "layers": layers,
        },
        "toolchain": _current_toolchain_block(),
        # How the board was routed. Present on every build so a 17-minute
        # escalated build is visible rather than mysterious, and so a reader
        # can tell "clean at the default effort" from "clean only at 5x" —
        # the second is a board one upstream change away from failing.
        "build": build_block,
        "bom": bom_block,
        "fab": {
            "profile": profile.id,
            "ready": ready,
            "assembly": product.assembly,
            "assemblyTier": product.assembly_tier,
            "gerberSource": gerber_source,
            "packet": f"{stem}_fab/",
        },
        "validation": validation,
        "artifacts": artifacts,
    }
    try:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(_canonical_json(sidecar_payload), encoding="utf-8")
    except OSError as exc:
        raise ExportError(f"failed to write metadata sidecar: {exc}") from exc

    # Ordering rule: the sidecar is on disk; the circuit.json artifact of
    # record appears (and gets its mtime) last, so artifact_changed fires
    # after metadata is readable.
    try:
        if _is_same_filesystem(built_circuit_json, boards_dir):
            os.replace(built_circuit_json, output_p)
        else:  # pragma: no cover — project and .circuit share a disk
            shutil.copy2(built_circuit_json, output_p)
            built_circuit_json.unlink(missing_ok=True)
        os.utime(output_p, None)
    except OSError as exc:
        raise ExportError(f"failed to move circuit.json into place: {exc}") from exc

    shutil.rmtree(work, ignore_errors=True)

    result: dict[str, object] = {
        "circuit_json_path": str(output_p),
        "metadata_path": str(sidecar_path),
        "schematic_png": str(review_written["_schematic.png"]),
        "pcb_png": str(review_written["_pcb.png"]),
        "board": {"width_mm": width_mm, "height_mm": height_mm, "layers": layers},
        "bom": _bom_result_block(bom_block),
        "fab": {"profile": profile.id, "ready": ready, "packet_dir": str(fab_dir)},
        "build": {
            "autorouter_effort": build_block["autorouterEffort"],
            "attempts": build_block["attempts"],
            "blocking_by_attempt": build_block["blockingByAttempt"],
            "attempt_evidence": build_block["attemptEvidence"],
        },
        "warnings": warnings,
    }
    progress.finish(
        ok=True,
        detail=f"{sum(1 for w in warnings if w.get('severity') == 'error')} blocking",
    )
    return result


def _bom_result_block(bom_block: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {
        "lines": bom_block.get("lines", 0),
        "orderable": bom_block.get("orderable", 0),
    }
    if "estimatedCostUsd" in bom_block:
        out["estimated_cost_usd"] = bom_block["estimatedCostUsd"]
    return out


def _unchanged_prior_result(
    *,
    sidecar_path: Path,
    identity: BoardSourceHash,
    output_p: Path,
    boards_dir: Path,
    fab_dir: Path,
) -> dict[str, object] | None:
    """Return the §3-shaped result reconstructed from the existing sidecar
    when the prior build is provably still valid; None means build for real.
    Conservative on every doubt: unreadable sidecar, fingerprint or toolchain
    drift, or any missing artifact falls through to a full build."""
    try:
        prior = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(prior, dict) or prior.get("generator") != GENERATOR_NAME:
        return None
    if prior.get("generatorRevision") != pipeline_revision():
        return None
    source = prior.get("source") or {}
    if source.get("fingerprint") != identity.source_fingerprint:
        return None
    try:
        if (prior.get("toolchain") or {}) != _current_toolchain_block():
            return None
    except RuntimeError:
        return None
    if not output_p.is_file():
        return None
    if routing_attempt_evidence_error(
        prior.get("build"),
        circuit_json_path=output_p,
    ) is not None:
        return None
    artifacts = prior.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return None
    for rel in artifacts.values():
        if not isinstance(rel, str) or not (boards_dir / rel).exists():
            return None

    board = prior.get("board") or {}
    fab_meta = prior.get("fab") or {}
    bom_meta = prior.get("bom") or {}
    validation = prior.get("validation") or {}
    build_meta = prior.get("build") or {}
    result: dict[str, object] = {
        "circuit_json_path": str(output_p),
        "metadata_path": str(sidecar_path),
        "schematic_png": str(boards_dir / str(artifacts.get("schematicPng"))),
        "pcb_png": str(boards_dir / str(artifacts.get("pcbPng"))),
        "board": {
            "width_mm": float(board.get("widthMm") or 0),
            "height_mm": float(board.get("heightMm") or 0),
            "layers": int(board.get("layers") or 0),
        },
        "bom": _bom_result_block(bom_meta if isinstance(bom_meta, dict) else {}),
        "fab": {
            "profile": str(fab_meta.get("profile") or ""),
            "ready": bool(fab_meta.get("ready")),
            "packet_dir": str(fab_dir),
        },
        "build": {
            "autorouter_effort": build_meta.get("autorouterEffort"),
            "attempts": build_meta.get("attempts"),
            "blocking_by_attempt": build_meta.get("blockingByAttempt"),
            "attempt_evidence": build_meta.get("attemptEvidence"),
        },
        "warnings": list(validation.get("warnings") or []),
        "unchanged": True,
    }
    return result


def _is_same_filesystem(path_a: Path, path_b: Path) -> bool:
    try:
        return path_a.stat().st_dev == path_b.stat().st_dev
    except OSError:
        return False
