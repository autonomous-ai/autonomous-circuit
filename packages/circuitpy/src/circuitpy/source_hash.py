"""Source identity for boards — the donor ``source_hash`` algorithm (dramapy,
itself from cadpy) with the Python ``ast`` walk swapped for a TypeScript
import scanner.

``board_source_hash(source_path, project_root)`` hashes the entry TSX
(``source_hash``) and folds a whole-graph fingerprint over the entry plus
``product.json`` plus ``parts.json`` plus the optional content-hashed
``golden-blocks.lock.json`` plus every local import discovered statically
(breadth-first) — including ``blocks/``. Editing the bible, either lock, or an
imported block therefore invalidates every board (contract §1:
``source.fingerprint``).

The import scanner is a regex over static ESM forms::

    import ... from './x'      export ... from './x'
    import './x'               require('./x')

Only relative specifiers (``./`` / ``../``) are followed; bare specifiers
(``tscircuit``, ``@tsci/...``) are toolchain-owned and excluded by design.
**Dynamic imports are invisible to this scanner** (``import(expr)``, computed
``require`` arguments); the golden-block discipline keeps board imports
static, and a dynamic import would at worst make the fingerprint too loose
(a stale-cache risk, never a wrong-artifact risk — ``CIRCUIT_FORCE_REGEN=1``
is the manual override).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_SKIPPED_PATH_PARTS = {
    "__pycache__",
    ".cache",
    ".circuit",
    ".claude",
    ".git",
    ".hg",
    ".svn",
    ".tscircuit",
    "build",
    "dist",
    "inputs",
    "node_modules",
}

# Suffixes eligible for the manifest. Only TS-family files are scanned for
# further imports; .json members (product.json, parts.json, data imports)
# are hashed as leaves.
_TRACKABLE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".json"}
_SCANNABLE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js"}

_IMPORT_FROM_RE = re.compile(
    r"""^\s*(?:import|export)\b[^'"\n]*?\bfrom\s+['"]([^'"]+)['"]""", re.M
)
_SIDE_EFFECT_RE = re.compile(r"""^\s*import\s+['"]([^'"]+)['"]""", re.M)
_REQUIRE_RE = re.compile(r"""\brequire\(\s*['"]([^'"]+)['"]\s*\)""")

# Resolution candidates for a relative specifier, in order.
_RESOLUTION_SUFFIXES = ("", ".tsx", ".ts", ".jsx", ".js", ".json")
_INDEX_NAMES = ("index.tsx", "index.ts", "index.jsx", "index.js")


@dataclass(frozen=True)
class SourceFileHash:
    path: str
    hash: str


@dataclass(frozen=True)
class BoardSourceHash:
    digest: str
    files: tuple[SourceFileHash, ...]
    source_path: str
    source_hash: str

    @property
    def source_fingerprint(self) -> str:
        return self.digest


def board_source_hash(source_path: Path, project_root: Path) -> BoardSourceHash:
    """Hash a board source plus local TS imports (and the bible + parts lock)."""
    resolved_script = source_path.expanduser().resolve()
    resolved_root = project_root.expanduser().resolve()

    files: dict[Path, str] = {}
    # The bible and both reproducibility locks are seeded unconditionally when
    # present (contract §1). A project created before the golden-block snapshot
    # lock existed remains hashable, but evidence/publication gates reject it.
    queue: list[Path] = [
        resolved_script,
        resolved_root / "product.json",
        resolved_root / "parts.json",
        resolved_root / "golden-blocks.lock.json",
    ]
    seen: set[Path] = set()

    while queue:
        current = queue.pop(0).resolve()
        if current in seen or not _trackable_file(current, resolved_root):
            continue
        seen.add(current)
        files[current] = _sha256_file(current)
        if current.suffix in _SCANNABLE_SUFFIXES:
            for dependency in _ts_import_dependencies(current, resolved_root):
                if dependency not in seen:
                    queue.append(dependency)

    manifest_files = tuple(
        SourceFileHash(path=_manifest_path(path, resolved_root), hash=file_hash)
        for path, file_hash in sorted(
            files.items(), key=lambda item: _manifest_path(item[0], resolved_root)
        )
    )
    digest = hashlib.sha256()
    for file in manifest_files:
        digest.update(file.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.hash.encode("ascii"))
        digest.update(b"\0")
    return BoardSourceHash(
        digest=digest.hexdigest(),
        files=manifest_files,
        source_path=_manifest_path(resolved_script, resolved_root),
        source_hash=files.get(resolved_script, ""),
    )


def _trackable_file(path: Path, project_root: Path) -> bool:
    if path.suffix not in _TRACKABLE_SUFFIXES or not path.is_file():
        return False
    if any(part in _SKIPPED_PATH_PARTS for part in path.parts):
        return False
    # Built artifacts living next to sources are never part of the identity.
    if path.name.endswith(".circuit.json") or path.name.endswith(".board.json"):
        return False
    return _is_relative_to(path, project_root)


def _ts_import_dependencies(file_path: Path, project_root: Path) -> tuple[Path, ...]:
    try:
        text = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    specifiers: set[str] = set()
    for pattern in (_IMPORT_FROM_RE, _SIDE_EFFECT_RE, _REQUIRE_RE):
        specifiers.update(pattern.findall(text))
    dependencies: set[Path] = set()
    for specifier in specifiers:
        if not specifier.startswith(("./", "../")):
            continue  # bare specifiers are toolchain-owned, not project source
        resolved = _resolve_specifier(file_path.parent, specifier, project_root)
        if resolved is not None:
            dependencies.add(resolved)
    return tuple(sorted(dependencies))


def _resolve_specifier(
    base_dir: Path, specifier: str, project_root: Path
) -> Path | None:
    target = (base_dir / specifier).resolve()
    for suffix in _RESOLUTION_SUFFIXES:
        candidate = Path(str(target) + suffix)
        if _trackable_file(candidate, project_root):
            return candidate
    if target.is_dir():
        for index_name in _INDEX_NAMES:
            candidate = target / index_name
            if _trackable_file(candidate, project_root):
                return candidate
    return None


def _manifest_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
