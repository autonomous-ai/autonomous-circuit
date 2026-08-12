#!/usr/bin/env python3
"""Regression lock on the example boards — a board may get better, never worse.

`examples/*/boards/main.board.json` is a committed sidecar: the pipeline's own
verdict on a real design, recorded at a moment in time. Before using that
verdict, the fast lock hashes the board's complete local import graph plus
`product.json` and `parts.json` and requires the sidecar fingerprint to match.
It also verifies the project's content-hashed golden-block snapshot lock, so
non-imported provenance/license/docs cannot drift outside the frozen design.
It also requires the canonical board/review artifacts to be declared and every
artifact declared by the sidecar to exist. That makes the sidecar a trustworthy
free ratchet rather than a stale success report. A board that once reached N
blocking warnings must never silently come back with N+1, and one that reached
`fab.ready: true` must never quietly stop being orderable.

Cheap because it reads the committed sidecars rather than rebuilding: it
catches the commit that regressed a board, at the moment that commit lands.
`--rebuild` does the expensive thing — rebuild every example through the real
pipeline in parallel and compare — which is what you want after a block, a
check or a toolchain bump.

The lock only ever tightens. When a board improves, `--accept` writes the new,
better numbers into the baseline, so the ratchet moves one way. There is no
flag that loosens it; a genuine regression is either fixed or explained in the
baseline's `note`.

    python evals/examples_lock.py             # fast: check committed sidecars
    python evals/examples_lock.py --rebuild   # slow: rebuild and compare
    python evals/examples_lock.py --accept    # ratchet the baseline tighter
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "packages" / "circuitpy" / "src"))

EXAMPLES = REPO / "examples"
BASELINE = Path(__file__).resolve().parent / "examples-baseline.json"

from circuitpy.source_hash import board_source_hash  # noqa: E402
from circuitpy import fab as fab_mod  # noqa: E402
from circuitpy import spec as spec_mod  # noqa: E402
from circuitpy.generation import (  # noqa: E402
    GENERATOR_NAME,
    _current_toolchain_block,
    pipeline_revision,
    routing_attempt_evidence_error,
)
from scripts.sync_golden_blocks import (  # noqa: E402
    SyncError as GoldenBlockSyncError,
    check_project as check_golden_block_snapshot,
)


FRESH_EVIDENCE = "Fresh"
INVALID_BLOCKING = 10_000
CANONICAL_BOARD_ARTIFACT = "main.circuit.json"
CANONICAL_REVIEW_ARTIFACTS = {
    "pcbPng": "main_review/_pcb.png",
    "schematicPng": "main_review/_schematic.png",
}


def sidecar_for(project: Path) -> dict | None:
    path = project / "boards" / "main.board.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError):
        return None


def measure(sidecar: dict) -> dict:
    warnings = sidecar.get("validation", {}).get("warnings", [])
    blocking = [w for w in warnings if w.get("severity") == "error"]
    return {
        "blocking": len(blocking),
        "fabReady": bool(sidecar.get("fab", {}).get("ready")),
        "blockingKinds": sorted({w["kind"] for w in blocking}),
        "bomLines": sidecar.get("bom", {}).get("lines"),
        "autorouterEffort": sidecar.get("build", {}).get(
            "autorouterEffort", "default"
        ),
    }


def invalid_measurement(
    status: str,
    *,
    detail: str,
    **evidence_fields: object,
) -> dict:
    """Return a fail-closed measurement without pretending it is a board result."""
    evidence: dict[str, object] = {"status": status, "detail": detail}
    evidence.update(evidence_fields)
    return {
        "blocking": INVALID_BLOCKING,
        "fabReady": False,
        "blockingKinds": [status],
        "bomLines": None,
        "autorouterEffort": "default",
        "evidence": evidence,
    }


def evidence_status(measured: dict) -> str:
    """Read the new evidence marker while accepting legacy in-memory values."""
    evidence = measured.get("evidence")
    if not isinstance(evidence, dict):
        return FRESH_EVIDENCE
    status = evidence.get("status")
    return str(status) if status else FRESH_EVIDENCE


def incomplete_sidecar_fields(sidecar: dict, *, project: Path | None = None) -> list[str]:
    """Return missing/malformed fields required to trust a board verdict.

    The generator intentionally emits ``validation: {}`` for a clean build,
    so ``warnings`` is optional.  The blocks that carry the measured values
    are not optional, nor are the board and two review artifacts emitted on
    every successful build.  Conditional manufacturing artifacts remain
    allowed, but once declared they are checked by
    :func:`missing_declared_artifacts`.
    """
    incomplete: list[str] = []

    validation = sidecar.get("validation")
    validation_warnings: list[dict] | None = None
    if not isinstance(validation, dict):
        incomplete.append("validation (missing or not an object)")
    else:
        warnings = validation.get("warnings", [])
        if not isinstance(warnings, list):
            incomplete.append("validation.warnings (not a list)")
        else:
            validation_warnings = warnings
            for index, warning in enumerate(warnings):
                if not isinstance(warning, dict):
                    incomplete.append(
                        f"validation.warnings[{index}] (not an object)"
                    )
                    continue
                if not isinstance(warning.get("severity"), str):
                    incomplete.append(
                        f"validation.warnings[{index}].severity (missing or invalid)"
                    )
                if not isinstance(warning.get("kind"), str) or not warning["kind"]:
                    incomplete.append(
                        f"validation.warnings[{index}].kind (missing or invalid)"
                    )

    fab = sidecar.get("fab")
    if not isinstance(fab, dict):
        incomplete.append("fab (missing or not an object)")
    elif not isinstance(fab.get("ready"), bool):
        incomplete.append("fab.ready (missing or not a boolean)")
    elif not isinstance(fab.get("profile"), str) or not fab.get("profile"):
        incomplete.append("fab.profile (missing or invalid)")

    routing_error = routing_attempt_evidence_error(
        sidecar.get("build"),
        circuit_json_path=(project / "boards" / CANONICAL_BOARD_ARTIFACT)
        if project is not None
        else None,
        final_warnings=(
            validation_warnings if project is not None else None
        ),
        fab_ready=(
            fab.get("ready")
            if isinstance(fab, dict) and isinstance(fab.get("ready"), bool)
            else None
        ),
    )
    if routing_error is not None:
        incomplete.append(routing_error)

    bom = sidecar.get("bom")
    if not isinstance(bom, dict):
        incomplete.append("bom (missing or not an object)")
    else:
        lines = bom.get("lines")
        if isinstance(lines, bool) or not isinstance(lines, int) or lines < 0:
            incomplete.append("bom.lines (missing or not a non-negative integer)")

    board = sidecar.get("board")
    if not isinstance(board, dict):
        incomplete.append("board (missing or not an object)")
    elif board.get("path") != CANONICAL_BOARD_ARTIFACT:
        incomplete.append(
            f"board.path (must be {CANONICAL_BOARD_ARTIFACT})"
        )

    artifacts = sidecar.get("artifacts")
    if not isinstance(artifacts, dict):
        incomplete.append("artifacts (missing or not an object)")
    else:
        for key, expected in CANONICAL_REVIEW_ARTIFACTS.items():
            if artifacts.get(key) != expected:
                incomplete.append(f"artifacts.{key} (must be {expected})")

    return incomplete


def missing_declared_artifacts(project: Path, sidecar: dict) -> list[str]:
    """List missing/invalid board-relative files promised by the sidecar."""
    boards_dir = (project / "boards").resolve()
    declared: list[tuple[str, object]] = []

    board = sidecar.get("board")
    if isinstance(board, dict) and board.get("path"):
        declared.append(("board.path", board["path"]))
    else:
        declared.append(("board.path", None))

    artifacts = sidecar.get("artifacts", {})
    if not isinstance(artifacts, dict):
        declared.append(("artifacts", None))
    else:
        declared.extend(
            (f"artifacts.{name}", value)
            for name, value in sorted(artifacts.items())
        )

    missing: list[str] = []
    for label, relative in declared:
        if not isinstance(relative, str) or not relative.strip():
            missing.append(f"{label} (not declared)")
            continue
        candidate = (boards_dir / relative).resolve()
        try:
            candidate.relative_to(boards_dir)
        except ValueError:
            missing.append(f"{label} ({relative!r} escapes boards/)")
            continue
        if not candidate.is_file():
            missing.append(relative)
    return missing


def measurement_for_project(project: Path) -> dict:
    """Measure one project only when its committed evidence is current."""
    sidecar_path = project / "boards" / "main.board.json"
    if not sidecar_path.is_file():
        return invalid_measurement(
            "MissingSidecar",
            detail=f"missing {sidecar_path.relative_to(project)}",
        )
    sidecar = sidecar_for(project)
    if sidecar is None:
        return invalid_measurement(
            "UnreadableSidecar",
            detail=f"cannot parse {sidecar_path.relative_to(project)}",
        )

    try:
        snapshot_errors = check_golden_block_snapshot(project)
    except GoldenBlockSyncError as exc:
        return invalid_measurement(
            "InvalidGoldenBlockSnapshot",
            detail=f"golden-block snapshot is not locked: {exc}",
        )
    if snapshot_errors:
        return invalid_measurement(
            "InvalidGoldenBlockSnapshot",
            detail="golden-block snapshot drift: " + "; ".join(snapshot_errors),
            snapshotErrors=snapshot_errors,
        )

    source_path = project / "boards" / "main.tsx"
    if not source_path.is_file():
        return invalid_measurement(
            "MissingBoardSource",
            detail=f"missing {source_path.relative_to(project)}",
        )
    try:
        identity = board_source_hash(source_path, project)
    except OSError as exc:
        return invalid_measurement(
            "UnreadableSourceGraph",
            detail=f"cannot hash source graph: {exc}",
        )

    recorded = sidecar.get("source")
    if not isinstance(recorded, dict):
        recorded = {}
    recorded_fingerprint = recorded.get("fingerprint")
    recorded_hash = recorded.get("hash")
    recorded_path = recorded.get("path")
    if (
        recorded_fingerprint != identity.source_fingerprint
        or recorded_hash != identity.source_hash
        or recorded_path != identity.source_path
    ):
        return invalid_measurement(
            "StaleSidecar",
            detail="sidecar source identity does not match the current source graph",
            recordedFingerprint=recorded_fingerprint,
            currentFingerprint=identity.source_fingerprint,
            recordedSourceHash=recorded_hash,
            currentSourceHash=identity.source_hash,
            recordedSourcePath=recorded_path,
            currentSourcePath=identity.source_path,
        )

    if (
        sidecar.get("generator") != GENERATOR_NAME
        or sidecar.get("generatorRevision") != pipeline_revision()
    ):
        return invalid_measurement(
            "StalePipeline",
            detail="sidecar was graded by a different circuitpy pipeline revision",
            recordedGenerator=sidecar.get("generator"),
            currentGenerator=GENERATOR_NAME,
            recordedRevision=sidecar.get("generatorRevision"),
            currentRevision=pipeline_revision(),
        )
    try:
        current_toolchain = _current_toolchain_block()
    except RuntimeError as exc:
        return invalid_measurement(
            "UnavailableToolchain",
            detail=f"cannot establish current pinned toolchain identity: {exc}",
        )
    recorded_toolchain = sidecar.get("toolchain")
    recorded_build_identity = (
        {
            key: value
            for key, value in recorded_toolchain.items()
            if key != "kicadCli"
        }
        if isinstance(recorded_toolchain, dict)
        else recorded_toolchain
    )
    current_build_identity = {
        key: value for key, value in current_toolchain.items() if key != "kicadCli"
    }
    if recorded_build_identity != current_build_identity:
        return invalid_measurement(
            "StaleToolchain",
            detail=(
                "sidecar compiler/router identity does not match the installed "
                "pinned and patched toolchain"
            ),
            recordedToolchain=recorded_build_identity,
            currentToolchain=current_build_identity,
        )

    # Structural validation comes before file validation so a missing
    # canonical board is reported as MissingArtifact rather than as a derived
    # routing-evidence mismatch.
    incomplete = incomplete_sidecar_fields(sidecar)
    if incomplete:
        return invalid_measurement(
            "IncompleteSidecar",
            detail="incomplete sidecar field(s): " + ", ".join(incomplete),
            incompleteFields=incomplete,
        )

    missing = missing_declared_artifacts(project, sidecar)
    if missing:
        return invalid_measurement(
            "MissingArtifact",
            detail="missing declared artifact(s): " + ", ".join(missing),
            missingArtifacts=missing,
        )

    validation = sidecar.get("validation")
    warnings = validation.get("warnings", []) if isinstance(validation, dict) else []
    fab = sidecar.get("fab")
    try:
        product = spec_mod.load_product(project)
        profile = fab_mod.get_profile(str(fab.get("profile")))
    except Exception as exc:
        return invalid_measurement(
            "IncompleteSidecar",
            detail=f"cannot reconstruct routing evidence context: {exc}",
        )
    routing_error = routing_attempt_evidence_error(
        sidecar.get("build"),
        circuit_json_path=project / "boards" / CANONICAL_BOARD_ARTIFACT,
        final_warnings=warnings,
        fab_ready=(
            fab.get("ready")
            if isinstance(fab, dict) and isinstance(fab.get("ready"), bool)
            else None
        ),
        product=product,
        profile=profile,
    )
    if routing_error is not None:
        return invalid_measurement(
            "IncompleteSidecar",
            detail="incomplete sidecar field(s): " + routing_error,
            incompleteFields=[routing_error],
        )

    try:
        measured = measure(sidecar)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return invalid_measurement(
            "UnreadableSidecar",
            detail=f"invalid measurement fields: {exc}",
        )
    measured["evidence"] = {
        "status": FRESH_EVIDENCE,
        "fingerprint": identity.source_fingerprint,
    }
    return measured


def current(rebuild: bool) -> dict[str, dict]:
    projects = sorted(
        p for p in EXAMPLES.iterdir()
        if p.is_dir() and (p / "product.json").is_file()
    )
    if not rebuild:
        return {
            project.name: measurement_for_project(project)
            for project in projects
        }

    from circuitpy.batch import BuildJob, build_many

    jobs = [
        BuildJob(
            source=p / "boards" / "main.tsx",
            output=p / "boards" / "main.circuit.json",
            label=p.name,
            meta={"project": p.name},
        )
        for p in projects
    ]

    def _progress(outcome, done: int, total: int) -> None:
        print(f"[{done}/{total}] {outcome.job.resolved_label():<20} "
              f"{outcome.seconds:.0f}s "
              f"{'fab-ready' if outcome.fab_ready else 'not ready'}", flush=True)

    report = build_many(jobs, on_done=_progress)
    print(report.summary())
    out = {}
    for outcome in report.outcomes:
        name = str(outcome.job.meta["project"])
        if not outcome.ok:
            out[name] = invalid_measurement(
                "BuildCrashed",
                detail=outcome.error or "build failed without an error message",
            )
            continue
        out[name] = measurement_for_project(EXAMPLES / name)
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--accept", action="store_true",
                        help="ratchet the baseline to today's better numbers")
    args = parser.parse_args(argv[1:])

    now = current(args.rebuild)
    baseline: dict = {}
    if BASELINE.is_file():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    boards: dict = baseline.get("boards", {})

    # A board cannot disappear from the filesystem and thereby disappear from
    # the ratchet. Existing baseline files have no evidence field; that legacy
    # shape remains valid and is compared exactly as before.
    for name in sorted(set(boards) - set(now)):
        now[name] = invalid_measurement(
            "MissingProject",
            detail=f"baseline board has no example project at examples/{name}",
        )

    failures: list[str] = []
    improvements: list[str] = []
    for name, measured in sorted(now.items()):
        status = evidence_status(measured)
        if status != FRESH_EVIDENCE:
            evidence = measured.get("evidence", {})
            detail = evidence.get("detail", "invalid evidence") \
                if isinstance(evidence, dict) else "invalid evidence"
            failures.append(f"{name}: {status}: {detail}")
            continue
        locked = boards.get(name)
        if locked is None:
            improvements.append(f"{name}: new board, locking at "
                                f"{measured['blocking']} blocking")
            continue
        if measured["blocking"] > locked["blocking"]:
            failures.append(
                f"{name}: {locked['blocking']} blocking -> {measured['blocking']} "
                f"({', '.join(measured['blockingKinds']) or 'none'})"
            )
        elif measured["blocking"] < locked["blocking"]:
            improvements.append(
                f"{name}: {locked['blocking']} -> {measured['blocking']} blocking"
            )
        if locked.get("fabReady") and not measured["fabReady"]:
            failures.append(f"{name}: was fab-ready, now is not")
        elif measured["fabReady"] and not locked.get("fabReady"):
            improvements.append(f"{name}: now fab-ready")

    for line in improvements:
        print(f"better  {line}")
    for line in failures:
        print(f"REGRESSION  {line}")

    if args.accept:
        invalid = {
            name: evidence_status(measured)
            for name, measured in now.items()
            if evidence_status(measured) != FRESH_EVIDENCE
        }
        if invalid:
            summary = ", ".join(
                f"{name}={status}" for name, status in sorted(invalid.items())
            )
            print(f"\nREFUSED  --accept requires fresh evidence ({summary})")
            return 1
        if failures:
            # ``--accept`` is a one-way ratchet, not an override.  In
            # particular, equal blocking counts must not be allowed to erase a
            # previously fab-ready verdict, and a worse blocking count must
            # not be hidden merely because the old baseline is left in place.
            print(
                "\nREFUSED  --accept cannot record or ignore a regression; "
                "fix the board or keep the existing baseline"
            )
            return 1
        merged = dict(boards)
        for name, measured in now.items():
            locked = merged.get(name)
            if locked is None or measured["blocking"] <= locked["blocking"]:
                # Evidence is a property of today's artifact, not a historical
                # ratchet field. Keeping it out preserves the existing baseline
                # schema while old baselines remain readable.
                merged[name] = {
                    key: value for key, value in measured.items()
                    if key != "evidence"
                }
        BASELINE.write_text(
            json.dumps(
                {
                    "note": (
                        "Ratchet, not a snapshot: these are the best numbers each "
                        "example board has ever reached. A build may improve on "
                        "them and --accept records that; a build may never come "
                        "back worse. Written by evals/examples_lock.py."
                    ),
                    "updatedAt": time.strftime("%Y-%m-%d"),
                    "boards": dict(sorted(merged.items())),
                },
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        print(f"\nbaseline ratcheted: {BASELINE}")
        return 0

    if not boards:
        print("\nno baseline yet — run with --accept to create one")
        return 1 if failures else 0
    print(f"\n{len(now)} example boards checked, {len(failures)} regressions")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
