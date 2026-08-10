"""Thin subprocess wrappers around the pinned Node toolchain + kicad-cli.

The only module that names binaries (contract §1 "the ffmpeg posture").
Everything runs out-of-process:

* ``tscircuit-cli`` from ``<toolchain>/node_modules/.bin`` — resolved via env
  ``CIRCUIT_TOOLCHAIN`` first, then the repo's ``toolchain/`` directory found
  by walking up from this file (works both from ``packages/circuitpy`` and
  from the vendored copy under ``skills/*/scripts/packages/circuitpy``).
* ``node`` from PATH — used for the small ``_js/*.cjs`` helpers
  (@tscircuit/checks, circuit-to-svg + sharp).
* ``kicad-cli`` probed on PATH, then the macOS app-bundle path. Optional:
  ``kicad_cli_exe()`` returns ``None`` when absent (stage 3/5 degrade per
  contract) — it never raises for absence.

Two environment facts every subprocess needs (verified 2026-08-10):

* the CLI's bin shim spawns a ``tsx`` runner **resolved from PATH** — when
  neither ``bun`` nor ``tsx`` is on PATH it exits 0 having done nothing, so
  ``<toolchain>/node_modules/.bin`` is prepended to PATH; and
* TSX eval resolves ``react/jsx-runtime`` from the board file's directory
  (workspace projects have no node_modules), so ``NODE_PATH`` is pointed at
  ``<toolchain>/node_modules``.

Helpers raise plain ``RuntimeError`` with an 800-char output tail /
``TimeoutError`` on budget exhaustion — callers wrap into the
:mod:`circuitpy.errors` hierarchy at their own boundary.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

_OUTPUT_TAIL_CHARS = 800

TOOLCHAIN_ENV = "CIRCUIT_TOOLCHAIN"
KICAD_APP_BUNDLE = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"


def toolchain_dir() -> Path:
    """The toolchain directory: env ``CIRCUIT_TOOLCHAIN`` > repo default
    (nearest ancestor of this file containing ``toolchain/package.json``)."""
    override = os.environ.get(TOOLCHAIN_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        candidate = ancestor / "toolchain"
        if (candidate / "package.json").is_file():
            return candidate
    raise RuntimeError(
        "no toolchain directory found: set CIRCUIT_TOOLCHAIN or run from a "
        "checkout containing toolchain/package.json"
    )


def node_modules_dir() -> Path:
    d = toolchain_dir() / "node_modules"
    if not d.is_dir():
        raise RuntimeError(
            f"toolchain not installed ({d} missing) — run scripts/setup-toolchain.sh"
        )
    return d


def tscircuit_cli_exe() -> str:
    exe = node_modules_dir() / ".bin" / "tscircuit-cli"
    if not exe.is_file():
        raise RuntimeError(
            f"tscircuit-cli not found at {exe} — run scripts/setup-toolchain.sh "
            "(never `npx tsci`: that is an unrelated npm package)"
        )
    return str(exe)


def node_exe() -> str:
    exe = shutil.which("node")
    if not exe:
        raise RuntimeError("node not found on PATH (need node >= 22)")
    return exe


def kicad_cli_exe() -> str | None:
    """kicad-cli, or ``None`` when not installed. Never raises for absence."""
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    if Path(KICAD_APP_BUNDLE).is_file():
        return KICAD_APP_BUNDLE
    return None


def subprocess_env() -> dict[str, str]:
    """os.environ + the toolchain's .bin on PATH + NODE_PATH (see module doc)."""
    modules = node_modules_dir()
    env = dict(os.environ)
    env["PATH"] = f"{modules / '.bin'}{os.pathsep}{env.get('PATH', '')}"
    env["NODE_PATH"] = str(modules)
    return env


@dataclass(frozen=True)
class RunResult:
    returncode: int
    output: str  # stdout + stderr combined (Node CLIs chatter on both)


def _run(
    cmd: Sequence[str],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = None,
    check: bool,
    env: dict[str, str] | None = None,
    what: str,
) -> RunResult:
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"{what} timed out after {timeout:g}s: {' '.join(list(cmd)[:6])}…"
        ) from exc
    output = proc.stdout.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        tail = output.strip()[-_OUTPUT_TAIL_CHARS:]
        raise RuntimeError(
            f"{what} failed (exit {proc.returncode}): {tail or 'no output'}"
        )
    return RunResult(returncode=proc.returncode, output=output)


def run_cli(
    args: Sequence[str],
    *,
    cwd: Path | str,
    timeout: float | None = None,
    check: bool = True,
) -> RunResult:
    """Run ``tscircuit-cli <args>``. With ``check=False`` the caller gates on
    produced artifacts instead of the exit code (the contract's standing rule:
    the CLI exits 0 with real errors and 1 only on fatal eval failures)."""
    return _run(
        [tscircuit_cli_exe(), *args],
        cwd=cwd,
        timeout=timeout,
        check=check,
        env=subprocess_env(),
        what="tscircuit-cli",
    )


def run_node(
    args: Sequence[str],
    *,
    cwd: Path | str | None = None,
    timeout: float | None = None,
) -> str:
    """Run ``node <args>`` with NODE_PATH at the toolchain; return stdout+stderr
    text. Raises ``RuntimeError`` (800-char tail) on nonzero exit."""
    return _run(
        [node_exe(), *args],
        cwd=cwd,
        timeout=timeout,
        check=True,
        env=subprocess_env(),
        what="node",
    ).output


def run_kicad(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    ok_codes: tuple[int, ...] = (0,),
) -> RunResult:
    """Run ``kicad-cli <args>``. ``ok_codes`` widens acceptance — erc/drc with
    ``--exit-code-violations`` exit 5 when violations exist, which is data,
    not failure. Raises ``RuntimeError`` when kicad-cli is absent."""
    exe = kicad_cli_exe()
    if exe is None:
        raise RuntimeError("kicad-cli not installed (brew install --cask kicad)")
    result = _run(
        [exe, *args], timeout=timeout, check=False, env=None, what="kicad-cli"
    )
    if result.returncode not in ok_codes:
        tail = result.output.strip()[-_OUTPUT_TAIL_CHARS:]
        raise RuntimeError(
            f"kicad-cli failed (exit {result.returncode}): {tail or 'no output'}"
        )
    return result


def helper_js(name: str) -> str:
    """Absolute path of a packaged ``_js/*.cjs`` helper."""
    path = Path(__file__).resolve().parent / "_js" / name
    if not path.is_file():
        raise RuntimeError(f"packaged helper missing: {path}")
    return str(path)


_versions_cache: dict[str, str | None] | None = None


def versions(*, refresh: bool = False) -> dict[str, str | None]:
    """Pinned toolchain versions for sidecars + idempotence checks:
    ``{"tscircuit": "0.0.2279", "checks": "0.0.152", "kicadCli": "10.0.5"|None}``.
    Cached per process (``refresh=True`` re-probes)."""
    global _versions_cache
    if _versions_cache is not None and not refresh:
        return dict(_versions_cache)
    modules = node_modules_dir()

    def _pkg_version(pkg: str) -> str | None:
        try:
            manifest = json.loads(
                (modules / pkg / "package.json").read_text(encoding="utf-8")
            )
            value = manifest.get("version")
            return str(value) if value else None
        except (OSError, ValueError):
            return None

    kicad_version: str | None = None
    if kicad_cli_exe() is not None:
        try:
            result = run_kicad(["version"], timeout=30)
            kicad_version = result.output.strip().splitlines()[0].strip() or "unknown"
        except (RuntimeError, TimeoutError, IndexError):
            kicad_version = "unknown"
    _versions_cache = {
        "tscircuit": _pkg_version("tscircuit"),
        "checks": _pkg_version("@tscircuit/checks"),
        "kicadCli": kicad_version,
    }
    return dict(_versions_cache)
