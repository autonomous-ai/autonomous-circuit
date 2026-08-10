from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from common.pyversion import ensure_python
    ensure_python()
    from review.cli import main
else:
    from common.pyversion import ensure_python  # scripts dir is importable in -m mode
    ensure_python()
    from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
