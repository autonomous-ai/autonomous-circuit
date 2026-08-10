"""Sandboxed dramapy-backed runner — invoked once per agent tool call.

The agent runs ``python scripts/drama episodes/ep001.py`` (or a project
dir with a ``main.py``). That spawns a fresh subprocess that pre-imports
``dramapy`` + ``dramalib`` (so their lazy deps land before the import
allow-list kicks in), enforces rlimits, installs the import allow-list,
and hands the episode file to ``dramapy.generation.generate_episode``.
dramapy loads the episode module, calls the user's ``gen_episode()``
inside this same sandbox process, and writes (contract §1):

  <stem>.mp4  <stem>.episode.json  <stem>.srt  <stem>_shots/  <stem>_review/

The parent (this module's ``run_sandboxed_sync``) handles wall-clock kills
and JSON unwrapping for the CLI.

Sandbox attribution (the donor's ``_caller_is_user_code`` trick, adapted):
USER code (the episode module, ``series``, project modules) may never
import ``subprocess`` / ``socket`` / ``urllib`` / ``os`` / …, while dramapy
itself legitimately runs ffmpeg via ``subprocess`` and — per contract §3 —
providers run outside the user-code sandbox in the parent process, so the
provider's own network client is dramapy's business, never the user's.
We enforce this by attributing each ``__import__`` to its calling module:
``dramapy.*`` / ``dramalib.*`` / ``common.*`` are runtime, everything else
is user code.
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


# Modules USER code must never import — even when they are already in
# sys.modules because dramapy / the runner pre-warmed them (dramapy needs
# subprocess for ffmpeg, os/pathlib for artifact IO). The attribution
# check in _restrict_imports keeps them out of user reach while leaving
# dramapy's own use intact.
DENIED_USER_MODULES = frozenset({
    "os", "pathlib", "io",
    "subprocess", "shutil", "tempfile", "glob",
    "urllib", "socket", "http", "ssl", "ftplib", "smtplib", "telnetlib",
    "requests", "httpx",
    "ctypes", "mmap",
    "pickle", "marshal", "shelve",
    "multiprocessing", "threading", "_thread", "asyncio",
    "pty", "signal",
    "importlib", "builtins",
})


# Fresh (not-yet-cached) imports allowed for anyone. dramapy's own deps are
# pre-warmed before restriction, so this list stays short.
ALLOWED_ROOT_MODULES = frozenset({
    "__future__",
    "math", "random", "json", "re", "string", "textwrap",
    "typing", "dataclasses", "enum", "functools", "itertools",
    "operator", "collections", "copy", "fractions", "decimal",
    "abc", "contextlib", "warnings", "weakref",
    # Safe stdlib the drama runtime imports lazily (rendering, hashing,
    # logging, timing). Harmless for user code too — no IO/exec surface.
    "atexit", "logging", "datetime", "time", "secrets", "shlex",
    "mimetypes", "unicodedata", "zlib", "binascii", "struct", "base64",
    "hashlib", "uuid", "wave", "bisect", "heapq", "statistics", "csv",
    "platform", "types", "colorsys",
    # Codec machinery loaded lazily on first hostname resolution (the
    # 'idna' codec imports stringprep) — needed by hosted providers.
    "encodings", "stringprep", "idna",
    # The drama runtime + the skill's own helper library.
    "dramapy", "dramalib",
    # Skill-internal plumbing — defense in depth if import order shifts.
    "common",
})


# Video work is slower than CAD: the mock provider synthesizes + stitches
# real clips through ffmpeg, and fal renders remotely. Contract §3 exposes
# --wall-clock-s for overrides.
def _default_wall_clock_s() -> float:
    """Wall-clock budget for one generate_episode run. The mock/animatic
    provider is local ffmpeg (fast: a 15-shot episode ~30s). Hosted providers
    render remotely at minutes-per-shot — a full episode, even fanned across
    the render pool, runs many minutes — so the budget must be large or the
    sandbox kills the render before the first real clip lands (this was the
    "blank video" bug). VIDEO_WALL_CLOCK_S overrides explicitly."""
    override = os.environ.get("VIDEO_WALL_CLOCK_S", "").strip()
    if override:
        try:
            return max(30.0, float(override))
        except ValueError:
            pass
    provider = os.environ.get("VIDEO_PROVIDER", "mock").strip().lower()
    return 120.0 if provider in ("", "mock") else 2400.0  # 40 min for hosted


WALL_CLOCK_TIMEOUT_S = _default_wall_clock_s()
CPU_TIMEOUT_S = 60
MEMORY_LIMIT_BYTES = 1024 * 1024 * 1024  # 1 GiB (contract §3 RLIMIT_AS)
OUTPUT_FILE_LIMIT_BYTES = 512 * 1024 * 1024  # 512 MiB — 1080p @ 12 Mbps x 120s
# is ~180 MiB, so a legit episode never hits this; a runaway writer does.


# -- Subprocess (in-process) entrypoint -------------------------------------


def _enforce_rlimits(wall_clock_s: float = WALL_CLOCK_TIMEOUT_S) -> None:
    # RLIMIT_CPU counts CPU-seconds across ALL threads and is inherited by
    # ffmpeg children; encode work is parallel across cores, so budget the
    # cap as the wall deadline x cores — the parent's wall-clock kill stays
    # the real deadline and this remains a runaway backstop (donor rule).
    if resource is None:
        # Windows: no rlimits. The parent's wall-clock kill remains.
        return
    ncores = os.cpu_count() or 1
    cpu_limit = max(CPU_TIMEOUT_S, int(wall_clock_s * ncores))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES,) * 2)
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_FILE_LIMIT_BYTES,) * 2)
    except (ValueError, OSError):
        pass


# Module prefixes that are the drama RUNTIME, never the user's episode code.
# dramapy/dramalib/common are ours; the concurrency stdlib is included because
# dramapy fans shots across a ThreadPoolExecutor and that machinery
# (concurrent.futures.thread → threading → _thread → queue) legitimately
# imports threading internals — those callers must not be judged "user code".
# The user's own modules load under other names (series / _dramapy_ep_* /
# project modules), so they still can't reach the denied set through here.
_RUNTIME_PREFIXES = (
    "dramapy", "dramalib", "common",
    "concurrent", "threading", "_thread", "queue", "asyncio",
)


def _caller_is_user_code(globals_: dict | None) -> bool:
    """Attribute an ``__import__`` call to user code or the drama runtime.

    dramapy loads the episode module under a synthetic name (``_dramapy_ep_*``
    or the file stem); the runtime lives under the prefixes above. Anything
    else — the episode module, ``series``, project modules — is user code.
    """
    if not globals_:
        return False
    name = globals_.get("__name__", "") or ""
    for prefix in _RUNTIME_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return False
    return True


def _restrict_imports(extra_allowed: frozenset[str] | None = None) -> None:
    """Install an import hook that rejects modules outside the allow-list.

    ``extra_allowed`` are module roots living inside the user's project
    directory (``series``, sibling packages) — discovered at runtime so
    ``import series`` works from any episode file.

    A module already in ``sys.modules`` is normally allowed (pre-warmed
    before restriction) — EXCEPT the ``DENIED_USER_MODULES`` when the
    caller is user code, per the attribution rule above.
    """
    import builtins
    real_import = builtins.__import__
    allow_set = ALLOWED_ROOT_MODULES | (extra_allowed or frozenset())

    def restricted(name, globals=None, locals=None, fromlist=(), level=0):
        if level > 0:
            # Relative import — only resolvable from inside a package that
            # already passed this hook, so the root was vetted on entry
            # (e.g. concurrent.futures lazily does `from .thread import …`,
            # which arrives here as name='thread', level=1).
            return real_import(name, globals, locals, fromlist, level)
        root = name.split(".")[0]
        if root in DENIED_USER_MODULES and _caller_is_user_code(globals):
            raise ImportError(
                f"import of {root!r} is not allowed in dramacode sandbox"
            )
        if root in sys.modules:
            return real_import(name, globals, locals, fromlist, level)
        if root not in allow_set:
            raise ImportError(
                f"import of {root!r} is not allowed in dramacode sandbox"
            )
        return real_import(name, globals, locals, fromlist, level)

    builtins.__import__ = restricted


def find_project_root(source_path: Path) -> Path:
    """The project root is the nearest ancestor holding ``series.py``.

    For ``<project>/episodes/ep001.py`` that's ``<project>/``. When no
    ``series.py`` exists up-tree (a standalone demo file), the file's own
    directory is the root. For a directory input, the directory itself
    (or its ``series.py``-bearing ancestor) wins.
    """
    start = source_path if source_path.is_dir() else source_path.parent
    for cand in (start, *start.parents):
        if (cand / "series.py").is_file():
            return cand
    return start


def _discover_project_modules(project_root: Path) -> frozenset[str]:
    """Package + module roots directly under the project root.

    ``series`` (top-level .py) and any ``__init__.py`` packages count;
    ``main.py`` is the entrypoint, not an import target.
    """
    roots: set[str] = set()
    if not project_root.is_dir():
        return frozenset()
    for p in project_root.iterdir():
        if p.is_dir() and (p / "__init__.py").exists():
            roots.add(p.name)
        elif p.is_file() and p.suffix == ".py" and p.stem != "main":
            roots.add(p.stem)
    return frozenset(roots)


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


def _workspace_relative(path: Path | str) -> str:
    """Contract §3: paths in the stdout JSON are workspace-relative.

    The CLI runs with cwd = the user's workspace, so relpath against cwd
    is workspace-relative; a path that escapes the workspace stays
    absolute rather than growing ``../`` chains.
    """
    p = Path(path)
    try:
        rel = os.path.relpath(p, Path.cwd())
    except ValueError:  # different drive (Windows)
        return str(p)
    if rel.startswith(".."):
        return str(p)
    return rel


def in_subprocess_main(
    source_path: str,
    out_dir: str,
    stem: str,
    provider: str = "",
    wall_clock_s: float = WALL_CLOCK_TIMEOUT_S,
) -> int:
    """Entry point invoked inside the subprocess.

    Pre-imports happen first (so the allow-list does not need to enumerate
    every transitive dramapy dep), then rlimits, then the import
    restriction, then we hand the episode to dramapy.

    ``source_path`` is ``episodes/epNNN.py`` or a directory with
    ``main.py`` — dramapy's ``generate_episode`` accepts both (§1).
    """
    import traceback

    # 1. Pre-import dramapy. It lives at <skill>/scripts/packages/dramapy/
    #    after the vendor step (build-skill-runtimes.sh). Tests inject a
    #    stub via DRAMACODE_TEST_DRAMAPY_PATH (the parent CLI prepends it
    #    to sys.path before importing us — see run_sandboxed_sync).
    _skill_root = Path(__file__).resolve().parents[2]
    _packages_dir = _skill_root / "scripts" / "packages"
    if str(_packages_dir) not in sys.path:
        sys.path.insert(0, str(_packages_dir))
    # The injected test stub must ALWAYS beat the vendored package —
    # both are regular packages, so path order decides. Re-hoist it.
    _test_path = os.environ.get("DRAMACODE_TEST_DRAMAPY_PATH")
    if _test_path:
        while _test_path in sys.path:
            sys.path.remove(_test_path)
        sys.path.insert(0, _test_path)
    import dramapy  # noqa: F401
    import dramapy.generation as _dramapy_gen  # noqa: F401

    # 2. Make the skill's own dramalib package importable + pre-warmed.
    if str(_skill_root) not in sys.path:
        sys.path.insert(0, str(_skill_root))
    try:
        import dramalib  # noqa: F401
        import dramalib.bible  # noqa: F401
        import dramalib.helpers  # noqa: F401
        import dramalib.spec  # noqa: F401
        import dramalib.tables  # noqa: F401
        import dramalib.tropes  # noqa: F401
    except Exception:
        pass

    # 3. Pre-warm the modules dramapy needs after restriction: ffmpeg via
    #    subprocess, artifact IO, hashing, provider transport. User code
    #    can't reach any of these — DENIED_USER_MODULES + attribution.
    import atexit  # noqa: F401
    import base64  # noqa: F401
    import datetime  # noqa: F401
    import glob as _glob  # noqa: F401
    import hashlib  # noqa: F401
    import http.client  # noqa: F401
    import importlib  # noqa: F401
    import importlib.util  # noqa: F401
    import io  # noqa: F401
    import logging  # noqa: F401
    import mimetypes  # noqa: F401
    # Concurrent shot rendering: dramapy fans shots across a thread pool.
    # Pre-warm the machinery (threading is denied to USER code, but caching
    # it here lets dramapy — non-user — create a ThreadPoolExecutor after the
    # lockdown; the attribution rule still blocks user code from importing it).
    import concurrent.futures  # noqa: F401
    import concurrent.futures.thread  # noqa: F401
    import queue as _queue  # noqa: F401  (ThreadPoolExecutor's work queue)
    import threading  # noqa: F401
    import secrets  # noqa: F401
    import shlex  # noqa: F401
    import shutil  # noqa: F401
    import socket  # noqa: F401
    import ssl  # noqa: F401
    import struct  # noqa: F401
    import tempfile  # noqa: F401
    import time  # noqa: F401
    import urllib.request  # noqa: F401
    import uuid  # noqa: F401
    import wave  # noqa: F401
    # (os / pathlib / subprocess / json are already cached — we import them
    # at module top.)

    source_p = Path(source_path).resolve()
    out_dir_p = Path(out_dir).resolve()
    out_dir_p.mkdir(parents=True, exist_ok=True)
    output_path = out_dir_p / f"{stem}.mp4"

    # 4. Project mode: the root (dir containing series.py) goes on
    #    sys.path so episode files do ``import series`` (§1).
    project_root = find_project_root(source_p)
    extra = _discover_project_modules(project_root)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    # 5. Lock down the sandbox.
    _enforce_rlimits(wall_clock_s)
    _restrict_imports(extra)

    # 6. Hand off to dramapy.
    try:
        result = _dramapy_gen.generate_episode(
            source_path=source_p,
            output_path=output_path,
            provider=(provider or None),
        )
    except Exception as e:
        code = _map_error_code(e, _dramapy_gen)
        oversized = _drain_oversized_outputs(out_dir_p)
        if oversized:
            cap_mib = OUTPUT_FILE_LIMIT_BYTES // (1024 * 1024)
            message = (
                f"output too large: ({', '.join(sorted(oversized))}) hit the "
                f"{cap_mib} MiB sandbox file cap and was discarded. Lower the "
                f"episode length or bitrate."
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

    payload = _build_success_payload(result, output_path, out_dir_p, stem)
    _emit(payload)
    return 0


def _map_error_code(e: Exception, gen_mod: Any) -> str:
    """Contract §3's closed error-code set, from §1's error hierarchy.

    ``SyntaxError`` / ``ImportError`` propagate from user code untouched
    (§1) — mapped by type first. A ``ProviderError`` whose message names a
    timeout becomes ``RENDER_TIMEOUT`` (the per-shot render budget).
    """
    def _t(name: str):
        return getattr(gen_mod, name, ())

    if isinstance(e, SyntaxError):
        return "SYNTAX_ERROR"
    if isinstance(e, ImportError):
        return "RUNTIME_ERROR"
    if isinstance(e, _t("SpecValidationError")):
        return "VALIDATION_FAILED"
    if isinstance(e, _t("ProjectShapeError")):
        return "VALIDATION_FAILED"
    if isinstance(e, _t("GeneratorRuntimeError")):
        return "RUNTIME_ERROR"
    if isinstance(e, _t("ProviderError")):
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            return "RENDER_TIMEOUT"
        return "PROVIDER_ERROR"
    if isinstance(e, _t("ExportError")):
        return "EXPORT_ERROR"
    if isinstance(e, _t("GenerationError")):
        return "EXPORT_ERROR"
    if isinstance(e, (TypeError, ValueError, AssertionError)):
        return "VALIDATION_FAILED"
    return "RUNTIME_ERROR"


def _build_success_payload(
    dramapy_result: Any,
    output_path: Path,
    out_dir: Path,
    stem: str,
) -> dict[str, Any]:
    """Normalize dramapy's generate_episode return into contract §3
    ``DramacodeResult``.

    dramapy returns ``dict[str, object]`` (§1, shape intentionally loose);
    we prefer its explicit keys, then fall back to the ``.episode.json``
    sidecar (the frozen camelCase contract), then to filesystem inference —
    so a partial return from a stub still yields a full verdict.
    """
    payload: dict[str, Any] = {"ok": True}
    src: dict[str, Any] = dramapy_result if isinstance(dramapy_result, dict) else {}

    sidecar_path = out_dir / f"{stem}.episode.json"
    sidecar: dict[str, Any] = {}
    if sidecar_path.is_file():
        try:
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            sidecar = {}

    inferred = {
        "episode_path": output_path,
        "srt_path": out_dir / f"{stem}.srt",
        "metadata_path": sidecar_path,
    }
    for key in ("episode_path", "srt_path", "metadata_path"):
        v = src.get(key)
        if v is None:
            cand = inferred[key]
            if cand.exists():
                payload[key] = _workspace_relative(cand)
        else:
            payload[key] = _workspace_relative(v)

    validation = sidecar.get("validation", {}) if isinstance(sidecar, dict) else {}
    duration = src.get("duration_s", validation.get("durationS"))
    if duration is not None:
        payload["duration_s"] = float(duration)
    shot_count = src.get("shot_count", validation.get("shotCount"))
    if shot_count is not None:
        payload["shot_count"] = int(shot_count)

    shots = src.get("shots")
    if not shots and isinstance(sidecar.get("shots"), list):
        shots = [
            {"id": s.get("id", ""), "path": s.get("path", ""),
             "duration_s": s.get("durationS", 0.0), "status": "rendered"}
            for s in sidecar["shots"]
        ]
    if shots:
        payload["shots"] = [
            {
                "id": str(s.get("id", "")),
                "path": _workspace_relative(out_dir / str(s.get("path", "")))
                if not os.path.isabs(str(s.get("path", "")))
                else _workspace_relative(s.get("path", "")),
                "duration_s": float(s.get("duration_s", 0.0) or 0.0),
                "status": str(s.get("status", "rendered")),
            }
            for s in shots
        ]

    warnings = src.get("warnings")
    if warnings is None:
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
        ]
    return payload


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


# -- Parent-side helper -----------------------------------------------------


def run_sandboxed_sync(
    source_path: Path,
    out_dir: Path,
    stem: str,
    *,
    provider: str | None = None,
    wall_clock_s: float = WALL_CLOCK_TIMEOUT_S,
) -> dict[str, Any]:
    """Spawn the worker subprocess and return the parsed JSON result.

    Always returns a dict — never raises — so the agent's downstream code
    can decode and react. Parses ``stdout.splitlines()[-1]`` (last-line
    tolerance: dramapy/ffmpeg may chatter above the JSON line).
    """
    scripts_dir = Path(__file__).resolve().parents[1]  # skills/dramacode/scripts
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(scripts_dir),
    }
    # Provider selection + credentials pass through to dramapy (contract §1:
    # arg > env VIDEO_PROVIDER > "mock"). Two sources, environment winning:
    # ~/.autonomous-video/keys.env (the paste-a-key file) first, then any
    # real env vars set by the server/shell.
    PROVIDER_VARS = (
        "VIDEO_PROVIDER",
        "VIDEO_FORCE_REGEN",
        "FAL_KEY",
        "DASHSCOPE_API_KEY",
        "MINIMAX_API_KEY",
        "VIDEO_FAL_MODEL",
        "VIDEO_DASHSCOPE_MODEL",
        "VIDEO_DASHSCOPE_BASE_URL",
        "VIDEO_MINIMAX_MODEL",
        "VIDEO_MINIMAX_BASE_URL",
        "VIDEO_MOCK_TTS",
        "DRAMACODE_TEST_DRAMAPY_PATH",
    )
    keys_env = Path(os.environ.get("HOME", "/tmp")) / ".autonomous-video" / "keys.env"
    if keys_env.is_file():
        try:
            for line in keys_env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key in PROVIDER_VARS and value and key not in os.environ:
                    env[key] = value
        except OSError:
            pass  # unreadable keys file must never break a render
    for var in PROVIDER_VARS:
        if var in os.environ:
            env[var] = os.environ[var]

    cmd = [
        sys.executable,
        "-c",
        (
            "import sys, os; "
            # Tests inject a fast stub dramapy via this env var (see
            # tests/conftest.py); production uses the vendored copy at
            # scripts/packages/dramapy/.
            "_p = os.environ.get('DRAMACODE_TEST_DRAMAPY_PATH'); "
            "_p and sys.path.insert(0, _p); "
            "from common.runner import in_subprocess_main; "
            "sys.exit(in_subprocess_main(sys.argv[1], sys.argv[2], sys.argv[3], "
            "sys.argv[4], float(sys.argv[5])))"
        ),
        str(source_path),
        str(out_dir),
        stem,
        provider or "",
        str(wall_clock_s),
    ]

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=wall_clock_s,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "ok": False,
            "error": {
                "code": "RENDER_TIMEOUT",
                "message": (
                    f"sandbox timeout after {wall_clock_s}s — a shot render "
                    "or the stitch ran past the wall clock. Re-run with a "
                    "higher --wall-clock-s, or cut shot count/duration."
                ),
            },
            "timed_out": True,
            "stdout": (e.stdout or "")[-2000:] if isinstance(e.stdout, str) else "",
            "stderr": (e.stderr or "")[-2000:] if isinstance(e.stderr, str) else "",
        }

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if not stdout:
        # Likely killed by rlimit (SIGXCPU on Linux/macOS).
        return {
            "ok": False,
            "error": {
                "code": "RENDER_TIMEOUT",
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
    return payload
