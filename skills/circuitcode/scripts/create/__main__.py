from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    skill_dir = scripts_dir.parent
    for candidate in (scripts_dir, skill_dir):
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    from common.pyversion import ensure_python
    ensure_python()
    from create.cli import main
else:
    from common.pyversion import ensure_python
    ensure_python()
    from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
