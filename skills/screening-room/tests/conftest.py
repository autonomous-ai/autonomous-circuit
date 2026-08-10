"""Test scaffolding for the screening-room skill.

Unlike dramacode (which stubs dramapy), these tests exercise the REAL
``dramapy.review_bundle`` — detecting a rotated clip is the whole point, and a
stub can't do that. We point both the in-process renderer and the subprocess
bundle CLI at the repo's dramapy source.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parents[1]  # skills/screening-room → skills → repo root
DRAMAPY_SRC = REPO_ROOT / "packages" / "dramapy" / "src"

if DRAMAPY_SRC.is_dir():
    if str(DRAMAPY_SRC) not in sys.path:
        sys.path.insert(0, str(DRAMAPY_SRC))
    # The bundle CLI (spawned as a subprocess) resolves dramapy from this too.
    os.environ.setdefault("SCREENING_TEST_DRAMAPY_PATH", str(DRAMAPY_SRC))
