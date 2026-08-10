from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    # Bare-directory launch: `python scripts/bundle …`. Put scripts/ on the
    # path so `bundle.cli` imports.
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from bundle.cli import main
else:
    from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())
