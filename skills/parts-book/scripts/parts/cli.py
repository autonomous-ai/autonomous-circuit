"""Write one exact populated component ref per ``parts.json`` entry.

The project board selects frozen golden blocks through
``golden-blocks.lock.json`` and imports concrete block symbols.  This tool
validates that snapshot, resolves fixed ref defaults and literal overrides,
then emits the same shape consumed by circuitpy::

  {"U2": {"lcsc": "C500795", "basic": false,
           "description": "AP7361C-33E-13", "block": "ldo-3v3"}}

There is no family/list/range language in the output.  A parametric or dynamic
ref that cannot be proven from source is a refusal, not a partial lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

PARTS_FILE = "parts.json"
PRODUCT_FILE = "product.json"
BLOCK_LOCK_FILE = "golden-blocks.lock.json"

JLCSEARCH_URL = "https://jlcsearch.tscircuit.com/api/search"
LOOKUP_TIMEOUT_S = 90.0          # cold queries measured at 47-90s (r5 recon)
LOOKUP_RETRIES = 2
CACHE_MAX_AGE_DAYS = 7.0
INVENTORY_TIMEOUT_S = 300.0

# Test seam: a callable (lcsc: str) -> component dict. When set it replaces
# every network call (the tests never touch the network).
LOOKUP_FN = None

# Test seam: a callable(project, blocks_dir, selected, timeout_s) returning
# ``(circuit_json_elements, evidence)``.  Production always performs a fresh
# isolated compile with the pinned toolchain.  Keeping the seam at this exact
# boundary lets the offline unit suite exercise every fail-closed inventory
# rule without pretending a stale repository artifact is fresh evidence.
INVENTORY_FN = None

# JSX tags that carry a pinned part.
PART_TAGS = (
    "resistor", "capacitor", "led", "crystal", "pushbutton", "chip",
    "connector", "diode", "inductor", "switch", "transistor", "netlabel",
)
VALUE_ATTRS = ("resistance", "capacitance", "frequency", "inductance", "color")

_SUPPLIER_RE = re.compile(
    r"supplierPartNumbers\s*=\s*\{\{\s*jlcpcb\s*:\s*\[\s*\"(C\d+)\"", re.S
)
_ATTR_RE = re.compile(r"(\w+)\s*=\s*\"([^\"]*)\"")
_TAG_RE = re.compile(r"<([A-Za-z][\w.]*)")
_LCSC_RE = re.compile(r"\bC(\d+)\b")
_EXACT_LCSC_RE = re.compile(r"^C\d+$")
_EXACT_REF_RE = re.compile(r"^[A-Z][A-Z0-9]*$")
_BLOCK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROJECT_SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx"}
_PROJECT_IMPORT_SUFFIXES = (*sorted(_PROJECT_SOURCE_SUFFIXES), ".json")
_PROJECT_MIRROR_SUFFIXES = {*_PROJECT_SOURCE_SUFFIXES, ".json"}
_PROJECT_SKIP_DIRS = {
    ".cache", ".circuit", ".claude", ".git", ".tscircuit", "__pycache__", "dist",
    "blocks", "inputs", "node_modules",
}
_BLOCK_OWNER_PREFIX = "__parts_block__"
_BOARD_OWNER_PREFIX = "__parts_board__"


class PartsBookError(ValueError):
    """A project identity cannot be resolved without guessing."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------


def _err(message: str, code: str = "VALIDATION_FAILED") -> int:
    print(json.dumps({"ok": False, "error": {"code": code, "message": message}}))
    return 2


def _slug(text: str) -> str:
    """Lowercase id slug: keeps a-z 0-9 . _ -, collapses everything else."""
    out = re.sub(r"[^a-z0-9._-]+", "-", str(text).strip().lower())
    return re.sub(r"-{2,}", "-", out).strip("-.") or "part"


def _lcsc_url(lcsc: str) -> str:
    """The LCSC catalog page — where the datasheet for this exact number is.

    jlcsearch returns no datasheet field; this is the canonical page, not a
    direct PDF, and the SKILL.md says so.
    """
    return f"https://www.lcsc.com/product-detail/{lcsc}.html"


def _today() -> str:
    return date.today().isoformat()


def _load_json_no_duplicates(path: Path):
    def pairs_hook(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise PartsBookError(f"{path} contains duplicate JSON key {key!r}")
            out[key] = value
        return out

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs_hook)
    except FileNotFoundError:
        raise
    except PartsBookError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise PartsBookError(f"unreadable {path}: {exc}") from exc


# --------------------------------------------------------------------------
# reading the golden blocks (the offline source of candidate slots)
# --------------------------------------------------------------------------


def _element_span(text: str, at: int) -> str:
    """The JSX element text around position ``at``.

    Back to the nearest ``<tag``; forward to the first ``/>`` or nested ``<``
    (chips put their nested <footprint> after the attributes we want).
    """
    start = text.rfind("<", 0, at)
    if start < 0:
        start = max(0, at - 400)
    close = text.find("/>", at)
    nested = text.find("<", at)
    end_candidates = [c for c in (close, nested) if c >= 0]
    end = min(end_candidates) if end_candidates else min(len(text), at + 400)
    return text[start:end]


def scan_block_tsx(path: Path) -> dict[str, dict]:
    """LCSC number -> what the block's source says about that part."""
    text = path.read_text(encoding="utf-8")
    found: dict[str, dict] = {}
    for match in _SUPPLIER_RE.finditer(text):
        lcsc = match.group(1)
        span = _element_span(text, match.start())
        tag_match = _TAG_RE.search(span)
        tag = (tag_match.group(1) if tag_match else "").lower()
        attrs = dict(_ATTR_RE.findall(span))
        entry = found.setdefault(lcsc, {
            "lcsc": lcsc, "tags": [], "mfr": "", "footprint": "",
            "value": "", "refdes": [],
        })
        if tag and tag not in entry["tags"]:
            entry["tags"].append(tag)
        entry["mfr"] = entry["mfr"] or attrs.get("manufacturerPartNumber", "")
        entry["footprint"] = entry["footprint"] or attrs.get("footprint", "")
        if not entry["value"]:
            for key in VALUE_ATTRS:
                if attrs.get(key):
                    entry["value"] = attrs[key]
                    break
        name = attrs.get("name", "")
        if name and name.isidentifier() and name not in entry["refdes"]:
            # Literal name="R11"; name={u} is a prop default and is not
            # resolvable from source — BLOCK.md carries those refdes.
            entry["refdes"].append(name)
    return found


def _clean_markdown(text: str) -> str:
    return re.sub(r"[`*]", "", text).strip()


def _documented_basic(basic_cell: str, note: str) -> bool | None:
    value = _clean_markdown(basic_cell).lower()
    if value in {"yes", "true", "basic"}:
        return True
    if value in {"no", "false", "extended"}:
        return False
    words = f" {note.lower()} "
    has_basic = bool(re.search(r"\bbasic\b", words))
    has_extended = bool(re.search(r"\bextended\b", words))
    if re.search(r"\b(?:not|non)[ -]?basic\b", words):
        return False
    if has_basic and not has_extended:
        return True
    if has_extended and not has_basic:
        return False
    return None


def parse_block_md(path: Path) -> list[dict]:
    """Return the machine-usable rows from a block's pinned-parts table."""

    if not path.is_file():
        raise PartsBookError(f"selected block has no parts contract: {path}")
    rows: list[dict] = []
    in_parts = False
    header: dict[str, int] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_parts = "parts" in stripped[3:].lower()
            header = None
            continue
        if not in_parts or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if not cells or all(set(cell) <= set("-: ") for cell in cells):
            continue
        if header is None:
            normalized = [re.sub(r"[^a-z]", "", cell.lower()) for cell in cells]
            header = {name: index for index, name in enumerate(normalized)}
            continue
        ref_index = header.get("refdes", header.get("ref"))
        part_index = header.get("part", header.get("exactpart"))
        lcsc_index = header.get("lcsc")
        if ref_index is None or part_index is None or lcsc_index is None:
            raise PartsBookError(f"{path}: pinned-parts table needs Ref, Part, and LCSC")
        required_indices = [ref_index, part_index, lcsc_index]
        if max(required_indices) >= len(cells):
            raise PartsBookError(f"{path}: malformed short pinned-parts row {stripped!r}")
        lcsc_matches = _LCSC_RE.findall(cells[lcsc_index])
        if len(lcsc_matches) > 1:
            raise PartsBookError(
                f"{path}: one pinned-parts row cannot name multiple LCSC identities"
            )
        lcsc_match = _LCSC_RE.search(cells[lcsc_index])
        if lcsc_match is None:
            # DNP copper, testpoints, holes and alternate non-orderable rows do
            # not belong in an assembly parts lock.
            continue
        package_index = header.get("package")
        basic_index = header.get("basic")
        note_index = header.get("note")
        note = cells[note_index] if note_index is not None and note_index < len(cells) else ""
        basic_cell = (
            cells[basic_index]
            if basic_index is not None and basic_index < len(cells)
            else ""
        )
        description = _clean_markdown(cells[part_index])
        head = description.split(",", 1)[0].strip()
        rows.append(
            {
                "ref_cell": cells[ref_index],
                "lcsc": f"C{lcsc_match.group(1)}",
                "description": description,
                "mfr": (
                    head
                    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+\-]*", head)
                    else ""
                ),
                "package": (
                    _clean_markdown(cells[package_index])
                    if package_index is not None and package_index < len(cells)
                    else ""
                ),
                "basic": _documented_basic(basic_cell, note),
            }
        )
    if not rows:
        raise PartsBookError(f"{path}: no populated pinned-part rows found")
    return rows


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for relative, file_sha in sorted(files.items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_sha.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _snapshot_files(blocks_dir: Path, blocks: list[str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for entry in ["glue.tsx", *blocks]:
        target = blocks_dir / entry
        if target.is_symlink():
            raise PartsBookError(f"golden-block snapshot contains symlink {target}")
        candidates = [target] if target.is_file() else sorted(target.rglob("*")) if target.is_dir() else []
        if not candidates:
            raise PartsBookError(f"golden-block snapshot is missing {target}")
        for candidate in candidates:
            if candidate.is_symlink():
                raise PartsBookError(
                    f"golden-block snapshot contains symlink {candidate}"
                )
            if candidate.is_file():
                files[candidate.relative_to(blocks_dir).as_posix()] = _sha256(candidate)
    return dict(sorted(files.items()))


def read_selected_blocks(project: Path, blocks_dir: Path) -> list[str]:
    """Validate the frozen snapshot and return its selected block ids."""

    lock = project / BLOCK_LOCK_FILE
    if lock.is_symlink() or blocks_dir.is_symlink():
        raise PartsBookError("golden-block lock and snapshot root must not be symlinks")
    try:
        payload = _load_json_no_duplicates(lock)
    except FileNotFoundError as exc:
        raise PartsBookError(
            f"missing {lock}; parts ownership must come from a frozen golden lock"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        raise PartsBookError(f"{lock} must be a schemaVersion 1 object")
    if not isinstance(payload.get("source"), str) or not payload["source"].strip():
        raise PartsBookError(f"{lock} has no source identity")
    blocks = payload.get("blocks")
    if (
        not isinstance(blocks, list)
        or not blocks
        or not all(isinstance(block, str) for block in blocks)
        or blocks != sorted(set(blocks))
        or any(not _BLOCK_ID_RE.fullmatch(block) for block in blocks)
    ):
        raise PartsBookError(f"{lock} blocks must be non-empty, unique, and sorted")
    recorded_files = payload.get("files")
    if not isinstance(recorded_files, dict) or not recorded_files:
        raise PartsBookError(f"{lock} has no content-addressed files map")
    files: dict[str, str] = {}
    allowed = tuple(f"{block}/" for block in blocks)
    covered: set[str] = set()
    for relative, digest in recorded_files.items():
        if (
            not isinstance(relative, str)
            or not isinstance(digest, str)
            or relative.startswith("/")
            or ".." in Path(relative).parts
            or not _SHA256_RE.fullmatch(digest)
        ):
            raise PartsBookError(f"{lock} contains unsafe file identity {relative!r}")
        if relative == "glue.tsx":
            covered.add("glue.tsx")
        elif relative.startswith(allowed):
            covered.add(relative.split("/", 1)[0])
        else:
            raise PartsBookError(f"{lock} contains unselected file {relative!r}")
        files[relative] = digest
    if covered != {"glue.tsx", *blocks}:
        raise PartsBookError(f"{lock} does not cover every selected block")
    if payload.get("treeSha256") != _tree_sha(files):
        raise PartsBookError(f"{lock} treeSha256 does not match its files map")
    actual = _snapshot_files(blocks_dir, blocks)
    if actual != dict(sorted(files.items())):
        missing = sorted(set(files) - set(actual))
        extra = sorted(set(actual) - set(files))
        changed = sorted(
            relative
            for relative in set(files) & set(actual)
            if files[relative] != actual[relative]
        )
        details = [*(f"missing {item}" for item in missing)]
        details.extend(f"unexpected {item}" for item in extra)
        details.extend(f"changed {item}" for item in changed)
        raise PartsBookError(
            "golden-block snapshot does not match its lock: " + "; ".join(details)
        )
    return blocks


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(^|\s)//[^\n]*", r"\1", text)


def _project_source_graph(project: Path) -> list[Path]:
    """Return main board source and its project-local non-block imports."""

    entry = project / "boards" / "main.tsx"
    if not entry.is_file() or entry.is_symlink():
        raise PartsBookError(f"project needs one regular board entry at {entry}")
    pending = [entry.resolve()]
    seen: set[Path] = set()
    dependency_res = (
        re.compile(r"(?:from\s*|import\s*)[\"'](?P<path>\.\.?/[^\"']+)[\"']"),
        re.compile(r"require\s*\(\s*[\"'](?P<path>\.\.?/[^\"']+)[\"']\s*\)"),
        re.compile(r"import\s*\(\s*[\"'](?P<path>\.\.?/[^\"']+)[\"']\s*\)"),
    )
    unsafe_specifier_res = (
        re.compile(r"(?:from\s*|import\s*)[\"'](?P<path>(?:/|file:)[^\"']+)[\"']"),
        re.compile(r"(?:require|import)\s*\(\s*[\"'](?P<path>(?:/|file:)[^\"']+)[\"']\s*\)"),
    )
    while pending:
        source = pending.pop(0)
        if source in seen:
            continue
        try:
            relative = source.relative_to(project.resolve())
        except ValueError as exc:
            raise PartsBookError(f"board source escapes project root: {source}") from exc
        if source.is_symlink() or not source.is_file():
            raise PartsBookError(f"project source is missing or a symlink: {source}")
        seen.add(source)
        text = _strip_comments(source.read_text(encoding="utf-8"))
        if source.suffix == ".json":
            continue
        unsafe = [
            match.group("path")
            for pattern in unsafe_specifier_res
            for match in pattern.finditer(text)
        ]
        if unsafe:
            raise PartsBookError(
                f"project source {relative.as_posix()} uses absolute/file import "
                f"{unsafe[0]!r}; inventory inputs must be self-contained"
            )
        matches = [
            match
            for pattern in dependency_res
            for match in pattern.finditer(text)
        ]
        for match in sorted(matches, key=lambda found: found.start()):
            candidate = (source.parent / match.group("path")).resolve()
            try:
                imported_relative = candidate.relative_to(project.resolve())
            except ValueError as exc:
                raise PartsBookError(
                    f"project-local import {match.group('path')!r} escapes the project root"
                ) from exc
            if imported_relative.parts and imported_relative.parts[0] == "blocks":
                continue
            if imported_relative.as_posix() == PARTS_FILE:
                raise PartsBookError(
                    "board composition may not import parts.json while regenerating it"
                )
            choices = [
                candidate,
                *(candidate.with_suffix(suffix) for suffix in _PROJECT_IMPORT_SUFFIXES),
                *(candidate / f"index{suffix}" for suffix in _PROJECT_SOURCE_SUFFIXES),
            ]
            resolved = next((choice for choice in choices if choice.is_file()), None)
            if resolved is None:
                raise PartsBookError(
                    f"cannot resolve project-local import {match.group('path')!r} "
                    f"from {relative.as_posix()}"
                )
            pending.append(resolved.resolve())
    return sorted(seen)


def _project_mirror_files(project: Path) -> dict[str, Path]:
    """Match CircuitPy's build mirror source universe, minus ``parts.json``."""

    files: dict[str, Path] = {}
    for root, dirs, names in os.walk(project):
        root_path = Path(root)
        dirs[:] = [
            name
            for name in dirs
            if name not in _PROJECT_SKIP_DIRS
            and not name.endswith("_review")
            and not name.endswith("_fab")
        ]
        for name in names:
            path = root_path / name
            if path.suffix not in _PROJECT_MIRROR_SUFFIXES:
                continue
            if (
                name == PARTS_FILE
                or name.startswith(".parts-")
                or name.endswith(".circuit.json")
                or name.endswith(".board.json")
            ):
                continue
            if path.is_symlink() or not path.is_file():
                raise PartsBookError(f"inventory source must be one regular file: {path}")
            relative = path.relative_to(project).as_posix()
            files[relative] = path
    return dict(sorted(files.items()))


def _composition_files(
    project: Path, blocks_dir: Path, selected: list[str]
) -> dict[str, Path]:
    """The exact compiler inputs, deliberately excluding ``parts.json``.

    The lock being generated cannot be allowed to authenticate itself.  The
    final circuit build includes ``parts.json`` in its normal source hash and
    independently reconciles the resulting BOM; this preflight fingerprint
    instead binds the board composition that *produces* the populated set.
    """

    files: dict[str, Path] = {}
    for name in (PRODUCT_FILE, BLOCK_LOCK_FILE):
        path = project / name
        if path.is_symlink() or not path.is_file():
            raise PartsBookError(f"inventory input must be one regular file: {path}")
        files[name] = path
    # The production compiler discovers these at the project root. Copy/hash
    # them when present; an inventory that silently ignores a real config is
    # not the same composition. Package manifests are copied for dependency
    # boundary parity, never to install or execute scripts.
    for name in (
        "tsconfig.json",
        "tscircuit.config.json",
        "tscircuit.config.ts",
        "tscircuit.config.js",
        "tscircuit.config.mjs",
        "tscircuit.config.cjs",
        "package.json",
        "package-lock.json",
    ):
        path = project / name
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise PartsBookError(f"inventory config must be one regular file: {path}")
            files[name] = path
    # Parse the reachable graph for unsafe/escaping/self-authenticating imports,
    # then mirror CircuitPy's complete TS/JS/JSON source universe. This also
    # binds literal fs reads and configuration inputs that import regexes cannot
    # discover.
    _project_source_graph(project)
    files.update(_project_mirror_files(project))
    for relative in _snapshot_files(blocks_dir, selected):
        files[f"blocks/{relative}"] = blocks_dir / relative
    for relative, path in files.items():
        if path.suffix not in _PROJECT_SOURCE_SUFFIXES:
            continue
        raw = path.read_text(encoding="utf-8")
        forbidden = {
            "parts.json": "the output lock",
            "process.env": "ambient process environment",
            "import.meta.env": "ambient module environment",
            "Math.random": "nondeterministic randomness",
            "Date.now": "wall-clock time",
            "node:fs": "ambient filesystem access",
            "'fs'": "ambient filesystem access",
            '"fs"': "ambient filesystem access",
            "'fs/promises'": "ambient filesystem access",
            '"fs/promises"': "ambient filesystem access",
            "process.getBuiltinModule": "ambient builtin-module access",
        }
        for token, meaning in forbidden.items():
            if token in raw:
                raise PartsBookError(
                    f"inventory source {relative} references {meaning} via {token!r}; "
                    "populated composition must be deterministic and self-contained"
                )
        if re.search(r"(?:require|import)\s*\(\s*(?![\"'])", raw):
            raise PartsBookError(
                f"inventory source {relative} uses a nonliteral dynamic import/require"
            )
    return dict(sorted(files.items()))


def _composition_fingerprint(
    project: Path, blocks_dir: Path, selected: list[str]
) -> tuple[str, dict[str, Path]]:
    files = _composition_files(project, blocks_dir, selected)
    digest = hashlib.sha256()
    for relative, path in files.items():
        if path.is_symlink() or not path.is_file():
            raise PartsBookError(f"inventory input changed or became unsafe: {path}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), files


def _staged_composition_fingerprint(stage: Path, relatives: Sequence[str]) -> str:
    """Hash the isolated compiler inputs with the production relative names."""

    digest = hashlib.sha256()
    for relative in sorted(relatives):
        path = stage / relative
        if path.is_symlink() or not path.is_file():
            raise PartsBookError(f"staged inventory input is missing or unsafe: {path}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _resolve_toolchain(project: Path) -> Path:
    override = os.environ.get("CIRCUIT_TOOLCHAIN", "").strip()
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    for origin in (project.resolve(), Path(__file__).resolve()):
        candidates.extend(parent / "toolchain" for parent in (origin, *origin.parents))
    seen: set[Path] = set()
    for raw in candidates:
        candidate = raw.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "package.json").is_file():
            return candidate
    raise PartsBookError(
        "pinned tscircuit toolchain not found; set CIRCUIT_TOOLCHAIN to the "
        "installed toolchain directory (never use npx tsci)"
    )


def _toolchain_identity(toolchain: Path, node: Path) -> dict:
    """Merkle-bind every installed byte the offline Node compiler can load."""

    roots = [toolchain / "package.json", toolchain / "package-lock.json"]
    node_modules = toolchain / "node_modules"
    if not node_modules.is_dir():
        raise PartsBookError(
            f"pinned toolchain is incomplete: missing {node_modules}; run setup-toolchain"
        )
    entries: list[Path] = []
    for path in roots:
        if path.is_symlink() or not path.is_file():
            raise PartsBookError(f"pinned toolchain input is missing/unsafe: {path}")
        entries.append(path)
    for root, dirs, files in os.walk(node_modules, followlinks=False):
        root_path = Path(root)
        for name in list(dirs):
            candidate = root_path / name
            if candidate.is_symlink():
                entries.append(candidate)
                dirs.remove(name)
        entries.extend(root_path / name for name in files)

    digest = hashlib.sha256()
    byte_count = 0
    file_count = 0
    resolved_toolchain = toolchain.resolve()
    for path in sorted(entries, key=lambda item: item.relative_to(toolchain).as_posix()):
        relative = path.relative_to(toolchain).as_posix()
        metadata = path.lstat()
        executable = stat.S_IMODE(metadata.st_mode) & 0o111
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            resolved = path.resolve(strict=True)
            try:
                resolved.relative_to(resolved_toolchain)
            except ValueError as exc:
                raise PartsBookError(
                    f"pinned toolchain symlink escapes its tree: {relative} -> {target}"
                ) from exc
            payload = target.encode("utf-8")
            kind = b"L"
        elif stat.S_ISREG(metadata.st_mode):
            payload = path.read_bytes()
            kind = b"F"
            byte_count += len(payload)
        else:
            raise PartsBookError(f"pinned toolchain contains special file {relative}")
        file_count += 1
        digest.update(relative.encode("utf-8") + b"\0" + kind + b"\0")
        digest.update(str(executable).encode("ascii") + b"\0")
        digest.update(str(len(payload)).encode("ascii") + b"\0")
        digest.update(hashlib.sha256(payload).hexdigest().encode("ascii") + b"\n")

    resolved_node = node.resolve(strict=True)
    if not resolved_node.is_file():
        raise PartsBookError(f"resolved Node executable is not a regular file: {resolved_node}")
    node_env = {
        "PATH": str(resolved_node.parent) + os.pathsep + "/usr/bin:/bin",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    version_proc = subprocess.run(
        [str(resolved_node), "--version"],
        capture_output=True,
        text=True,
        env=node_env,
        timeout=15,
    )
    version = (version_proc.stdout or "").strip()
    if version_proc.returncode != 0 or not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise PartsBookError("could not bind the resolved Node executable/version")
    return {
        "schemaVersion": 1,
        "treeSha256": digest.hexdigest(),
        "fileCount": file_count,
        "byteCount": byte_count,
        "node": {
            "version": version,
            "sha256": _sha256(resolved_node),
        },
    }


def _copy_inventory_inputs(stage: Path, files: dict[str, Path]) -> None:
    for relative, source in files.items():
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    # The CLI walks upward to choose an output root.  An isolated anchor keeps
    # its newly-created artifact inside this invocation's empty temp tree.
    anchor = stage / "package.json"
    if not anchor.exists():
        anchor.write_text(
            '{"name":"parts-inventory","private":true,"version":"0.0.0"}\n',
            encoding="utf-8",
        )


def _compile_inventory(
    project: Path,
    blocks_dir: Path,
    selected: list[str],
    timeout_s: float,
) -> tuple[list[dict], dict]:
    """Compile an offline placement inventory in a fresh private tree.

    This intentionally invokes Node + the pinned CLI main module directly.
    The public ``tscircuit-cli`` shim shells out to ``tsx`` and has historically
    exited zero without producing anything when ``tsx`` was absent; direct
    invocation removes that false-success path.  The artifact is still the
    gate, never the process status.
    """

    if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
        raise PartsBookError("inventory timeout must be a positive number")
    before, files = _composition_fingerprint(project, blocks_dir, selected)
    toolchain = _resolve_toolchain(project)
    node_name = shutil.which("node")
    if not node_name:
        raise PartsBookError("node is missing from PATH; the pinned compiler cannot run")
    node = Path(node_name).resolve(strict=True)
    toolchain_identity = _toolchain_identity(toolchain, node)
    loader = toolchain / "node_modules/tsx/dist/loader.mjs"
    cli_main = toolchain / "node_modules/@tscircuit/cli/dist/cli/main.js"
    bin_dir = toolchain / "node_modules/.bin"
    base_env = {
        "PATH": str(bin_dir) + os.pathsep + str(node.parent) + os.pathsep + "/usr/bin:/bin",
        "NODE_PATH": str(toolchain / "node_modules"),
        "CIRCUIT_PARTS_ENGINE": "off",
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
    }
    command = [
        node,
        "--import",
        str(loader),
        str(cli_main),
        "build",
        "boards/main.tsx",
        "--routing-disabled",
        "--disable-parts-engine",
        "--ignore-errors",
        "--concurrency",
        "1",
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="parts-book-inventory-") as temp:
            stage = Path(temp)
            _copy_inventory_inputs(stage, files)
            private_home = stage / ".home"
            private_tmp = stage / ".tmp"
            private_home.mkdir()
            private_tmp.mkdir()
            env = {
                **base_env,
                "HOME": str(private_home),
                "TMPDIR": str(private_tmp),
            }
            if _staged_composition_fingerprint(stage, files) != before:
                raise PartsBookError(
                    "project composition changed while staging the fresh parts inventory"
                )
            started_ns = time.time_ns()
            proc = subprocess.Popen(
                command,
                cwd=stage,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                stdout, stderr = proc.communicate(timeout=float(timeout_s))
            except subprocess.TimeoutExpired as exc:
                os.killpg(proc.pid, signal.SIGKILL)
                stdout, stderr = proc.communicate()
                raise PartsBookError(
                    f"fresh parts inventory compile exceeded {timeout_s:g}s; "
                    "its process group was terminated"
                ) from exc
            output = (stdout or "") + (stderr or "")
            artifact = stage / "dist/boards/main/circuit.json"
            if artifact.is_symlink() or not artifact.is_file():
                raise PartsBookError(
                    "pinned compiler produced no fresh dist/boards/main/circuit.json "
                    f"(exit {proc.returncode}; tail: {output.strip()[-600:] or 'none'})"
                )
            if artifact.stat().st_mtime_ns + 1_000_000_000 < started_ns:
                raise PartsBookError("parts inventory artifact predates this invocation")
            try:
                artifact_bytes = artifact.read_bytes()
                elements = json.loads(artifact_bytes)
            except (OSError, ValueError) as exc:
                raise PartsBookError(f"fresh parts inventory is unreadable: {exc}") from exc
            if not isinstance(elements, list) or not all(
                isinstance(element, dict) for element in elements
            ):
                raise PartsBookError("fresh parts inventory must be a circuit element array")
            errors = sorted(
                str(element.get("type"))
                for element in elements
                if str(element.get("type") or "").endswith("_error")
            )
            if errors:
                summary = ", ".join(
                    f"{kind} x{errors.count(kind)}" for kind in sorted(set(errors))
                )
                raise PartsBookError(
                    "fresh routing-disabled inventory contains serialized errors: " + summary
                )
            if re.search(r"(?:Async effect error|Fatal error|Unhandled rejection)", output):
                raise PartsBookError(
                    "compiler logged an unrepresented asynchronous/fatal failure: "
                    + (output.strip()[-600:] or "unknown failure")
                )
            if proc.returncode != 0:
                raise PartsBookError(
                    "pinned compiler returned nonzero despite emitting an artifact "
                    f"(exit {proc.returncode}; tail: {output.strip()[-600:] or 'none'})"
                )
            artifact_sha = hashlib.sha256(artifact_bytes).hexdigest()
    except OSError as exc:
        raise PartsBookError(f"could not run the pinned parts inventory: {exc}") from exc
    after, _ = _composition_fingerprint(project, blocks_dir, selected)
    if after != before:
        raise PartsBookError(
            "project composition changed during parts inventory; discard and rerun"
        )
    if _toolchain_identity(toolchain, node) != toolchain_identity:
        raise PartsBookError(
            "pinned toolchain or Node executable changed during parts inventory"
        )
    return elements, {
        "compositionSha256": before,
        "circuitSha256": artifact_sha,
        "toolchain": toolchain_identity,
        "routingDisabled": True,
        "partsEngine": "off",
        "returnCode": 0,
        "command": command[4:],
    }


def _imported_instances(project: Path, selected: list[str]) -> list[dict]:
    """Resolve concrete JSX block instances from project-owned source."""

    sources = _project_source_graph(project)
    selected_set = set(selected)
    instances: list[dict] = []
    aliases: dict[str, str] = {}
    texts: dict[Path, str] = {}
    import_re = re.compile(
        r"import\s+(?P<clause>\{.*?\}|[A-Za-z_$][\w$]*)\s+from\s+"
        r"[\"'](?P<path>[^\"']+)[\"']",
        re.S,
    )
    block_path_re = re.compile(
        r"(?:^|/)blocks/(?P<block>[a-z0-9][a-z0-9-]*)/[^/]+$"
    )
    for source in sources:
        text = _strip_comments(source.read_text(encoding="utf-8"))
        texts[source] = text
        for match in import_re.finditer(text):
            path_match = block_path_re.search(match.group("path"))
            if path_match is None:
                continue
            block = path_match.group("block")
            if block not in selected_set:
                raise PartsBookError(
                    f"{source}: imports unselected golden block {block!r}"
                )
            clause = match.group("clause").strip()
            names: list[str] = []
            braced = re.search(r"\{(.*?)\}", clause, re.S)
            if braced:
                for item in braced.group(1).split(","):
                    item = re.sub(r"^\s*type\s+", "", item.strip())
                    if not item:
                        continue
                    pieces = re.split(r"\s+as\s+", item)
                    names.append(pieces[-1].strip())
            default = clause.split(",", 1)[0].strip()
            if default and not default.startswith("{") and re.fullmatch(r"[A-Za-z_$][\w$]*", default):
                names.append(default)
            for name in names:
                previous = aliases.get(name)
                if previous is not None and previous != block:
                    raise PartsBookError(
                        f"project import alias {name!r} ambiguously names {previous!r} and {block!r}"
                    )
                aliases[name] = block
    for source, text in texts.items():
        for symbol, block in sorted(aliases.items()):
            tag_re = re.compile(rf"<(?!/){re.escape(symbol)}\b(?P<attrs>.*?)(?:/?>)", re.S)
            for match in tag_re.finditer(text):
                instances.append(
                    {
                        "block": block,
                        "symbol": symbol,
                        "attrs": match.group("attrs"),
                        "source": source,
                    }
                )
    if not instances:
        raise PartsBookError(
            "no selected golden-block JSX instance found; dynamic/re-exported "
            "composition needs an explicit count-aware resolver"
        )
    return instances


def _default_ref_props(path: Path) -> dict[str, str]:
    text = _strip_comments(path.read_text(encoding="utf-8"))
    mapping: dict[str, str] = {}
    for variable, prop, ref in re.findall(
        r"const\s+(\w+)\s*=\s*props\.(\w+)\s*\?\?\s*[\"']([A-Z][A-Z0-9]*)[\"']",
        text,
    ):
        del variable
        previous = mapping.get(ref)
        if previous is not None and previous != prop:
            raise PartsBookError(
                f"{path}: default ref {ref} is controlled by both {previous} and {prop}"
            )
        mapping[ref] = prop
    return mapping


def _direct_block_imports(project: Path, selected: list[str]) -> set[str]:
    selected_set = set(selected)
    found: set[str] = set()
    pattern = re.compile(r"(?:^|/)blocks/([a-z0-9][a-z0-9-]*)/")
    for source in _project_source_graph(project):
        if source.suffix == ".json":
            continue
        text = _strip_comments(source.read_text(encoding="utf-8"))
        for block in pattern.findall(text):
            if block not in selected_set:
                raise PartsBookError(
                    f"{source}: imports unselected golden block {block!r}"
                )
            found.add(block)
    return found


def _documented_ref_rules(raw: str, overridable: set[str]) -> list[dict]:
    """Turn BLOCK.md notation into ownership rules, never population.

    The compiler owns the actual ref set.  These rules merely reconcile each
    compiled identity with the selected frozen block contract.  Exact/range
    rules outrank parametric notation, which outranks a block's explicit
    ref-prop override surface (e.g. ``SwTact name={sw}``).
    """

    rules: list[dict] = []
    cell = re.sub(r"[*]", "", raw).strip()
    for raw_token in re.split(r"\s*[,/]\s*", cell):
        token = raw_token.strip()
        exact_head = re.fullmatch(r"([A-Z]+\d+)", _clean_markdown(token))
        if exact_head:
            ref = exact_head.group(1)
            rules.append({"kind": "exact", "value": ref, "specificity": 3})
            if ref in overridable:
                prefix = re.match(r"^[A-Z]+", ref).group(0)  # type: ignore[union-attr]
                rules.append(
                    {"kind": "regex", "value": rf"{prefix}\d+", "specificity": 1}
                )
            continue
        ranged = re.fullmatch(
            r"([A-Z]+)(\d+)\s*[\-–—]\s*([A-Z]+)?(\d+)",
            _clean_markdown(token),
        )
        if ranged:
            left, first, right, last = ranged.groups()
            right = right or left
            if left != right or int(last) < int(first) or int(last) - int(first) > 1000:
                raise PartsBookError(f"unsafe documented ref range {raw!r}")
            rules.extend(
                {"kind": "exact", "value": f"{left}{number}", "specificity": 3}
                for number in range(int(first), int(last) + 1)
            )
            continue
        compact = _clean_markdown(token).replace(" ", "")
        parametric = re.fullmatch(r"([A-Z]+)(\d*)n", compact)
        if parametric:
            prefix, fixed_digits = parametric.groups()
            rules.append(
                {
                    "kind": "regex",
                    "value": rf"{prefix}{fixed_digits}\d+",
                    "specificity": 2,
                }
            )
            continue
        plus = re.fullmatch(r"([A-Z]+)(\d+)\+", compact)
        if plus:
            prefix, first = plus.groups()
            rules.append(
                {
                    "kind": "at_least",
                    "value": (prefix, int(first)),
                    "specificity": 2,
                }
            )
            continue
    if not rules:
        raise PartsBookError(
            f"frozen populated ref notation {raw!r} is not a supported exact, "
            "finite-range, parametric-n, or start+ ownership contract"
        )
    return rules


def _rule_matches(rule: dict, ref: str) -> bool:
    kind = rule["kind"]
    if kind == "exact":
        return ref == rule["value"]
    if kind == "regex":
        return re.fullmatch(str(rule["value"]), ref) is not None
    if kind == "at_least":
        prefix, first = rule["value"]
        match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", ref)
        return match is not None and int(match.group(1)) >= first
    return False


def _block_metadata_rows(
    project: Path, blocks_dir: Path, selected: list[str]
) -> list[dict]:
    active = _direct_block_imports(project, selected)
    rows: list[dict] = []
    for block in sorted(active):
        block_source = blocks_dir / block / f"{block}.tsx"
        if not block_source.is_file():
            raise PartsBookError(f"selected block source missing: {block_source}")
        pinned: set[str] = set()
        for source in sorted((blocks_dir / block).rglob("*.tsx")):
            pinned.update(scan_block_tsx(source))
        ref_props = _default_ref_props(block_source)
        for row in parse_block_md(blocks_dir / block / "BLOCK.md"):
            if row["lcsc"] not in pinned:
                raise PartsBookError(
                    f"block {block!r} documents {row['lcsc']} but its selected "
                    "frozen source does not pin it"
                )
            copied = dict(row)
            copied["block"] = block
            copied["rules"] = _documented_ref_rules(
                row["ref_cell"], set(ref_props)
            )
            rows.append(copied)
    return rows


def _require_block_owner_markers(
    project: Path, blocks_dir: Path, selected: list[str]
) -> tuple[set[str], set[str]]:
    project_sources = [
        path
        for path in _project_mirror_files(project).values()
        if path.suffix in _PROJECT_SOURCE_SUFFIXES
    ]
    allowed_board_markers: set[str] = set()
    for source in project_sources:
        if source.suffix not in _PROJECT_SOURCE_SUFFIXES:
            continue
        text = source.read_text(encoding="utf-8")
        if _BLOCK_OWNER_PREFIX in text:
            raise PartsBookError(
                f"project-owned source {source} uses reserved compiled block-owner "
                f"marker {_BLOCK_OWNER_PREFIX!r}; only frozen block roots may own it"
            )
        board_marker = re.compile(
            rf"<group\b(?:(?!>).)*\bname\s*=\s*[\"']"
            rf"{re.escape(_BOARD_OWNER_PREFIX)}[a-z0-9][a-z0-9-]*[\"']"
            rf"(?:(?!>).)*>",
            re.S,
        )
        raw_count = text.count(_BOARD_OWNER_PREFIX)
        structured = board_marker.findall(_strip_comments(text))
        structured_count = len(structured)
        if raw_count != structured_count:
            raise PartsBookError(
                f"project-owned source {source} has an unstructured/spoofed "
                f"{_BOARD_OWNER_PREFIX!r} marker"
            )
        for match in board_marker.finditer(_strip_comments(text)):
            name_match = re.search(
                rf"{re.escape(_BOARD_OWNER_PREFIX)}[a-z0-9][a-z0-9-]*",
                match.group(0),
            )
            if name_match is None or name_match.group(0) in allowed_board_markers:
                raise PartsBookError(
                    f"project parts-owner marker is duplicate or malformed in {source}"
                )
            allowed_board_markers.add(name_match.group(0))
    instances = _imported_instances(project, selected)
    expected_compiled_roots: set[str] = set()
    for block in sorted(_direct_block_imports(project, selected)):
        source = blocks_dir / block / f"{block}.tsx"
        raw_sources = {
            path: path.read_text(encoding="utf-8")
            for path in sorted((blocks_dir / block).rglob("*"))
            if path.is_file() and path.suffix in _PROJECT_SOURCE_SUFFIXES
        }
        reserved = [
            (path, prefix)
            for path, raw in raw_sources.items()
            for prefix in (_BLOCK_OWNER_PREFIX, _BOARD_OWNER_PREFIX)
            for _ in range(raw.count(prefix))
        ]
        expected = [
            item
            for item in reserved
            if item == (source, _BLOCK_OWNER_PREFIX)
        ]
        if len(reserved) != 1 or len(expected) != 1:
            details = ", ".join(
                f"{path.relative_to(blocks_dir).as_posix()}:{prefix}"
                for path, prefix in reserved
            ) or "none"
            raise PartsBookError(
                f"selected block {block!r} must reserve exactly its own one root "
                f"parts marker (found {details})"
            )
        text = _strip_comments(raw_sources[source])
        marker = re.compile(
            rf"<group\b(?P<attrs>(?:(?!>).)*)\bname\s*=\s*\{{\s*`"
            rf"{re.escape(_BLOCK_OWNER_PREFIX + block + '__')}"
            rf"\$\{{[A-Za-z_$][\w$]*\}}`\s*\}}(?:(?!>).)*>", re.S
        )
        matches = list(marker.finditer(text))
        if len(matches) != 1:
            raise PartsBookError(
                f"selected block {block!r} needs exactly one structured root-group "
                f"marker {_BLOCK_OWNER_PREFIX}{block}__${{instance}} (found {len(matches)})"
            )
        marker_start = matches[0].start()
        return_at = text.rfind("return", 0, marker_start)
        arrow_at = text.rfind("=>", 0, marker_start)
        boundary = max(return_at, arrow_at)
        intervening = text[boundary + 2 : marker_start] if boundary >= 0 else text[:marker_start]
        if boundary < 0 or re.search(r"<\s*(?:group|chip|resistor|capacitor|led|connector|pushbutton)\b", intervening):
            raise PartsBookError(
                f"selected block {block!r} parts-owner marker is not its returned root group"
            )
        declarations = re.findall(
            r"export\s+(?:default\s+)?const\s+([A-Za-z_$][\w$]*)\s*=",
            text[:marker_start],
        )
        if not declarations:
            raise PartsBookError(
                f"selected block {block!r} marker is not inside an exported root component"
            )
        root_symbol = declarations[-1]
        if any(
            instance["block"] == block and instance["symbol"] == root_symbol
            for instance in instances
        ):
            expected_compiled_roots.add(block)
    return expected_compiled_roots, allowed_board_markers


def _compiled_description(element: dict) -> str:
    mfr = str(element.get("manufacturer_part_number") or "").strip()
    if mfr:
        return mfr
    for key in (
        "display_resistance",
        "display_capacitance",
        "display_inductance",
        "display_frequency",
    ):
        value = str(element.get(key) or "").strip()
        if value:
            return value
    return ""


def _metadata_for_compiled_ref(
    ref: str,
    lcsc: str,
    element: dict,
    rows: list[dict],
    *,
    owner: str,
) -> dict:
    if owner == "board":
        return {
            "basic": None,
            "description": _compiled_description(element),
            "block": "board",
            "mfr": str(element.get("manufacturer_part_number") or ""),
            "package": "",
            "source": "compiled-board",
        }
    matched: list[tuple[int, dict]] = []
    for row in rows:
        if row["block"] != owner:
            continue
        for rule in row["rules"]:
            if _rule_matches(rule, ref):
                matched.append((int(rule["specificity"]), row))
    if not matched:
        raise PartsBookError(
            f"compiled {ref}/{lcsc} belongs to frozen block {owner!r} but no "
            "BLOCK.md ref rule covers it"
        )
    specificity = max(score for score, _row in matched)
    strongest = [row for score, row in matched if score == specificity]
    same_identity = [row for row in strongest if row["lcsc"] == lcsc]
    if not same_identity:
        expected = ", ".join(
            sorted({f"{row['block']}:{row['lcsc']}" for row in strongest})
        )
        raise PartsBookError(
            f"compiled {ref} pins {lcsc} but its strongest frozen block "
            f"contract pins {expected}"
        )
    owners = {row["block"] for row in same_identity}
    if len(owners) != 1:
        raise PartsBookError(
            f"compiled {ref}/{lcsc} has ambiguous frozen block ownership: "
            + ", ".join(sorted(owners))
        )
    normalized = {
        (row["description"], row["basic"], row["mfr"], row["package"])
        for row in same_identity
    }
    if len(normalized) != 1:
        raise PartsBookError(
            f"compiled {ref}/{lcsc} matches conflicting frozen metadata"
        )
    row = same_identity[0]
    return {
        "basic": row["basic"],
        "description": row["description"],
        "block": row["block"],
        "mfr": row["mfr"],
        "package": row["package"],
        "source": "compiled-block",
    }


def _compiled_block_owners(
    elements: list[dict], selected: set[str], expected_roots: set[str],
    allowed_board_markers: set[str],
) -> tuple[dict[str, str], set[str]]:
    groups: dict[str, dict] = {}
    for element in elements:
        if element.get("type") != "source_group":
            continue
        group_id = str(element.get("source_group_id") or "")
        if not group_id or group_id in groups:
            raise PartsBookError(
                f"compiled inventory has duplicate/empty source_group_id {group_id!r}"
            )
        groups[group_id] = element
    owners: dict[str, str] = {}
    marker_owners: dict[str, str] = {}
    marker_names: set[str] = set()
    for group_id, group in groups.items():
        name = str(group.get("name") or "")
        if not (
            name.startswith(_BLOCK_OWNER_PREFIX)
            or name.startswith(_BOARD_OWNER_PREFIX)
        ):
            continue
        if group.get("was_automatically_named") is not False:
            raise PartsBookError(
                f"compiled parts-owner marker must be explicitly named: {name!r}"
            )
        block_match = re.fullmatch(
            rf"{re.escape(_BLOCK_OWNER_PREFIX)}"
            rf"(?P<block>[a-z0-9][a-z0-9-]*)__"
            rf"(?P<instance>[A-Z][A-Z0-9]*)",
            name,
        )
        board_match = re.fullmatch(
            rf"{re.escape(_BOARD_OWNER_PREFIX)}[a-z0-9][a-z0-9-]*",
            name,
        )
        if block_match is None and board_match is None:
            raise PartsBookError(f"compiled parts-owner marker is unsafe: {name!r}")
        block = block_match.group("block") if block_match else "board"
        if board_match is not None and name not in allowed_board_markers:
            raise PartsBookError(
                f"compiled board parts-owner marker was not declared by project source: {name!r}"
            )
        if block != "board" and block not in selected:
            raise PartsBookError(
                f"compiled owner marker {name!r} claims unselected block {block!r}"
            )
        if name in marker_names:
            raise PartsBookError(f"compiled block-owner marker is duplicated: {name!r}")
        marker_names.add(name)
        marker_owners[group_id] = block
    for group_id in groups:
        cursor = group_id
        seen: set[str] = set()
        found: list[str] = []
        while cursor:
            if cursor in seen:
                raise PartsBookError(f"compiled source-group ancestry cycles at {cursor}")
            seen.add(cursor)
            group = groups.get(cursor)
            if group is None:
                raise PartsBookError(
                    f"compiled source-group ancestry references missing {cursor}"
                )
            block = marker_owners.get(cursor)
            if block is not None:
                found.append(block)
            cursor = str(group.get("parent_source_group_id") or "")
        block_found = [owner for owner in found if owner != "board"]
        board_count = found.count("board")
        if len(block_found) > 1 or (not block_found and board_count > 1):
            raise PartsBookError(
                f"compiled source group {group_id} is nested under multiple parts owners: "
                + ", ".join(found)
            )
        if block_found:
            owners[group_id] = block_found[0]
        elif board_count == 1:
            owners[group_id] = "board"
    compiled_block_roots = set(marker_owners.values()) - {"board"}
    missing_roots = sorted(expected_roots - compiled_block_roots)
    if missing_roots:
        raise PartsBookError(
            "fresh compiler emitted no owner marker for active frozen root block(s): "
            + ", ".join(missing_roots)
        )
    compiled_board_markers = {
        name for name in marker_names if name.startswith(_BOARD_OWNER_PREFIX)
    }
    missing_board_markers = sorted(allowed_board_markers - compiled_board_markers)
    if missing_board_markers:
        raise PartsBookError(
            "fresh compiler emitted no board parts-owner marker(s): "
            + ", ".join(missing_board_markers)
        )
    return owners, set(groups)


def _records_from_inventory(
    elements: list[dict], rows: list[dict], selected: list[str], expected_roots: set[str],
    allowed_board_markers: set[str],
) -> list[dict]:
    selected_set = set(selected)
    owners, group_ids = _compiled_block_owners(
        elements, selected_set, expected_roots, allowed_board_markers
    )
    source_elements: dict[str, dict] = {}
    refs: set[str] = set()
    for element in elements:
        if element.get("type") != "source_component":
            continue
        source_id = str(element.get("source_component_id") or "")
        ref = str(element.get("name") or "")
        group_id = str(element.get("source_group_id") or "")
        if not source_id or source_id in source_elements or not _EXACT_REF_RE.fullmatch(ref):
            raise PartsBookError(
                f"compiled populated component has duplicate/unsafe ref/source "
                f"identity {ref!r}/{source_id!r}"
            )
        if ref in refs:
            raise PartsBookError(f"compiled inventory contains duplicate component ref {ref}")
        if not group_id or group_id not in group_ids:
            raise PartsBookError(
                f"compiled {ref}/{source_id} has missing or unknown source_group_id "
                f"{group_id!r}"
            )
        refs.add(ref)
        source_elements[source_id] = element

    synthetic_sources: set[str] = set()
    for element in elements:
        if element.get("type") != "source_manually_placed_via":
            continue
        synthetic_id = str(element.get("source_manually_placed_via_id") or "")
        if not synthetic_id or synthetic_id in synthetic_sources or synthetic_id in source_elements:
            raise PartsBookError(
                f"compiled inventory has duplicate/empty synthetic via identity {synthetic_id!r}"
            )
        synthetic_sources.add(synthetic_id)

    pcb_by_source: dict[str, list[dict]] = {}
    copper_by_component: set[str] = set()
    pcb_ids: set[str] = set()
    copper_ids: set[str] = set()
    for element in elements:
        if element.get("type") == "pcb_component":
            pcb_id = str(element.get("pcb_component_id") or "")
            if not pcb_id or pcb_id in pcb_ids:
                raise PartsBookError(
                    f"compiled inventory has duplicate/empty pcb_component_id {pcb_id!r}"
                )
            pcb_ids.add(pcb_id)
            source_id = str(element.get("source_component_id") or "")
            if not source_id:
                raise PartsBookError(
                    f"compiled PCB component {pcb_id} has no source-component identity"
                )
            if source_id in source_elements:
                pcb_by_source.setdefault(source_id, []).append(element)
            elif source_id not in synthetic_sources:
                raise PartsBookError(
                    f"compiled PCB component {pcb_id} references unknown source owner "
                    f"{source_id!r}"
                )
        if element.get("type") in {"pcb_smtpad", "pcb_plated_hole"}:
            id_key = (
                "pcb_smtpad_id"
                if element.get("type") == "pcb_smtpad"
                else "pcb_plated_hole_id"
            )
            copper_id = str(element.get(id_key) or "")
            if not copper_id or copper_id in copper_ids:
                raise PartsBookError(
                    f"compiled inventory has duplicate/empty physical land ID {copper_id!r}"
                )
            copper_ids.add(copper_id)
            component_id = str(element.get("pcb_component_id") or "")
            if component_id:
                copper_by_component.add(component_id)
    unknown_land_owners = sorted(copper_by_component - pcb_ids)
    if unknown_land_owners:
        raise PartsBookError(
            "compiled physical lands reference unknown PCB components: "
            + ", ".join(unknown_land_owners[:10])
        )
    records: dict[str, dict] = {}
    for source_id, element in source_elements.items():
        ref = str(element.get("name") or "")
        joined = pcb_by_source.get(source_id, [])
        if len(joined) != 1:
            raise PartsBookError(
                f"compiled {ref} must join exactly one pcb_component (found {len(joined)})"
            )
        pcb = joined[0]
        pcb_id = str(pcb.get("pcb_component_id") or "")
        if not pcb_id or pcb_id not in copper_by_component:
            raise PartsBookError(
                f"compiled ref {ref} has no physical SMT/PTH copper land"
            )
        dnp = pcb.get("do_not_place")
        if dnp is True:
            continue
        if dnp is not False:
            raise PartsBookError(
                f"compiled {ref} has no literal populated/DNP assembly state"
            )
        supplier = element.get("supplier_part_numbers")
        jlc = supplier.get("jlcpcb") if isinstance(supplier, dict) else None
        if (
            not isinstance(jlc, list)
            or len(jlc) != 1
            or not isinstance(jlc[0], str)
            or not _EXACT_LCSC_RE.fullmatch(jlc[0])
        ):
            raise PartsBookError(
                f"compiled populated ref {ref} needs exactly one JLCPCB C-number"
            )
        lcsc = jlc[0]
        source_group_id = str(element.get("source_group_id") or "")
        owner = owners.get(source_group_id)
        if owner is None:
            raise PartsBookError(
                f"compiled {ref} has no explicit frozen-block or board parts-owner marker"
            )
        metadata = _metadata_for_compiled_ref(
            ref, lcsc, element, rows, owner=owner
        )
        records[ref] = {"ref": ref, "lcsc": lcsc, **metadata}
    if not records:
        raise PartsBookError("fresh compiler inventory resolved no populated parts")
    return [records[ref] for ref in sorted(records)]


def collect_candidates(
    project: Path, blocks_dir: Path, *, timeout_s: float = INVENTORY_TIMEOUT_S
) -> tuple[list[dict], dict]:
    """Resolve the exact populated set from a fresh compiler artifact."""

    selected = read_selected_blocks(project, blocks_dir)
    expected_roots, allowed_board_markers = _require_block_owner_markers(
        project, blocks_dir, selected
    )
    before, _ = _composition_fingerprint(project, blocks_dir, selected)
    compiler = INVENTORY_FN or _compile_inventory
    elements, evidence = compiler(project, blocks_dir, selected, timeout_s)
    if not isinstance(elements, list) or not isinstance(evidence, dict):
        raise PartsBookError("parts inventory compiler returned malformed evidence")
    after, _ = _composition_fingerprint(project, blocks_dir, selected)
    if after != before:
        raise PartsBookError(
            "project composition changed during the fresh populated-parts inventory"
        )
    recorded = evidence.get("compositionSha256")
    if recorded not in (None, before):
        raise PartsBookError(
            "parts inventory evidence does not match the current project composition"
        )
    evidence = dict(evidence)
    evidence["compositionSha256"] = before
    rows = _block_metadata_rows(project, blocks_dir, selected)
    return _records_from_inventory(
        elements, rows, selected, expected_roots, allowed_board_markers
    ), evidence


# --------------------------------------------------------------------------
# jlcsearch lookup (never in a build loop — cold queries take 47-90s)
# --------------------------------------------------------------------------


def cache_dir() -> Path:
    override = os.environ.get("CIRCUIT_PARTS_CACHE_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".autonomous-circuit" / "parts-cache"


def _http_get_json(url: str, timeout: float) -> dict:
    """urllib first; curl on TLS/urllib failure.

    Repo convention: sandboxes intercept TLS and break stdlib urllib, so a
    curl fallback is what actually gets the bytes. Both are best-effort —
    every caller degrades to a lookup_note.
    """
    try:
        import urllib.request

        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # TLS interception, DNS, HTTP error, timeout
        try:
            proc = subprocess.run(
                ["curl", "-sS", "--max-time", str(int(timeout)), url],
                capture_output=True, text=True, timeout=timeout + 10,
            )
        except Exception:
            raise exc
        if proc.returncode != 0 or not proc.stdout.strip():
            raise exc
        return json.loads(proc.stdout)


def _cached(lcsc: str, max_age_days: float) -> dict | None:
    path = cache_dir() / f"{lcsc}.json"
    if not path.is_file():
        return None
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(blob["fetched_at"])
    except Exception:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - fetched).total_seconds() / 86400
    if age_days > max_age_days:
        return None
    component = blob.get("component")
    return component if isinstance(component, dict) else None


def _write_cache(lcsc: str, component: dict) -> None:
    path = cache_dir() / f"{lcsc}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "component": component,
        }), encoding="utf-8")
    except OSError:
        pass  # a cache miss is never a failure


def lookup_lcsc(lcsc: str, *, timeout: float = LOOKUP_TIMEOUT_S,
                retries: int = LOOKUP_RETRIES,
                max_age_days: float = CACHE_MAX_AGE_DAYS,
                use_cache: bool = True) -> dict:
    """One exact orderable number -> the jlcsearch component record.

    Raises RuntimeError when the part can't be resolved (offline, slow, or
    the number returns nothing).
    """
    if use_cache:
        hit = _cached(lcsc, max_age_days)
        if hit is not None:
            return hit
    if LOOKUP_FN is not None:
        component = LOOKUP_FN(lcsc)
        if not component:
            raise RuntimeError(f"{lcsc}: no catalog match")
        _write_cache(lcsc, component)
        return component
    url = f"{JLCSEARCH_URL}?q={lcsc}&limit=1"
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            payload = _http_get_json(url, timeout)
            components = payload.get("components") or []
            if not components:
                raise RuntimeError(f"{lcsc}: no catalog match")
            component = components[0]
            _write_cache(lcsc, component)
            return component
        except Exception as exc:
            last = exc
            if attempt + 1 < max(1, retries):
                time.sleep(1.0)
    raise RuntimeError(f"{lcsc}: {last}")


def apply_component(record: dict, component: dict) -> None:
    """Fold a jlcsearch component onto a part record."""
    number = component.get("lcsc")
    returned = (
        number
        if isinstance(number, str) and number.startswith("C")
        else f"C{number}"
    )
    if returned != record["lcsc"]:
        raise PartsBookError(
            f"catalog returned {returned} while resolving exact identity {record['lcsc']}"
        )
    if not isinstance(component.get("is_basic"), bool):
        raise PartsBookError(
            f"catalog returned no literal Basic/Extended classification for {record['lcsc']}"
        )
    record["mfr"] = component.get("mfr") or record.get("mfr", "")
    if not str(record.get("description") or "").strip() and record["mfr"]:
        record["description"] = str(record["mfr"])
    record["package"] = component.get("package") or record.get("package", "")
    record["basic"] = bool(component.get("is_basic"))
    record["preferred"] = bool(component.get("is_preferred"))
    stock = component.get("stock")
    price = component.get("price")
    record["stock"] = int(stock) if isinstance(stock, (int, float)) else None
    record["unit_price_usd"] = (
        round(float(price), 6) if isinstance(price, (int, float)) else None
    )
    record["stock_checked"] = _today()
    record["source"] = "jlcsearch"


# --------------------------------------------------------------------------
# parts.json (owned wholly)
# --------------------------------------------------------------------------


_CATALOG_FIELDS = (
    "stock",
    "unit_price_usd",
    "stock_checked",
    "datasheet_url",
    "preferred",
    "source",
)
_REF_REVIEW_FIELDS = ("override", "footprint_risk", "swapped_from")


def _consistent_value(records: list[dict], key: str, lcsc: str):
    values = [record.get(key) for record in records if record.get(key) not in (None, "")]
    if not values:
        return None
    first = values[0]
    if any(value != first for value in values[1:]):
        raise PartsBookError(
            f"existing parts.json has conflicting {key} metadata for {lcsc}"
        )
    return first


def read_existing(path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Read exact or legacy output solely to preserve reviewed metadata.

    Identity is always regenerated from the selected source.  Legacy wrapper
    records are accepted as migration input but can contribute only catalog
    facts keyed by LCSC, never old grouped refdes.
    """

    if not path.is_file():
        return {}, {}
    try:
        blob = _load_json_no_duplicates(path)
    except FileNotFoundError:
        return {}, {}
    exact_by_ref: dict[str, dict] = {}
    if isinstance(blob, dict) and isinstance(blob.get("parts"), list):
        raw_records = blob["parts"]
    elif isinstance(blob, list):
        raw_records = blob
    elif isinstance(blob, dict):
        raw_records = []
        for ref, entry in blob.items():
            if not _EXACT_REF_RE.fullmatch(ref) or not isinstance(entry, dict):
                raise PartsBookError(
                    "existing parts.json is neither the exact-ref object nor the "
                    "legacy parts-book wrapper"
                )
            copied = dict(entry)
            copied["_ref"] = ref
            raw_records.append(copied)
            exact_by_ref[ref] = copied
    else:
        raise PartsBookError(
            "existing parts.json must be an exact-ref object or legacy wrapper"
        )
    grouped: dict[str, list[dict]] = {}
    for record in raw_records:
        if not isinstance(record, dict) or not _EXACT_LCSC_RE.fullmatch(
            str(record.get("lcsc") or "")
        ):
            raise PartsBookError(
                "existing parts.json contains a record without one exact LCSC number"
            )
        if "basic" in record and not isinstance(record.get("basic"), bool):
            raise PartsBookError("existing parts.json has non-boolean Basic classification")
        for key in ("description", "mfr", "package"):
            if key in record and (
                not isinstance(record[key], str) or not record[key].strip()
            ):
                raise PartsBookError(
                    f"existing parts.json has non-string/empty reviewed {key} metadata"
                )
        for key in ("stock", "unit_price_usd"):
            if key in record and not isinstance(record[key], (int, float)):
                raise PartsBookError(f"existing parts.json has non-numeric {key}")
        if "stock_checked" in record and (
            not isinstance(record["stock_checked"], str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["stock_checked"])
        ):
            raise PartsBookError("existing parts.json has invalid stock_checked date")
        grouped.setdefault(str(record["lcsc"]), []).append(record)
    by_lcsc: dict[str, dict] = {}
    for lcsc, records in grouped.items():
        latest = max(records, key=lambda record: str(record.get("stock_checked") or ""))
        snapshot: dict = {}
        for key in ("basic", "mfr", "package", "datasheet_url", "preferred"):
            value = _consistent_value(records, key, lcsc)
            if value is not None:
                snapshot[key] = value
        descriptions = {
            record["description"].strip()
            for record in records
            if isinstance(record.get("description"), str) and record["description"].strip()
        }
        if len(descriptions) == 1:
            snapshot["description"] = descriptions.pop()
        for key in ("stock", "unit_price_usd", "stock_checked"):
            if latest.get(key) not in (None, ""):
                snapshot[key] = latest[key]
        sources = [record.get("source") for record in records if record.get("source")]
        if sources:
            snapshot["source"] = "jlcsearch" if "jlcsearch" in sources else sources[0]
        by_lcsc[lcsc] = snapshot
    return by_lcsc, exact_by_ref


def carry_forward(record: dict, previous_lcsc: dict, previous_ref: dict) -> None:
    """Keep reviewed facts without letting an old file own ref identity."""

    same_exact_identity = str(previous_ref.get("lcsc") or "") == record["lcsc"]
    if same_exact_identity and isinstance(previous_ref.get("basic"), bool):
        # The exact populated-ref lock is more specific than a block table row
        # shared by multiple variants and is already consumed by the product
        # profile/BOM gate. Preserve that reviewed classification offline;
        # --lookup can deliberately refresh it from the catalog.
        record["basic"] = previous_ref["basic"]
    if same_exact_identity:
        for key in ("description", "mfr", "package"):
            if record.get(key) in (None, "") and previous_ref.get(key) not in (None, ""):
                record[key] = previous_ref[key]
    for key in _CATALOG_FIELDS:
        if key == "basic" and same_exact_identity:
            continue
        if record.get(key) in (None, "") and previous_lcsc.get(key) not in (None, ""):
            record[key] = previous_lcsc[key]
    if (
        previous_lcsc.get("source") == "jlcsearch"
        and record.get("source") in {"block-default", "compiled-block", "compiled-board"}
    ):
        record["source"] = "jlcsearch-cached"
    if same_exact_identity:
        for key in _REF_REVIEW_FIELDS:
            if previous_ref.get(key) not in (None, "") and key not in record:
                record[key] = previous_ref[key]


def finalize(record: dict) -> dict:
    """Return the strict value object stored under one exact ref key."""

    ref = str(record.get("ref") or "")
    lcsc = str(record.get("lcsc") or "")
    raw_description = record.get("description")
    if not isinstance(raw_description, str):
        raise PartsBookError(f"{ref or 'component'} has non-string reviewed description")
    description = raw_description.strip()
    block = str(record.get("block") or "").strip()
    if not _EXACT_REF_RE.fullmatch(ref):
        raise PartsBookError(f"resolved component ref {ref!r} is not exact uppercase")
    if not _EXACT_LCSC_RE.fullmatch(lcsc):
        raise PartsBookError(f"{ref} has no exact orderable LCSC number")
    if not description:
        raise PartsBookError(f"{ref}/{lcsc} has no reviewed description")
    if not block or (block != "board" and not _BLOCK_ID_RE.fullmatch(block)):
        raise PartsBookError(f"{ref}/{lcsc} has invalid block ownership {block!r}")
    if not isinstance(record.get("basic"), bool):
        raise PartsBookError(
            f"{ref}/{lcsc} has no reviewed Basic/Extended classification; "
            "run --lookup or provide --basic/--extended for a manual change"
        )
    out = {
        "lcsc": lcsc,
        "basic": record["basic"],
        "description": description,
        "block": block,
    }
    for key in (
        "mfr",
        "package",
        "stock",
        "unit_price_usd",
        "stock_checked",
        "datasheet_url",
        "source",
        "preferred",
        "override",
        "footprint_risk",
        "swapped_from",
        "lookup_mismatch",
    ):
        if record.get(key) not in (None, ""):
            if key in {"mfr", "package"} and not isinstance(record[key], str):
                raise PartsBookError(f"{ref}/{lcsc} has non-string reviewed {key}")
            out[key] = record[key]
    return out


def write_parts_json(
    path: Path, parts: dict[str, dict], *, precommit=None
) -> None:
    """Atomically replace the whole exact-ref lock; no wrapper metadata."""

    payload = json.dumps(dict(sorted(parts.items())), indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".parts-", suffix=".json", delete=False
    ) as handle:
        staged = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if precommit is not None:
            precommit()
        os.replace(staged, path)
    finally:
        if staged.exists():
            staged.unlink()


def _assert_inventory_composition_current(
    project: Path, blocks_dir: Path, expected_sha256: str, parts_path: Path,
    expected_parts_sha256: str | None,
) -> None:
    selected = read_selected_blocks(project, blocks_dir)
    actual, _ = _composition_fingerprint(project, blocks_dir, selected)
    if actual != expected_sha256:
        raise PartsBookError(
            "project composition changed after parts inventory; refusing a stale parts.json"
        )
    current_parts_sha256 = _sha256(parts_path) if parts_path.is_file() else None
    if current_parts_sha256 != expected_parts_sha256:
        raise PartsBookError(
            "parts.json changed concurrently; refusing to overwrite another review"
        )


def _read_review_manifest(path: Path) -> dict[str, dict]:
    """Reviewed metadata for compiler-proven board-owned refs, atomically."""

    if path.is_symlink() or not path.is_file():
        raise PartsBookError(f"review manifest must be one regular JSON file: {path}")
    payload = _load_json_no_duplicates(path)
    if not isinstance(payload, dict):
        raise PartsBookError("review manifest must be an exact ref -> entry object")
    allowed = {"lcsc", "basic", "description", "mfr", "package"}
    reviewed: dict[str, dict] = {}
    for ref, entry in payload.items():
        if not _EXACT_REF_RE.fullmatch(str(ref)) or not isinstance(entry, dict):
            raise PartsBookError(f"review manifest has unsafe exact ref {ref!r}")
        if set(entry) - allowed:
            raise PartsBookError(
                f"review manifest {ref} has unsupported fields: "
                + ", ".join(sorted(set(entry) - allowed))
            )
        if (
            not _EXACT_LCSC_RE.fullmatch(str(entry.get("lcsc") or ""))
            or not isinstance(entry.get("basic"), bool)
            or not isinstance(entry.get("description"), str)
            or not entry["description"].strip()
            or any(
                key in entry
                and (
                    not isinstance(entry[key], str)
                    or not entry[key].strip()
                )
                for key in ("mfr", "package")
            )
        ):
            raise PartsBookError(
                f"review manifest {ref} needs exact lcsc, boolean basic, and description"
            )
        reviewed[str(ref)] = dict(entry)
    return reviewed


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/parts",
        description=(
            "Lock a board project's BOM identities into parts.json "
            "(owned wholly by parts-book)."
        ),
    )
    p.add_argument("project", type=Path,
                   help="Project directory containing product.json.")
    p.add_argument("--lookup", action="store_true",
                   help="Refresh stock/price/Basic from jlcsearch (slow: cold "
                        "queries take 47-90s; never run inside a build loop).")
    p.add_argument("--blocks", type=Path, default=None,
                   help="Frozen blocks directory (default: <project>/blocks); "
                        "its bytes must match golden-blocks.lock.json.")
    p.add_argument(
        "--inventory-timeout",
        type=float,
        default=INVENTORY_TIMEOUT_S,
        help="Seconds allowed for the fresh offline routing-disabled inventory compile.",
    )
    p.add_argument(
        "--review-file",
        type=Path,
        default=None,
        help=(
            "Atomic exact-ref metadata for compiler-proven board-owned parts; "
            "never adds population absent from the fresh inventory."
        ),
    )
    mutation = p.add_mutually_exclusive_group()
    mutation.add_argument("--add", metavar="REF", default=None,
                   help="Add one exact board-owned glue ref (requires --lcsc "
                        "and --description).")
    mutation.add_argument("--swap", metavar="REF", default=None,
                   help="Point one exact populated ref at a different orderable "
                        "number (requires --lcsc, --description, and --package).")
    p.add_argument("--lcsc", default=None, help="Exact LCSC number, e.g. C6186.")
    p.add_argument("--mfr", default=None)
    p.add_argument("--package", default=None)
    p.add_argument("--description", default=None,
                   help="Reviewed part description (required by --add and --swap).")
    classification = p.add_mutually_exclusive_group()
    classification.add_argument("--basic", action="store_true",
                                help="Record a reviewed JLC Basic classification.")
    classification.add_argument("--extended", action="store_true",
                                help="Record a reviewed JLC Extended classification.")
    p.add_argument("--timeout", type=float, default=LOOKUP_TIMEOUT_S)
    p.add_argument("--retries", type=int, default=LOOKUP_RETRIES)
    p.add_argument("--max-age-days", type=float, default=CACHE_MAX_AGE_DAYS,
                   help="Cache entries older than this are refetched.")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore the on-disk cache for this run.")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        project = args.project
        if project.is_file() and project.name == PRODUCT_FILE:
            project = project.parent
        product_json = project / PRODUCT_FILE
        if not product_json.is_file():
            raise PartsBookError(
                f"no {PRODUCT_FILE} found at {product_json} — point parts-book "
                "at one board project"
            )
        if (args.add or args.swap) and not args.lcsc:
            raise PartsBookError(
                "--add/--swap require --lcsc with one exact orderable number"
            )
        if args.lcsc and not _EXACT_LCSC_RE.fullmatch(args.lcsc):
            raise PartsBookError(
                f"--lcsc must be one exact LCSC number like C6186, got {args.lcsc!r}"
            )
        for option, value in (("--add", args.add), ("--swap", args.swap)):
            if value is not None and not _EXACT_REF_RE.fullmatch(value):
                raise PartsBookError(
                    f"{option} requires one exact uppercase populated ref, got {value!r}"
                )
        if args.add and not str(args.description or "").strip():
            raise PartsBookError("--add requires a reviewed --description")
        manual_basic = True if args.basic else False if args.extended else None
        mutation_selected = bool(args.add or args.swap)
        manual_values = {
            "--lcsc": args.lcsc,
            "--mfr": args.mfr,
            "--package": args.package,
            "--description": args.description,
            "--basic/--extended": manual_basic,
        }
        if not mutation_selected and any(value is not None for value in manual_values.values()):
            used = ", ".join(name for name, value in manual_values.items() if value is not None)
            raise PartsBookError(f"{used} require exactly one of --add or --swap")
        if mutation_selected and manual_basic is None:
            raise PartsBookError("--add/--swap require exactly one of --basic or --extended")
        if args.lookup and (mutation_selected or args.review_file is not None):
            raise PartsBookError(
                "--lookup cannot be combined with manual/review mutations; "
                "refresh catalog facts in a separate run"
            )
        if args.review_file is not None and mutation_selected:
            raise PartsBookError("--review-file cannot be combined with --add/--swap")
        blocks_dir = args.blocks or (project / "blocks")
        if not blocks_dir.is_dir():
            raise PartsBookError(f"frozen blocks directory does not exist: {blocks_dir}")

        parts_path = project / PARTS_FILE
        initial_parts_sha256 = _sha256(parts_path) if parts_path.is_file() else None
        records, inventory_evidence = collect_candidates(
            project, blocks_dir, timeout_s=args.inventory_timeout
        )
        by_ref = {record["ref"]: record for record in records}
        previous_lcsc, previous_ref = read_existing(parts_path)
        for record in records:
            carry_forward(
                record,
                previous_lcsc.get(record["lcsc"], {}),
                previous_ref.get(record["ref"], {}),
            )
        notes: list[str] = []
        if args.review_file is not None:
            reviewed_manifest = _read_review_manifest(args.review_file)
            unresolved_board_refs = {
                ref
                for ref, record in by_ref.items()
                if record["block"] == "board"
                and (
                    not isinstance(record.get("basic"), bool)
                    or not str(record.get("description") or "").strip()
                )
            }
            if set(reviewed_manifest) != unresolved_board_refs:
                missing = sorted(unresolved_board_refs - set(reviewed_manifest))
                extra = sorted(set(reviewed_manifest) - unresolved_board_refs)
                detail = [*(f"missing {ref}" for ref in missing)]
                detail.extend(f"unexpected {ref}" for ref in extra)
                raise PartsBookError(
                    "--review-file must exactly cover every unresolved compiled "
                    "board-owned ref: " + "; ".join(detail)
                )
            for ref, reviewed in reviewed_manifest.items():
                record = by_ref.get(ref)
                if record is None:
                    raise PartsBookError(
                        f"review manifest ref {ref} is absent from the fresh populated inventory"
                    )
                if record["lcsc"] != reviewed["lcsc"]:
                    raise PartsBookError(
                        f"review manifest {ref} pins {reviewed['lcsc']} but the fresh "
                        f"compiler pins {record['lcsc']}"
                    )
                if record["block"] != "board":
                    raise PartsBookError(
                        f"review manifest {ref} cannot override frozen block "
                        f"{record['block']!r}; update its BLOCK.md/source instead"
                    )
                record.update(reviewed)
                record["source"] = "review-manifest"
                record["override"] = True
        if args.add:
            record = by_ref.get(args.add)
            if record is None:
                raise PartsBookError(
                    f"--add ref {args.add} is absent from the fresh populated inventory"
                )
            if record["block"] != "board":
                raise PartsBookError(
                    f"--add cannot override frozen block-owned ref {args.add}"
                )
            if record["lcsc"] != args.lcsc:
                raise PartsBookError(
                    f"--add {args.add} pins {args.lcsc} but the fresh compiler pins "
                    f"{record['lcsc']}"
                )
            record.update(
                {
                    "basic": manual_basic,
                    "description": str(args.description).strip(),
                    "mfr": args.mfr or record.get("mfr", ""),
                    "package": args.package or record.get("package", ""),
                    "source": "manual",
                    "override": True,
                }
            )
        swap_note = None
        if args.swap:
            record = by_ref.get(args.swap)
            if record is None:
                raise PartsBookError(
                    f"no populated ref {args.swap} to swap (have: "
                    f"{', '.join(sorted(by_ref)) or 'none'})"
                )
            if not str(args.description or "").strip() or not str(args.package or "").strip():
                raise PartsBookError(
                    "--swap requires reviewed --description and --package for the new identity"
                )
            old_lcsc = record["lcsc"]
            old_package = record.get("package", "")
            record.update(
                {
                    "swapped_from": old_lcsc,
                    "lcsc": args.lcsc,
                    "basic": manual_basic,
                    "override": True,
                    "source": "manual",
                    "stock": None,
                    "unit_price_usd": None,
                    "stock_checked": None,
                    "datasheet_url": _lcsc_url(args.lcsc),
                }
            )
            record["description"] = args.description.strip()
            if args.mfr is not None:
                record["mfr"] = args.mfr
            new_package = args.package
            record["package"] = new_package
            if not old_package or new_package != old_package:
                record["footprint_risk"] = True
                swap_note = (
                    f"FOOTPRINT CHANGE/UNVERIFIED: {args.swap} moved {old_lcsc} "
                    f"({old_package or 'unknown old package'}) "
                    f"-> {args.lcsc} ({new_package}). This invalidates the LAYOUT, "
                    "not just the BOM — re-author the block land pattern and rebuild "
                    "every board before ordering."
                )
            notes.append(
                f"{args.swap} is pinned by block {record['block']} — the exact-ref "
                "swap will raise part_drift until that block source pins the same LCSC"
            )

        records = [by_ref[ref] for ref in sorted(by_ref)]
        failures: list[str] = []
        if args.lookup:
            refreshed: dict[str, dict] = {}
            for lcsc in sorted({record["lcsc"] for record in records}):
                try:
                    refreshed[lcsc] = lookup_lcsc(
                        lcsc,
                        timeout=args.timeout,
                        retries=args.retries,
                        max_age_days=args.max_age_days,
                        use_cache=not args.no_cache,
                    )
                except Exception as exc:
                    failures.append(str(exc))
            for record in records:
                component = refreshed.get(record["lcsc"])
                if component is not None:
                    apply_component(record, component)

        final = {record["ref"]: finalize(record) for record in records}
        expected_composition = str(inventory_evidence["compositionSha256"])
        write_parts_json(
            parts_path,
            final,
            precommit=lambda: _assert_inventory_composition_current(
                project, blocks_dir, expected_composition, parts_path,
                initial_parts_sha256,
            ),
        )
        out: dict = {
            "ok": True,
            "inventory": inventory_evidence,
            "parts": [
                {
                    "ref": ref,
                    "lcsc": entry["lcsc"],
                    "stock_checked": entry.get("stock_checked"),
                    "basic": entry["basic"],
                }
                for ref, entry in final.items()
            ],
        }
        if failures:
            out["lookup_note"] = (
                f"{len(failures)} of {len({record['lcsc'] for record in records})} "
                "LCSC identities could not be refreshed "
                f"(first: {failures[0]}); preserved reviewed metadata by LCSC. "
                "jlcsearch cold queries take 47-90s."
            )
        if swap_note:
            notes.insert(0, swap_note)
        if notes:
            out["notes"] = notes
        print(json.dumps(out))
        return 0
    except PartsBookError as exc:
        return _err(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
