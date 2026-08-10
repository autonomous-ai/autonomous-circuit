"""pytest fixtures for the dramacode skill.

The real ``dramapy.generation.generate_episode`` is Track B's deliverable
(in flight — this skill codes against the CONTRACT's shapes only, per
``docs/video-interfaces.md``). This conftest materializes a lightweight
stub dramapy into a tempdir and injects it via
``DRAMACODE_TEST_DRAMAPY_PATH`` so the suite exercises the runner, the
sandbox, and the JSON contract without real provider renders.

The stub:

  * Provides ``dramapy.generation.generate_episode`` matching the frozen
    contract §1 signature, plus the full error hierarchy
    (``GenerationError`` → ``ProjectShapeError`` / ``GeneratorRuntimeError``
    / ``SpecValidationError`` / ``ProviderError`` / ``ExportError``).
  * Loads the episode module, calls ``gen_episode()``, enforces the
    envelope rule (unknown keys raise ``TypeError``), and duck-reads the
    Episode exactly like the real pipeline (attributes or dict keys).
  * Writes the contract §1 artifact set — a REAL (tiny) mp4 via ffmpeg
    (0.5s black clip; zero-length placeholder if ffmpeg is absent), the
    canonical camelCase ``.episode.json`` sidecar (written BEFORE the
    final mp4, per the ordering rule), ``.srt`` when any dialogue,
    ``<stem>_shots/`` clips + sidecars, ``<stem>_review/`` pngs.
  * Runs the two cheap §1 verifiers the tests rely on: ``hook_too_long``
    and ``no_cliffhanger``; envelope warnings pass through as declared.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest


def _build_stub_dramapy(stub_root: Path) -> None:
    """Materialize a tiny ``dramapy`` package at ``stub_root/dramapy/``."""
    pkg = stub_root / "dramapy"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(dedent('''
        """Test stub for dramapy. See tests/conftest.py for the why."""
        from . import generation  # noqa: F401
    ''').lstrip())

    (pkg / "generation.py").write_text(dedent('''
        """Stub dramapy.generation mirroring contract §1's generate_episode.

        All imports are at module top so the runner's pre-import step
        caches them BEFORE the sandbox import restriction activates —
        exactly how the real package must behave.
        """
        from __future__ import annotations

        import hashlib
        import importlib.util
        import json
        import os
        import shutil
        import subprocess
        from pathlib import Path
        from typing import Any


        class GenerationError(Exception):
            """Base error type per contract §1."""


        class ProjectShapeError(GenerationError):
            pass


        class GeneratorRuntimeError(GenerationError):
            pass


        class SpecValidationError(GenerationError):
            pass


        class ProviderError(GenerationError):
            pass


        class ExportError(GenerationError):
            pass


        # A valid 1x1 gray PNG — placeholder for _poster.png / _board.png.
        _PNG_1PX = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108000000003a7e"
            "9b550000000a49444154789c636000000002000148afa4710000000049454e"
            "44ae426082"
        )


        def _get(obj: Any, key: str, default=None):
            """Contract §1 duck-typing: attributes or dict keys, never
            isinstance."""
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default)


        def _load_episode_module(source_path: Path):
            source_path = Path(source_path)
            if source_path.is_dir():
                main_py = source_path / "main.py"
                if not main_py.exists():
                    raise ProjectShapeError(f"no main.py in {source_path}")
                target = main_py
            else:
                target = source_path
            # Synthetic module name — the runner's import-attribution hook
            # treats it as user code.
            spec = importlib.util.spec_from_file_location(
                "__dramacode_episode__", str(target)
            )
            if spec is None or spec.loader is None:
                raise GeneratorRuntimeError(f"could not load spec for {target}")
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except (SyntaxError, ImportError):
                raise  # §1: these propagate untouched; runner maps by type
            except Exception as e:
                raise GeneratorRuntimeError(
                    f"episode module failed to load: {e}"
                ) from e
            return module


        def _resolve_envelope(module) -> dict:
            fn = getattr(module, "gen_episode", None)
            if not callable(fn):
                raise ProjectShapeError(
                    "episode file defines no gen_episode() at module scope"
                )
            try:
                envelope = fn()
            except (SyntaxError, ImportError):
                raise
            except AssertionError as e:
                # §1: AssertionError from validate() re-raised with message.
                raise ProjectShapeError(f"validate() failed: {e}") from e
            except Exception as e:
                raise GeneratorRuntimeError(f"gen_episode() raised: {e}") from e
            if not isinstance(envelope, dict):
                raise ProjectShapeError(
                    "gen_episode() must return the envelope dict "
                    "{'episode': Episode, 'warnings': [...]} — got "
                    f"{type(envelope).__name__}"
                )
            unknown = set(envelope) - {"episode", "warnings"}
            if unknown:
                raise TypeError(
                    f"unknown envelope key(s): {', '.join(sorted(unknown))}"
                )
            if "episode" not in envelope:
                raise ProjectShapeError("envelope is missing 'episode'")
            return envelope


        def _flatten_shots(episode) -> list:
            shots = []
            for scene in (_get(episode, "scenes", []) or []):
                shots.extend(_get(scene, "shots", []) or [])
            return shots


        def _verify(episode, shots) -> list[dict]:
            """The two cheap §1 verifiers the skill's tests rely on."""
            warnings: list[dict] = []
            hook_max = float(_get(episode, "hook_max_s", 5.0) or 5.0)
            lead = 0.0
            for s in shots:
                if _get(s, "kind") != "establish":
                    break
                lead += float(_get(s, "duration_s", 0.0) or 0.0)
            if lead > hook_max:
                warnings.append({
                    "part": str(_get(shots[0], "id", "s?")) if shots else "episode",
                    "kind": "hook_too_long",
                    "detail": (
                        f"first non-establish beat lands after {lead:.1f}s "
                        f"(hook_max_s={hook_max:.1f})"
                    ),
                    "severity": "warning",
                })
            if not _get(episode, "cliffhanger") or not shots:
                warnings.append({
                    "part": "episode",
                    "kind": "no_cliffhanger",
                    "detail": "cliffhanger unset or final shot missing",
                    "severity": "warning",
                })
            return warnings


        def _render_tiny_clip(path: Path) -> None:
            """A real 0.5s black mp4 via ffmpeg; zero-length placeholder
            when ffmpeg is unavailable."""
            try:
                proc = subprocess.run(
                    ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i", "color=c=black:s=108x192:d=0.5:r=24",
                     "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                    capture_output=True, timeout=30,
                )
                if proc.returncode == 0 and path.is_file():
                    return
            except Exception:
                pass
            path.write_bytes(b"")


        def _srt_timestamp(t: float) -> str:
            ms = int(round(t * 1000))
            h, ms = divmod(ms, 3600000)
            m, ms = divmod(ms, 60000)
            s, ms = divmod(ms, 1000)
            return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


        def _sha256_file(path: Path) -> str:
            h = hashlib.sha256()
            with path.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            return h.hexdigest()


        def generate_episode(
            source_path,
            output_path,
            *,
            provider: str | None = None,
            max_render_s: float | None = None,
        ) -> dict[str, object]:
            """Stub of dramapy.generation.generate_episode per contract §1."""
            source_path = Path(source_path)
            output_path = Path(output_path)
            if output_path.suffix != ".mp4":
                raise ProjectShapeError(
                    f"output_path suffix must be .mp4, got {output_path.suffix!r}"
                )
            out_dir = output_path.parent
            stem = output_path.stem
            out_dir.mkdir(parents=True, exist_ok=True)

            module = _load_episode_module(source_path)
            envelope = _resolve_envelope(module)
            episode = envelope["episode"]
            declared = envelope.get("warnings") or []

            shots = _flatten_shots(episode)
            duration_s = sum(
                float(_get(s, "duration_s", 0.0) or 0.0) for s in shots
            )

            warnings = _verify(episode, shots)
            for w in declared:
                warnings.append({
                    "part": str(_get(w, "part", "")),
                    "kind": str(_get(w, "kind", "functional")),
                    "detail": str(_get(w, "detail", "")),
                    "severity": str(_get(w, "severity", "warning")),
                })

            provider_name = provider or os.environ.get("VIDEO_PROVIDER") or "mock"

            # --- Render one tiny real clip; every artifact copies it. ---
            tmp_clip = out_dir / f".{stem}_stubclip.mp4"
            _render_tiny_clip(tmp_clip)

            shots_dir = out_dir / f"{stem}_shots"
            shots_dir.mkdir(exist_ok=True)
            shots_meta = []
            shots_result = []
            for s in shots:
                sid = str(_get(s, "id", "shot"))
                clip = shots_dir / f"shot_{sid}.mp4"
                shutil.copyfile(tmp_clip, clip)
                sidecar = shots_dir / f"shot_{sid}.json"
                sidecar.write_text(json.dumps({
                    "cast": list(_get(s, "cast", []) or []),
                    "draws": 1,
                    "prompt": str(_get(s, "prompt", "") or ""),
                    "provider": provider_name,
                    "renderMs": 0,
                }, sort_keys=True, separators=(",", ":")))
                shots_meta.append({
                    "id": sid,
                    "path": f"{stem}_shots/shot_{sid}.mp4",
                    "jsonPath": f"{stem}_shots/shot_{sid}.json",
                })
                shots_result.append({
                    "id": sid,
                    "path": str(clip),
                    "duration_s": float(_get(s, "duration_s", 0.0) or 0.0),
                    "status": "rendered",
                })

            # Subtitles when any dialogue.
            srt_path = None
            dialogue = [
                s for s in shots
                if _get(s, "kind") == "dialogue" and _get(s, "line")
            ]
            if dialogue:
                t = 0.0
                lines = []
                idx = 0
                for s in shots:
                    d = float(_get(s, "duration_s", 0.0) or 0.0)
                    if _get(s, "kind") == "dialogue" and _get(s, "line"):
                        idx += 1
                        lines.append(
                            f"{idx}\\n{_srt_timestamp(t)} --> "
                            f"{_srt_timestamp(t + d)}\\n{_get(s, 'line')}\\n"
                        )
                    t += d
                srt_path = out_dir / f"{stem}.srt"
                srt_path.write_text("\\n".join(lines), encoding="utf-8")

            # Review pngs.
            review_dir = out_dir / f"{stem}_review"
            review_dir.mkdir(exist_ok=True)
            (review_dir / "_poster.png").write_bytes(_PNG_1PX)
            (review_dir / "_board.png").write_bytes(_PNG_1PX)

            # Sidecar — written BEFORE the final mp4 (ordering rule §1).
            src_hash = _sha256_file(source_path if source_path.is_file()
                                    else source_path / "main.py")
            fingerprint = src_hash
            series_py = (source_path.parent.parent / "series.py"
                         if source_path.is_file() else source_path / "series.py")
            if series_py.is_file():
                fingerprint = hashlib.sha256(
                    (src_hash + _sha256_file(series_py)).encode()
                ).hexdigest()

            validation: dict = {
                "durationS": duration_s,
                "shotCount": len(shots),
            }
            if warnings:
                validation["warnings"] = warnings

            metadata_path = out_dir / f"{stem}.episode.json"
            metadata_path.write_text(json.dumps({
                "entryKind": "episode",
                "episode": {
                    "durationS": duration_s,
                    "fps": 24,
                    "number": int(_get(episode, "number", 0) or 0),
                    "path": f"{stem}.mp4",
                    "resolution": [1080, 1920],
                    "title": str(_get(episode, "title", "") or ""),
                },
                "generator": "dramapy",
                "provider": {"model": "stub", "name": provider_name},
                "shots": shots_meta,
                "source": {
                    "fingerprint": fingerprint,
                    "hash": src_hash,
                    "kind": "python",
                    "path": str(source_path),
                },
                "validation": validation,
                "_stub": True,
            }, sort_keys=True, separators=(",", ":")))

            shutil.copyfile(tmp_clip, output_path)
            tmp_clip.unlink(missing_ok=True)

            return {
                "episode_path": output_path,
                "srt_path": srt_path,
                "metadata_path": metadata_path,
                "duration_s": duration_s,
                "shot_count": len(shots),
                "shots": shots_result,
                "warnings": warnings,
            }
    ''').lstrip())


# Build the stub once per session and expose it to the subprocess runner
# via DRAMACODE_TEST_DRAMAPY_PATH (recognized by common/runner.py and the
# review CLI).
@pytest.fixture(scope="session", autouse=True)
def _dramapy_stub():
    stub_root = Path(tempfile.mkdtemp(prefix="dramacode-dramapy-stub-"))
    _build_stub_dramapy(stub_root)
    prev = os.environ.get("DRAMACODE_TEST_DRAMAPY_PATH")
    os.environ["DRAMACODE_TEST_DRAMAPY_PATH"] = str(stub_root)
    # Also make it visible to in-process imports (test helper code).
    sys.path.insert(0, str(stub_root))
    try:
        yield stub_root
    finally:
        if prev is None:
            os.environ.pop("DRAMACODE_TEST_DRAMAPY_PATH", None)
        else:
            os.environ["DRAMACODE_TEST_DRAMAPY_PATH"] = prev
        if str(stub_root) in sys.path:
            sys.path.remove(str(stub_root))
        shutil.rmtree(stub_root, ignore_errors=True)
