"""Shared scaffolding for dramapy tests: real projects on disk, real
generation with the mock provider, NO mocks of dramapy itself (donor test
discipline). Each test module carries its own sys.path bootstrap header;
this module repeats it so files also work standalone."""

from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy import media  # noqa: E402

# Env that could redirect provider selection or unlock network providers —
# popped for every test so CI can never hit a network by accident.
ENV_KEYS = (
    "VIDEO_PROVIDER",
    "FAL_KEY",
    "VIDEO_FAL_MODEL",
    "DASHSCOPE_API_KEY",
    "VIDEO_DASHSCOPE_MODEL",
    "VIDEO_DASHSCOPE_BASE_URL",
    "MINIMAX_API_KEY",
    "VIDEO_MINIMAX_MODEL",
    "VIDEO_MINIMAX_BASE_URL",
)


class ProviderEnvGuard:
    """unittest mixin: isolate provider-related environment per test."""

    def setUp(self) -> None:  # type: ignore[override]
        super().setUp()  # type: ignore[misc]
        self._saved_env = {
            key: os.environ.pop(key) for key in ENV_KEYS if key in os.environ
        }

    def tearDown(self) -> None:  # type: ignore[override]
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self._saved_env)
        super().tearDown()  # type: ignore[misc]


DEFAULT_CAST = (
    {
        "id": "li_wei",
        "name": "Li Wei",
        "look": "woman, 28, sharp bob, gray suit",
        "voice": "f_low_calm",
        "ref_images": [],
    },
    {
        "id": "dorian",
        "name": "Dorian Cross",
        "look": "man, 34, black coat",
        "voice": "m_deep_cold",
        "ref_images": [],
    },
)


def series_dict(
    *,
    title: str = "Contract Bride",
    genre: str = "revenge-romance",
    style: str = "photoreal-drama",
    aspect: str = "9:16",
    resolution: tuple[int, int] = (270, 480),
    fps: int = 24,
    language: str = "en",
) -> dict:
    return {
        "title": title,
        "genre": genre,
        "style": style,
        "aspect": aspect,
        "resolution": resolution,
        "fps": fps,
        "language": language,
    }


def write_series(
    project_root: Path,
    *,
    series: dict | None = None,
    cast: tuple[dict, ...] = DEFAULT_CAST,
    trailer: str = "",
) -> Path:
    """Write a duck-typed (plain dict) series bible. ``trailer`` appends raw
    source, letting tests dirty the file to shift the fingerprint."""
    project_root.mkdir(parents=True, exist_ok=True)
    path = project_root / "series.py"
    path.write_text(
        f"SERIES = {series if series is not None else series_dict()!r}\n"
        f"CAST = {list(cast)!r}\n" + trailer,
        encoding="utf-8",
    )
    return path


def shot(
    shot_id: str,
    kind: str,
    duration_s: float,
    *,
    prompt: str = "placeholder shot",
    cast: list[str] | None = None,
    line: str | None = None,
    emotion: str | None = None,
) -> dict:
    entry: dict = {
        "id": shot_id,
        "kind": kind,
        "duration_s": duration_s,
        "prompt": prompt,
    }
    if cast is not None:
        entry["cast"] = cast
    if line is not None:
        entry["line"] = line
    if emotion is not None:
        entry["emotion"] = emotion
    return entry


def episode_dict(
    shots: list[dict],
    *,
    number: int = 1,
    title: str = "The Slap",
    hook_max_s: float = 5.0,
    cliffhanger: str | None = "freeze on the burning letter",
    bgm: str | None = None,
    burn_subtitles: bool = False,
    scene_id: str = "s1",
) -> dict:
    return {
        "number": number,
        "title": title,
        "hook_max_s": hook_max_s,
        "scenes": [{"id": scene_id, "location": "penthouse lobby", "shots": shots}],
        "cliffhanger": cliffhanger,
        "bgm": bgm,
        "burn_subtitles": burn_subtitles,
    }


def write_episode(
    project_root: Path,
    envelope: dict,
    *,
    name: str = "ep001",
    subdir: str = "episodes",
) -> Path:
    """Write ``episodes/<name>.py`` returning the given envelope verbatim
    (dicts everywhere — exercising the duck-typed resolution path)."""
    episode_dir = project_root / subdir if subdir else project_root
    episode_dir.mkdir(parents=True, exist_ok=True)
    path = episode_dir / f"{name}.py"
    path.write_text(
        "import series  # the project root is on sys.path (contract §1)\n\n"
        "def gen_episode():\n"
        f"    return {envelope!r}\n",
        encoding="utf-8",
    )
    return path


def make_clip(
    path: Path,
    *,
    duration_s: float,
    width: int = 270,
    height: int = 480,
    fps: int = 24,
    audio: bool = False,
) -> Path:
    """Render a real (non-dramapy) clip for planting into the render cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x336699:s={width}x{height}:r={fps}:d={duration_s:.3f}",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=f=300:r=48000:d={duration_s:.3f}"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "96k"]
    args += ["-t", f"{duration_s:.3f}", str(path)]
    media.run_ffmpeg(args)
    return path
