#!/usr/bin/env python3
"""Build one board N times and say whether the router produced the same copper.

    python3 toolchain/determinism/run_repeat.py examples/harness-puck -n 3
    python3 toolchain/determinism/run_repeat.py examples/harness-puck -n 3 --load 8

This is the ruler for the determinism work, so it deliberately does the *least*
possible around the router: it mirrors the project's source into N separate
scratch directories and runs ``tscircuit-cli build`` in each, serially. No DFM,
no KiCad, no exports — every one of those is downstream of the route, and
including them would make a slow measurement of a fast question.

``--load`` starts N busy-loop processes for the duration, which is how the "on
a loaded machine" half of the acceptance bar gets measured on purpose rather
than by waiting for the machine to be busy.

Output is one JSON object on stdout (and a human summary on stderr), so it can
be pasted into a report or diffed between two toolchain states.

THE RULER REFUSES TO MEASURE A BOARD THAT DID NOT ROUTE
-------------------------------------------------------

Measured 2026-08-16: ``examples/harness-puck`` built in a bare mirror comes
back with ``pcb_autorouting_error`` — *"Autorouting was skipped because 1 PCB
placement error was found"* (Y1's courtyard overlaps SW1's) — and therefore
zero ``pcb_trace`` and zero ``pcb_via`` elements. The first version of this
script reported that board as ``route bytes IDENTICAL`` across three runs,
which is true and worthless: an empty route is trivially reproducible.

That is `docs/lessons.md` lesson A wearing a new hat — a check that cannot see
a shape silently passes it. So a run with no copper, or with an autorouting
error, is now a hard failure of the *measurement*, not a pass of the board.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from route_fingerprint import diff_summary, fingerprint  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TOOLCHAIN = REPO / "toolchain"
PRELOAD = Path(__file__).resolve().parent / "deterministic-run.mjs"


def _circuitpy():
    """circuitpy.generation, imported lazily.

    Only the deterministic modes need it. The plain baseline mode has to keep
    working in a tree where circuitpy is broken or absent, because that is
    exactly when you want to measure.
    """
    sys.path.insert(0, str(REPO / "packages" / "circuitpy" / "src"))
    from circuitpy import generation  # noqa: PLC0415

    return generation

_SKIP_DIRS = {
    ".circuit",
    ".claude",
    ".git",
    ".tscircuit",
    "__pycache__",
    "dist",
    "inputs",
    "node_modules",
}
_KEEP_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".json"}


def mirror(project: Path, work: Path) -> None:
    """The same source surface generation.py copies into .circuit/build."""
    for root, dirs, files in os.walk(project):
        root_path = Path(root)
        dirs[:] = [
            d
            for d in dirs
            if d not in _SKIP_DIRS
            and not d.endswith("_review")
            and not d.endswith("_fab")
        ]
        for name in files:
            source = root_path / name
            if source.suffix not in _KEEP_SUFFIXES:
                continue
            if name.endswith(".circuit.json") or name.endswith(".board.json"):
                continue
            target = work / source.relative_to(project)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    anchor = work / "package.json"
    if not anchor.exists():
        anchor.write_text(
            json.dumps({"name": "determinism-workspace", "private": True}),
            encoding="utf-8",
        )


_EFFORT_RE = re.compile(r'(autorouterEffortLevel\s*=\s*")([^"]+)(")')


def force_effort(work: Path, effort: str) -> list[str]:
    """Pin every board in the mirror to one effort level.

    Determinism is a property of the mechanism, not of a setting, so the ruler
    is allowed to measure at a cheaper effort than the board ships with — but
    only if it says so. Returns the files it changed; the caller records both
    the request and the result in the report.
    """
    changed: list[str] = []
    for path in sorted(work.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8")
        if not _EFFORT_RE.search(text):
            continue
        new = _EFFORT_RE.sub(lambda m: f"{m.group(1)}{effort}{m.group(3)}", text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(str(path.relative_to(work)))
    return changed


def declared_efforts(work: Path) -> list[str]:
    found: list[str] = []
    for path in sorted(work.rglob("*.tsx")):
        found.extend(m.group(2) for m in _EFFORT_RE.finditer(path.read_text(encoding="utf-8")))
    return sorted(set(found))


def route_health(circuit_json: Path) -> dict[str, object]:
    """Did this build actually produce a route, did it complain, and did the
    network answer it the same way?

    The guard that stops the ruler reporting on an empty board — and the one
    that stops a network problem being read as a toolchain problem. Measured
    2026-08-16 on terminal-keyboard, which carries no ``parts.json``: two
    builds of one source came back with **47 and 17** ``HTTP 429`` rate-limit
    failures from the supplier catalogue, so a different subset of parts
    resolved each time and the files differed by 5459 against 5441 elements.
    No seed and no canonical order can fix that; the two builds are not builds
    of the same board. hydrate-coaster, whose 19 parts are pinned in
    ``parts.json``, took zero 429s and is byte-identical.
    """
    text = circuit_json.read_text(encoding="utf-8")
    data = json.loads(text)
    kinds: dict[str, int] = {}
    messages: list[str] = []
    unresolved = 0
    for element in data:
        if not isinstance(element, dict):
            continue
        kind = str(element.get("type", ""))
        if kind == "source_part_not_found_warning":
            unresolved += 1
        if kind.endswith("_error"):
            kinds[kind] = kinds.get(kind, 0) + 1
            message = element.get("message")
            if isinstance(message, str) and len(messages) < 4:
                messages.append(message)
    return {
        "error_kinds": kinds,
        "error_messages": messages,
        "parts_unresolved": unresolved,
        "supplier_rate_limited": text.count("HTTP 429"),
    }


def loadavg() -> list[float]:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):  # pragma: no cover - not on this platform
        return []


def cli_env() -> dict[str, str]:
    modules = TOOLCHAIN / "node_modules"
    env = dict(os.environ)
    env["PATH"] = f"{modules / '.bin'}{os.pathsep}{env.get('PATH', '')}"
    env["NODE_PATH"] = str(modules)
    return env


def build_once(
    work: Path, entry: str, timeout_s: float, *, seed: str | None = None
) -> tuple[float, str]:
    exe = str(TOOLCHAIN / "node_modules" / ".bin" / "tscircuit-cli")
    env = cli_env()
    if seed is not None:
        existing = (env.get("NODE_OPTIONS") or "").strip()
        flag = f"--import {PRELOAD.as_uri()}"
        env["NODE_OPTIONS"] = f"{existing} {flag}".strip() if existing else flag
        env["CIRCUIT_DETERMINISTIC_SEED"] = seed
    started = time.monotonic()
    proc = subprocess.run(
        [exe, "build", entry],
        cwd=str(work),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout_s,
        env=env,
    )
    return time.monotonic() - started, proc.stdout.decode("utf-8", "replace")


def pipeline_once(work: Path, entry: str) -> tuple[float, Path]:
    """The product path: circuitpy.build_board, all stages, sidecar and all."""
    generation = _circuitpy()
    board = work / entry
    out = board.with_name(board.stem.split(".")[0] + ".circuit.json")
    started = time.monotonic()
    generation.build_board(board, out)
    return time.monotonic() - started, out


def packet_hashes(circuit_json: Path) -> dict[str, str]:
    """sha256 of every file in the packet this build produced.

    A reproducible route is the acceptance bar, but the artefact a customer
    receives is the packet, and "did this board change?" has to be answerable
    of *that*. Hashing each file separately says which ones are stable rather
    than only that the set is not.
    """
    stem = circuit_json.name.split(".")[0]
    hashes: dict[str, str] = {}
    for suffix in ("_fab", "_review"):
        folder = circuit_json.parent / f"{stem}{suffix}"
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                hashes[str(path.relative_to(circuit_json.parent))] = digest
    return hashes


class Load:
    """N busy CPUs, for the loaded half of the measurement."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.procs: list[subprocess.Popen] = []

    def __enter__(self) -> "Load":
        for _ in range(self.n):
            self.procs.append(
                subprocess.Popen(
                    [sys.executable, "-c", "while True: pass"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
        if self.n:
            time.sleep(2)  # let the scheduler notice
        return self

    def __exit__(self, *_exc: object) -> None:
        for proc in self.procs:
            proc.kill()
        for proc in self.procs:
            proc.wait()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project", type=Path)
    ap.add_argument("-n", "--runs", type=int, default=3)
    ap.add_argument("--entry", default="boards/main.tsx")
    ap.add_argument("--load", type=int, default=0, help="busy processes to start")
    ap.add_argument("--wall-clock-s", type=float, default=5400.0)
    ap.add_argument("--label", default="")
    ap.add_argument("--keep", action="store_true", help="keep the scratch builds")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--mode",
        choices=("cli", "cli-deterministic", "pipeline"),
        default="cli",
        help=(
            "cli: the bare toolchain, no fixes — the baseline ruler. "
            "cli-deterministic: the same call with the seeded-RNG preload and "
            "the canonicalisation pass, which isolates the fix from the rest "
            "of the pipeline. pipeline: circuitpy.build_board, the product."
        ),
    )
    ap.add_argument(
        "--seed",
        default="",
        help="seed for cli-deterministic (default: the project's own name)",
    )
    ap.add_argument(
        "--effort",
        default="",
        help=(
            "pin every board in the mirror to this autorouterEffortLevel. The "
            "report records it, so a cheap-effort measurement can never be "
            "quoted as if it were the shipped one."
        ),
    )
    ap.add_argument(
        "--compare",
        type=Path,
        default=None,
        help=(
            "a report from an earlier run; the exit code also requires this "
            "run's route bytes to match it. This is how 'quiet' and 'loaded' "
            "get compared to each other rather than only to themselves."
        ),
    )
    ap.add_argument(
        "--allow-unrouted",
        action="store_true",
        help="report even when a build produced no copper (default: refuse)",
    )
    args = ap.parse_args()

    project = args.project.resolve()
    scratch = Path(
        os.environ.get("CIRCUIT_DETERMINISM_SCRATCH", "/tmp/circuit-determinism")
    ) / f"{project.name}-{os.getpid()}"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True, exist_ok=True)

    runs: list[dict] = []
    entry_path = Path(args.entry)
    efforts: list[str] = []
    try:
        with Load(args.load):
            for i in range(args.runs):
                work = scratch / f"run{i + 1}"
                work.mkdir(parents=True, exist_ok=True)
                mirror(project, work)
                if args.effort:
                    force_effort(work, args.effort)
                efforts = declared_efforts(work)
                started_load = loadavg()
                if args.mode == "pipeline":
                    seconds, built = pipeline_once(work, args.entry)
                    output = ""
                else:
                    seed = (args.seed or project.name) if args.mode != "cli" else None
                    seconds, output = build_once(
                        work, args.entry, args.wall_clock_s, seed=seed
                    )
                    built = (
                        work
                        / "dist"
                        / entry_path.parent
                        / entry_path.stem
                        / "circuit.json"
                    )
                    if built.is_file() and seed is not None:
                        _circuitpy()._canonicalise_file(built)
                if not built.is_file():
                    print(output[-2000:], file=sys.stderr)
                    raise SystemExit(f"run {i + 1}: no circuit.json at {built}")
                fp = fingerprint(built)
                fp["seconds"] = round(seconds, 1)
                fp["run"] = i + 1
                fp["loadavg_at_start"] = started_load
                fp["loadavg_at_end"] = loadavg()
                fp.update(route_health(built))
                if args.mode == "pipeline":
                    fp["packet"] = packet_hashes(built)
                runs.append(fp)
                print(
                    f"  run {i + 1}: {seconds:6.1f}s  route_bytes={fp['route_bytes'][:12]}  "
                    f"geometry={fp['geometry'][:12]}  file={fp['bytes'][:12]}  "
                    f"copper={fp['copper_elements']}  load={started_load[:1]}",
                    file=sys.stderr,
                )

        # The ruler refuses an empty board. See the module docstring.
        unrouted = [r["run"] for r in runs if not r["copper_elements"]]
        skipped = [
            r["run"] for r in runs if r["error_kinds"].get("pcb_autorouting_error")
        ]
        if (unrouted or skipped) and not args.allow_unrouted:
            for r in runs:
                for message in r["error_messages"]:
                    print(f"  run {r['run']}: {message[:160]}", file=sys.stderr)
            raise SystemExit(
                f"no route to compare — runs with zero copper: {unrouted or 'none'}, "
                f"runs where autorouting was skipped: {skipped or 'none'}. "
                "An empty route is identical every time and proves nothing; fix "
                "the board or pass --allow-unrouted if you meant it."
            )

        geometry = {r["geometry"] for r in runs}
        ordered = {r["ordered"] for r in runs}
        byte_hashes = {r["bytes"] for r in runs}
        route_bytes = {r["route_bytes"] for r in runs}
        report = {
            "label": args.label or project.name,
            "project": str(project),
            "mode": args.mode,
            "runs": args.runs,
            "background_load": args.load,
            "deterministic_route_bytes": len(route_bytes) == 1,
            "deterministic_geometry": len(geometry) == 1,
            "deterministic_ordered": len(ordered) == 1,
            "deterministic_bytes": len(byte_hashes) == 1,
            "distinct_route_byte_hashes": len(route_bytes),
            "distinct_geometry_hashes": len(geometry),
            "distinct_byte_hashes": len(byte_hashes),
            "copper_elements": sorted({r["copper_elements"] for r in runs}),
            "seconds": [r["seconds"] for r in runs],
            # The ruler, carried beside the number (north-star: "every number
            # carries the ruler it was measured with").
            "autorouter_effort": efforts,
            "effort_forced_to": args.effort or None,
            "loadavg": [r["loadavg_at_start"] for r in runs],
            "parts_unresolved": [r["parts_unresolved"] for r in runs],
            "supplier_rate_limited": [r["supplier_rate_limited"] for r in runs],
            "detail": runs,
        }
        # A byte difference the network caused is not a byte difference the
        # toolchain caused, and reading it as one sends the next agent after
        # the wrong bug. Say which it was.
        varying_inputs = (
            len({r["parts_unresolved"] for r in runs}) > 1
            or len({r["supplier_rate_limited"] for r in runs}) > 1
        )
        report["inputs_varied_between_runs"] = varying_inputs
        if varying_inputs:
            report["inputs_varied_note"] = (
                "the supplier catalogue answered these runs differently "
                f"(unresolved parts {[r['parts_unresolved'] for r in runs]}, "
                f"HTTP 429s {[r['supplier_rate_limited'] for r in runs]}) — "
                "these are not builds of the same board, and no seed can make "
                "them equal. Pin the parts in parts.json, or build with "
                "CIRCUIT_PARTS_ENGINE=off."
            )
        if args.mode == "pipeline":
            # Which packet files are the same in every run, and which are not.
            names = sorted({n for r in runs for n in r.get("packet", {})})
            stable = [
                n for n in names if len({r.get("packet", {}).get(n) for r in runs}) == 1
            ]
            report["packet_files"] = len(names)
            report["packet_stable"] = stable
            report["packet_unstable"] = [n for n in names if n not in set(stable)]

        matched_reference = None
        if args.compare is not None:
            reference = json.loads(args.compare.read_text(encoding="utf-8"))
            ref_route = {r["route_bytes"] for r in reference["detail"]}
            ref_bytes = {r["bytes"] for r in reference["detail"]}
            matched_reference = {
                "against": str(args.compare),
                "label": reference.get("label"),
                "background_load": reference.get("background_load"),
                "route_bytes_match": ref_route == route_bytes,
                "whole_file_match": ref_bytes == byte_hashes,
            }
            report["compared_to"] = matched_reference
        if len(geometry) > 1:
            a = Path(runs[0]["path"])
            b = next(Path(r["path"]) for r in runs[1:] if r["geometry"] != runs[0]["geometry"])
            report["first_divergence"] = diff_summary(a, b)
        print(json.dumps(report, indent=2))
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(
            f"{report['label']}: {args.runs} runs, load={args.load}, "
            f"effort={efforts or 'default'}, copper={report['copper_elements']} — "
            f"route bytes {'IDENTICAL' if report['deterministic_route_bytes'] else 'DIFFER'}, "
            f"geometry {'IDENTICAL' if report['deterministic_geometry'] else 'DIFFERS'}, "
            f"whole file {'IDENTICAL' if report['deterministic_bytes'] else 'DIFFERS'}",
            file=sys.stderr,
        )
        if varying_inputs:
            print(f"  ! {report['inputs_varied_note']}", file=sys.stderr)
        ok = bool(report["deterministic_route_bytes"])
        if matched_reference is not None:
            print(
                f"  vs {matched_reference['label']} (load="
                f"{matched_reference['background_load']}): route bytes "
                f"{'MATCH' if matched_reference['route_bytes_match'] else 'DIFFER'}, "
                f"whole file "
                f"{'MATCH' if matched_reference['whole_file_match'] else 'DIFFER'}",
                file=sys.stderr,
            )
            ok = ok and bool(matched_reference["route_bytes_match"])
        return 0 if ok else 1
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
