"""Stage 0b — route the board with our own router instead of the shipped one.

Off by default. ``CIRCUIT_ROUTER=portfolio`` turns it on.

``packages/router`` is deliberately independent: ``routerlib`` imports geometry
*from* ``circuitpy.checks`` and nothing the other way round, so its opinion of a
board is not this package's opinion reflected back. This module is the one-way
adapter that lets the pipeline ask for a route, exactly as ``verify_bridge``
lets it ask for a second opinion.

**Why there is a flag at all.** The autorouter we ship cannot represent the
rules we grade against — no model of drill clearance, no concept of a plane as
a net, no net classes, and a clearance target equal to our floor so it aims at
the failure line. Four attempts to configure it failed for that one reason.
``routerlib`` knows those rules natively. What it does not yet have is the
shipped router's completeness, so this is measured before it is switched on and
**the default does not change**. See ``docs/architecture/routing.md`` for the
numbers on both sides.

Three env vars, and nothing else:

``CIRCUIT_ROUTER``
    ``off`` (default, or unset) keeps the shipped autorouter's copper.
    ``portfolio`` runs ours and keeps it **only if it connects at least as many
    nets** — the same rule the effort escalation obeys, because a stage that
    can make a board worse is a stage nobody can leave on.
    ``portfolio-force`` keeps ours unconditionally. Measurement only: it exists
    so an A/B can see the real answer rather than the better of two.
``CIRCUIT_ROUTER_MODE``
    ``single`` | ``best-of-n`` | ``relay``. Default ``relay``.
``CIRCUIT_ROUTER_BUDGET``
    ``cheap`` | ``standard`` | ``thorough``. Default ``thorough``: the north
    star's exchange rate is a two-week fab round trip against minutes of
    compute, and the whole four-family relay is about 30 seconds on a board of
    this size.

**Known limitation, stated rather than worked around.** A board that already
carries a ``pcb_copper_pour`` is refused. The pour in a built ``circuit.json``
was generated after routing and is carved around the *incumbent's* traces;
replacing the copper underneath leaves a zone shaped like an answer that is no
longer there. Pouring after our route is the fix and it is not written yet.
``CIRCUIT_ROUTER_ALLOW_POURS=1`` overrides, and the report says it did.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROUTER_ENV = "CIRCUIT_ROUTER"
MODE_ENV = "CIRCUIT_ROUTER_MODE"
BUDGET_CLASS_ENV = "CIRCUIT_ROUTER_BUDGET"
ALLOW_POURS_ENV = "CIRCUIT_ROUTER_ALLOW_POURS"
SEED_ENV = "CIRCUIT_ROUTER_SEED"

#: Counted, never timed — the harness contract. Wall clock is a property of
#: the machine and a budget measured in it makes the machine decide the board.
MAX_ITERATIONS = 2_000_000
MAX_NODES = 20_000_000

_LOADED: bool | None = None


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def engine() -> str:
    """``off`` | ``portfolio`` | ``portfolio-force``."""
    value = (os.environ.get(ROUTER_ENV) or "").strip().lower()
    if value in ("", "off", "0", "false", "no", "tscircuit", "shipped"):
        return "off"
    if value in ("portfolio", "routerlib", "on", "1", "true"):
        return "portfolio"
    if value in ("portfolio-force", "force"):
        return "portfolio-force"
    return "off"


def _ensure_path() -> bool:
    """Put ``routerlib`` on ``sys.path``. Vendored copy first, repo second —
    the same resolution order ``verify_bridge`` uses."""
    global _LOADED
    if _LOADED is not None:
        return _LOADED
    here = Path(__file__).resolve()
    candidates = [here.parents[1]]
    for ancestor in here.parents:
        candidates.append(ancestor / "packages" / "router" / "src")
    for candidate in candidates:
        if (candidate / "routerlib" / "portfolio.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            _LOADED = True
            return True
    _LOADED = False
    return False


def _registry() -> dict:
    """The algorithm families, loaded by path out of ``packages/router``.

    They live outside the installed package on purpose — each is a single
    tournament entry, not library code — so they are loaded the way the judge
    loads them rather than imported.
    """
    import importlib.util

    here = Path(__file__).resolve()
    algos: Path | None = None
    for ancestor in here.parents:
        candidate = ancestor / "packages" / "router" / "algorithms"
        if candidate.is_dir():
            algos = candidate
            break
    out: dict = {}
    if algos is not None:
        for path in sorted(algos.glob("*.py")):
            if path.name.startswith("test_") or path.name.startswith("bench"):
                continue
            spec = importlib.util.spec_from_file_location(
                f"_algo_{path.stem.replace('-', '_')}", path
            )
            if spec is None or spec.loader is None:
                continue
            try:
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                out.update(dict(getattr(module, "ROUTERS", {})))
            except Exception:  # noqa: BLE001 — a family that will not load is
                continue      # one family, never the build
    from routerlib.baseline import PatternRouter

    out.setdefault(PatternRouter.name, PatternRouter)
    return out


def route_board(circuit_json_path: Path, profile: Any) -> dict:
    """Re-route ``circuit_json_path`` in place. Returns the report for the
    sidecar; never raises, because a routing experiment must not be able to
    fail a build that would otherwise pass.

    Every early return carries ``applied: False`` and a ``reason``. An absent
    router must be visible, never silent — the same rule ``verify_bridge``
    follows for an absent check.
    """
    mode = engine()
    report: dict[str, Any] = {"engine": mode, "applied": False}
    if mode == "off":
        report["reason"] = "not requested"
        return report
    if not _ensure_path():
        report["reason"] = (
            "routerlib not found — packages/router is not vendored beside "
            "circuitpy and not in this checkout"
        )
        return report

    t0 = time.perf_counter()
    try:
        from routerlib import portfolio
        from routerlib.adapters import (
            apply_solution,
            problem_from_circuit_json,
        )
        from routerlib.connectivity import analyse
        from routerlib.model import Budget, RoutingSolution

        elements = json.loads(circuit_json_path.read_text(encoding="utf-8"))
        pours = sum(
            1 for e in elements
            if isinstance(e, dict) and e.get("type") == "pcb_copper_pour"
        )
        report["pours"] = pours
        if pours and not _truthy(os.environ.get(ALLOW_POURS_ENV)):
            report["reason"] = (
                f"board carries {pours} copper pour(s) generated around the "
                f"incumbent's traces; re-pouring after our route is not "
                f"written yet (set {ALLOW_POURS_ENV}=1 to route anyway)"
            )
            return report

        problem = problem_from_circuit_json(
            elements, problem_id=circuit_json_path.stem,
            strip_routes=True, strip_planes=True,
        )
        with_copper = problem_from_circuit_json(
            elements, problem_id=circuit_json_path.stem,
            strip_routes=False, strip_planes=True,
        )
        incumbent = RoutingSolution(
            router="tscircuit-autorouter",
            traces=with_copper.existing_traces,
            vias=with_copper.existing_vias,
            complete=False,
        )
        before = analyse(problem, incumbent)

        budget = Budget(
            max_iterations=MAX_ITERATIONS,
            max_nodes=MAX_NODES,
            seed=int(os.environ.get(SEED_ENV) or 0),
        )
        result = portfolio.route(
            problem, budget, _registry(),
            budget_class=(os.environ.get(BUDGET_CLASS_ENV) or "thorough").strip(),
            mode=(os.environ.get(MODE_ENV) or "relay").strip(),
        )
        after = analyse(problem, result.solution)

        report["selection"] = result.selection.as_dict()
        report["stages"] = [s.as_dict() for s in result.stages]
        report["fingerprint"] = result.solution.fingerprint()
        report["before"] = {
            "connectedNets": len(before.connected_nets),
            "routableNets": before.routable_nets,
            "completeness": round(before.completeness, 6),
            "traces": len(incumbent.traces),
            "vias": len(incumbent.vias),
            "copperMm": round(incumbent.copper_length_mm, 1),
        }
        report["after"] = {
            "connectedNets": len(after.connected_nets),
            "routableNets": after.routable_nets,
            "completeness": round(after.completeness, 6),
            "traces": len(result.solution.traces),
            "vias": len(result.solution.vias),
            "copperMm": round(result.solution.copper_length_mm, 1),
        }
        if mode == "portfolio" and after.completeness < before.completeness - 1e-9:
            report["reason"] = (
                f"kept the incumbent: ours connects "
                f"{len(after.connected_nets)}/{after.routable_nets} nets against "
                f"the autorouter's {len(before.connected_nets)}. A stage that "
                f"can make a board worse is a stage nobody can leave on "
                f"(use {ROUTER_ENV}=portfolio-force to override)"
            )
            return report

        circuit_json_path.write_text(
            json.dumps(apply_solution(elements, problem, result.solution)),
            encoding="utf-8",
        )
        report["applied"] = True
    except BaseException as exc:  # noqa: BLE001 — never fail a build over this
        report["reason"] = f"{type(exc).__name__}: {exc}"
        report["applied"] = False
    finally:
        report["seconds"] = round(time.perf_counter() - t0, 2)
    return report


__all__ = [
    "ALLOW_POURS_ENV",
    "BUDGET_CLASS_ENV",
    "MODE_ENV",
    "ROUTER_ENV",
    "engine",
    "route_board",
]
