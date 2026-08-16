"""Where a composition gets the routers it composes.

A composition is a router that calls routers, so it needs a name → factory map.
It takes one as an **argument** rather than importing one, and that is the whole
reason a stage is swappable: the plane stage and the signal stage are two
strings in a plan, resolved against whatever map the caller hands over. A test
composes two stub routers with no algorithm files on disk; a suite run composes
the nine tournament families.

:func:`load_algorithms` is the convenience for the second case. It is a copy of
``scripts/judge.py``'s loader rather than an import of it, because ``scripts/``
is not importable from inside ``src/routerlib`` without putting a script
directory on ``sys.path``, and a library that reaches into a sibling script
directory is a library that breaks the moment it is installed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Callable, Mapping

#: A registry maps a family name to a zero-argument factory returning something
#: with ``.route(problem, budget) -> RoutingSolution``. The same type
#: ``routerlib.portfolio`` uses, deliberately, so one map drives both.
Registry = Mapping[str, Callable[[], object]]

#: ``packages/router``, when this package is being used out of the repo.
PACKAGE_ROOT = Path(__file__).resolve().parents[3]

#: Files in ``algorithms/`` that are runners or tests rather than families.
NOT_ALGORITHMS = frozenset({"bench.py", "bench_plane_and_classes.py"})


def load_algorithms(directory: str | Path | None = None) -> dict:
    """Every family in ``algorithms/``, by name, loaded by path.

    A file that fails to import is reported on stderr and skipped: a broken
    family is a missing candidate, and a composition that names it gets a
    ``missing`` stage rather than a traceback. Losing a stage to configuration
    and losing it to routing must not look alike.
    """
    root = Path(directory) if directory else PACKAGE_ROOT / "algorithms"
    out: dict = {}
    if root.is_dir():
        for path in sorted(root.glob("*.py")):
            if path.name in NOT_ALGORITHMS or path.name.startswith("test_"):
                continue
            try:
                out.update(_load_one(path))
            except Exception as exc:  # noqa: BLE001 - a broken family is data
                print(f"skipping {path.name}: {exc}", file=sys.stderr)
    from routerlib.baseline import PatternRouter

    out.setdefault(PatternRouter.name, PatternRouter)
    return out


def _load_one(path: Path) -> dict:
    spec = importlib.util.spec_from_file_location(
        f"_algo_{path.stem.replace('-', '_')}", path
    )
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return dict(getattr(module, "ROUTERS", {}))


__all__ = ["NOT_ALGORITHMS", "PACKAGE_ROOT", "Registry", "load_algorithms"]
