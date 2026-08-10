"""Re-exec into a modern interpreter when launched under an old python3.

The `claude` subprocess runs skill tools with whatever `python3` its PATH
resolves — on some machines that is 3.9 while dramapy requires >=3.10.
Call ensure_python() first thing in every __main__ shim: on an old
interpreter it searches (in order) $VIDEO_PYTHON, then python3.13..3.10 on
PATH, and os.execv's the same argv under the first one found. Raises a
clear error when none exists. No-op on >=3.10.
"""

from __future__ import annotations

import os
import shutil
import sys

MIN = (3, 10)
_SENTINEL = "VIDEO_PYVERSION_REEXEC"


def ensure_python() -> None:
    if sys.version_info >= MIN:
        return
    if os.environ.get(_SENTINEL):
        sys.stderr.write(
            f"dramacode: re-exec loop — {sys.executable} is still "
            f"{sys.version_info.major}.{sys.version_info.minor}\n"
        )
        raise SystemExit(2)

    candidates = []
    env_python = os.environ.get("VIDEO_PYTHON")
    if env_python:
        candidates.append(env_python)
    candidates += [f"python3.{minor}" for minor in range(13, MIN[1] - 1, -1)]

    for candidate in candidates:
        resolved = shutil.which(candidate) if os.sep not in candidate else candidate
        if resolved and os.access(resolved, os.X_OK):
            os.environ[_SENTINEL] = "1"
            os.execv(resolved, [resolved, *sys.argv])

    sys.stderr.write(
        f"dramacode: needs python >= {MIN[0]}.{MIN[1]} but running "
        f"{sys.version_info.major}.{sys.version_info.minor} and no newer "
        "python3.x found on PATH (set VIDEO_PYTHON to override)\n"
    )
    raise SystemExit(2)
