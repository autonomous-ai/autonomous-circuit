"""Import ``plane-and-classes.py``, whose name is not an identifier.

The brief names the file with hyphens, so it cannot be a normal module. This
loads it by path and caches it, so tests and the bench runner get the same
module object and there is exactly one place that knows the filename.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALGORITHM = HERE / "plane-and-classes.py"

_cached = None


def load():
    """The ``plane-and-classes`` module."""
    global _cached
    if _cached is not None:
        return _cached
    src = str(HERE.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    spec = importlib.util.spec_from_file_location("plane_and_classes", ALGORITHM)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise ImportError(f"cannot load {ALGORITHM}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["plane_and_classes"] = module
    spec.loader.exec_module(module)
    _cached = module
    return module


def router_class():
    return load().PlaneAndClassesRouter
