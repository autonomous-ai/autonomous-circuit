"""Stages 4c and 5b — the standalone checks, called from the pipeline.

``packages/verify`` is deliberately independent: it imports nothing from
circuitpy, so its opinion is a second opinion. This module is the one-way
adapter that lets the pipeline ask for it.

Three jobs, and nothing else:

1. find ``verifylib`` (vendored beside circuitpy in a skill runtime, or in the
   repo at ``packages/verify/src`` in dev) without making it a hard dependency;
2. run the checks that belong at each stage;
3. hand every finding to :func:`circuitpy.fab.apply_verify_policy`, so the fab
   profile — not the check — decides what blocks ``fab.ready``.

**When ``verifylib`` is missing** the pipeline emits one ``verify_unavailable``
info, exactly as it does for kicad-cli. An absent check must be visible, never
silent: the whole point of this package is that silence is not a pass.

**Corners run beside the build, not inside it.** ``corners.check`` is the only
check whose cost is noticeable (seconds, not milliseconds), it can never
produce a blocking finding, and its purpose is to catch what a nominal pass
already called clean. Set ``CIRCUIT_VERIFY_CORNERS=1`` to include it; the
default keeps it off the critical path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from circuitpy.fab import FabProfile, apply_verify_policy

#: Checks that read circuit.json. They run at stage 4, beside the DFM gate.
CIRCUIT_JSON_CHECKS = (
    "assembly", "netclass", "dc", "review", "thermal", "crystal",
)
#: Checks that read the shipped packet. Stage 5b, after the gerbers exist.
PACKET_CHECKS = ("gerber",)

CORNERS_ENV = "CIRCUIT_VERIFY_CORNERS"
DISABLE_ENV = "CIRCUIT_VERIFY_OFF"

_MODULES: dict[str, Any] | None = None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _ensure_path() -> None:
    """Put ``verifylib`` on ``sys.path``. Mirrors the skill runtime's own
    resolution order: the vendored copy first, the repo checkout as the
    dev-before-vendoring fallback."""
    here = Path(__file__).resolve()
    candidates = [
        # Vendored: skills/<skill>/scripts/packages/{circuitpy,verifylib}
        here.parents[1],
        # Repo dev checkout: <repo>/packages/verify/src
        *(
            ancestor / "packages" / "verify" / "src"
            for ancestor in here.parents
        ),
    ]
    for candidate in candidates:
        if (candidate / "verifylib" / "__init__.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return


def _load() -> dict[str, Any] | None:
    """Import verifylib once. ``None`` when it is not installed."""
    global _MODULES
    if _MODULES is not None:
        return _MODULES or None
    _ensure_path()
    try:
        from verifylib import (  # noqa: PLC0415
            assembly,
            corners,
            crystal,
            dc,
            gerber_truth,
            model,
            netclass,
            review,
            thermal,
        )
    except Exception:  # noqa: BLE001 - an absent verifier is not a build failure
        _MODULES = {}
        return None
    _MODULES = {
        "assembly": assembly,
        "corners": corners,
        "crystal": crystal,
        "dc": dc,
        "gerber_truth": gerber_truth,
        "model": model,
        "netclass": netclass,
        "review": review,
        "thermal": thermal,
    }
    return _MODULES


def unavailable_warning() -> dict[str, str]:
    return {
        "part": "board",
        "kind": "verify_unavailable",
        "detail": (
            "packages/verify is not importable, so the assembly, net-class, "
            "DC, design-review, thermal and gerber-truth checks did not run — "
            "this board was graded on geometry alone"
        ),
        "severity": "info",
    }


def _failed(name: str, exc: BaseException) -> dict[str, str]:
    return {
        "part": "board",
        "kind": "check_failed",
        "detail": f"verifylib {name} raised {type(exc).__name__}: {exc}",
        "severity": "warning",
    }


def available() -> bool:
    return _load() is not None


def check_circuit_json(
    circuit_json_path: Path,
    *,
    profile: FabProfile,
    assembly_order: bool,
) -> list[dict]:
    """Stage 4c: everything judgeable from the compiled board."""
    if _truthy(os.environ.get(DISABLE_ENV)):
        return []
    modules = _load()
    if modules is None:
        return [unavailable_warning()]

    findings: list[dict] = []
    try:
        board = modules["model"].load(circuit_json_path)
    except Exception as exc:  # noqa: BLE001
        return [_failed("model.load", exc)]

    runners = {
        "assembly": lambda: modules["assembly"].check(board, assembly=assembly_order),
        "netclass": lambda: modules["netclass"].check(board),
        "dc": lambda: modules["dc"].check(board),
        "review": lambda: modules["review"].check(board),
        "thermal": lambda: modules["thermal"].check(board),
        "crystal": lambda: modules["crystal"].check(board),
    }
    for name in CIRCUIT_JSON_CHECKS:
        try:
            findings.extend(runners[name]().findings)
        except Exception as exc:  # noqa: BLE001
            findings.append(_failed(name, exc))

    if _truthy(os.environ.get(CORNERS_ENV)):
        try:
            findings.extend(modules["corners"].check(board).findings)
        except Exception as exc:  # noqa: BLE001
            findings.append(_failed("corners", exc))

    return apply_verify_policy(findings, profile)


def check_packet(
    circuit_json_path: Path,
    gerbers_zip: Path,
    *,
    profile: FabProfile,
    assembly_order: bool,
) -> list[dict]:
    """Stage 5b: the packet the fab actually receives.

    This cannot run earlier — the artifact does not exist until stage 5 — and
    it is the only check in the pipeline that inspects what JLCPCB is sent
    rather than what we meant to send.
    """
    if _truthy(os.environ.get(DISABLE_ENV)):
        return []
    modules = _load()
    if modules is None:
        return []  # stage 4c already said so once; do not say it twice
    try:
        board = modules["model"].load(circuit_json_path)
        result = modules["gerber_truth"].check(
            board, str(gerbers_zip), assembly=assembly_order
        )
    except Exception as exc:  # noqa: BLE001
        return [_failed("gerber_truth", exc)]
    return apply_verify_policy(result.findings, profile)
