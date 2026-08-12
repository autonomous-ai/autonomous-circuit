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

# Test seam: a callable (lcsc: str) -> component dict. When set it replaces
# every network call (the tests never touch the network).
LOOKUP_FN = None

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


def _expand_fixed_refs(raw: str, *, block: str) -> list[str]:
    """Expand only finite exact groups/ranges; reject parametric notation."""

    cell = _clean_markdown(raw)
    refs: list[str] = []
    for token in re.split(r"\s*[,/]\s*", cell):
        token = token.strip()
        if _EXACT_REF_RE.fullmatch(token):
            refs.append(token)
            continue
        ranged = re.fullmatch(r"([A-Z]+)(\d+)\s*[\-–—]\s*([A-Z]+)?(\d+)", token)
        if ranged:
            left_prefix, first, right_prefix, last = ranged.groups()
            right_prefix = right_prefix or left_prefix
            start, stop = int(first), int(last)
            if left_prefix != right_prefix or stop < start or stop - start > 200:
                raise PartsBookError(
                    f"block {block!r} has unsafe ref range {raw!r}"
                )
            refs.extend(f"{left_prefix}{number}" for number in range(start, stop + 1))
            continue
        raise PartsBookError(
            f"block {block!r} has unresolved parametric/alternate ref {raw!r}; "
            "lock an explicit populated instance instead of guessing"
        )
    if not refs or len(refs) != len(set(refs)):
        raise PartsBookError(f"block {block!r} has an empty/duplicate ref group {raw!r}")
    return refs


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
    import_re = re.compile(r"(?:from\s*|import\s*)[\"'](?P<path>\.\.?/[^\"']+)[\"']")
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
        for match in import_re.finditer(text):
            candidate = (source.parent / match.group("path")).resolve()
            try:
                imported_relative = candidate.relative_to(project.resolve())
            except ValueError:
                continue
            if imported_relative.parts and imported_relative.parts[0] == "blocks":
                continue
            choices = [
                candidate,
                *(candidate.with_suffix(suffix) for suffix in _PROJECT_SOURCE_SUFFIXES),
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


def _resolve_ref(default_ref: str, prop: str | None, attrs: str, *, block: str) -> str:
    if re.search(r"\{\s*\.\.\.", attrs):
        raise PartsBookError(
            f"block {block!r} uses a JSX prop spread; exact ref overrides cannot "
            "be proven statically"
        )
    if prop is None:
        return default_ref
    matches = list(re.finditer(
        rf"(?:^|\s){re.escape(prop)}\s*=\s*(?:"
        r'\"(?P<double>[^\"]+)\"|\'(?P<single>[^\']+)\'|'
        r"\{\s*[\"'](?P<braced>[^\"']+)[\"']\s*\}|(?P<dynamic>\{[^}]*\}))",
        attrs,
        re.S,
    ))
    if not matches:
        return default_ref
    if len(matches) != 1:
        raise PartsBookError(
            f"block {block!r} prop {prop!r} is assigned more than once"
        )
    match = matches[0]
    value = match.group("double") or match.group("single") or match.group("braced")
    if value is None or not _EXACT_REF_RE.fullmatch(value):
        raise PartsBookError(
            f"block {block!r} prop {prop!r} is dynamic/non-exact; cannot prove "
            f"the populated ref derived from {default_ref}"
        )
    return value


def collect_candidates(project: Path, blocks_dir: Path) -> list[dict]:
    """Resolve one record per populated fixed ref from selected block instances."""

    selected = read_selected_blocks(project, blocks_dir)
    instances = _imported_instances(project, selected)
    pinned_lcsc: set[str] = set()
    for block in selected:
        for source in sorted((blocks_dir / block).rglob("*.tsx")):
            pinned_lcsc.update(scan_block_tsx(source))
    candidates: dict[str, dict] = {}
    for instance in instances:
        block = instance["block"]
        block_source = blocks_dir / block / f"{block}.tsx"
        if not block_source.is_file():
            raise PartsBookError(f"selected block source missing: {block_source}")
        ref_props = _default_ref_props(block_source)
        for row in parse_block_md(blocks_dir / block / "BLOCK.md"):
            if row["lcsc"] not in pinned_lcsc:
                raise PartsBookError(
                    f"block {block!r} documents {row['lcsc']} but the selected "
                    "frozen source does not pin it"
                )
            for default_ref in _expand_fixed_refs(row["ref_cell"], block=block):
                ref = _resolve_ref(
                    default_ref,
                    ref_props.get(default_ref),
                    instance["attrs"],
                    block=block,
                )
                if ref in candidates:
                    previous = candidates[ref]
                    raise PartsBookError(
                        f"ambiguous duplicate populated ref {ref}: block {previous['block']} "
                        f"pins {previous['lcsc']} and block {block} pins {row['lcsc']}"
                    )
                candidates[ref] = {
                    "ref": ref,
                    "lcsc": row["lcsc"],
                    "basic": row["basic"],
                    "description": row["description"],
                    "block": block,
                    "mfr": row["mfr"],
                    "package": row["package"],
                    "source": "block-default",
                }
    if not candidates:
        raise PartsBookError("selected block instances resolved no populated parts")
    return [candidates[ref] for ref in sorted(candidates)]


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
    if number is not None and f"C{number}" != record["lcsc"]:
        record["lookup_mismatch"] = f"C{number}"
    record["mfr"] = component.get("mfr") or record.get("mfr", "")
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
    "basic",
    "mfr",
    "package",
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
        grouped.setdefault(str(record["lcsc"]), []).append(record)
    by_lcsc: dict[str, dict] = {}
    for lcsc, records in grouped.items():
        latest = max(records, key=lambda record: str(record.get("stock_checked") or ""))
        snapshot: dict = {}
        for key in ("basic", "mfr", "package", "datasheet_url", "preferred"):
            value = _consistent_value(records, key, lcsc)
            if value is not None:
                snapshot[key] = value
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
    for key in _CATALOG_FIELDS:
        if key == "basic" and same_exact_identity:
            continue
        if record.get(key) in (None, "") and previous_lcsc.get(key) not in (None, ""):
            record[key] = previous_lcsc[key]
    if (
        previous_lcsc.get("source") == "jlcsearch"
        and record.get("source") == "block-default"
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
    description = str(record.get("description") or "").strip()
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
            out[key] = record[key]
    return out


def write_parts_json(path: Path, parts: dict[str, dict]) -> None:
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
        os.replace(staged, path)
    finally:
        if staged.exists():
            staged.unlink()


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
    mutation = p.add_mutually_exclusive_group()
    mutation.add_argument("--add", metavar="REF", default=None,
                   help="Add one exact board-owned glue ref (requires --lcsc "
                        "and --description).")
    mutation.add_argument("--swap", metavar="REF", default=None,
                   help="Point one exact populated ref at a different orderable "
                        "number (requires --lcsc).")
    p.add_argument("--lcsc", default=None, help="Exact LCSC number, e.g. C6186.")
    p.add_argument("--mfr", default=None)
    p.add_argument("--package", default=None)
    p.add_argument("--description", default=None,
                   help="Reviewed part description (required by --add; optional on swap).")
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
        blocks_dir = args.blocks or (project / "blocks")
        if not blocks_dir.is_dir():
            raise PartsBookError(f"frozen blocks directory does not exist: {blocks_dir}")

        records = collect_candidates(project, blocks_dir)
        by_ref = {record["ref"]: record for record in records}
        notes: list[str] = []
        if args.add:
            if args.add in by_ref:
                raise PartsBookError(
                    f"populated ref {args.add} already exists; use --swap to repoint it"
                )
            by_ref[args.add] = {
                "ref": args.add,
                "lcsc": args.lcsc,
                "basic": manual_basic,
                "description": str(args.description).strip(),
                "block": "board",
                "mfr": args.mfr or "",
                "package": args.package or "",
                "source": "manual",
                "override": True,
            }
        swap_note = None
        if args.swap:
            record = by_ref.get(args.swap)
            if record is None:
                raise PartsBookError(
                    f"no populated ref {args.swap} to swap (have: "
                    f"{', '.join(sorted(by_ref)) or 'none'})"
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
            if args.description is not None:
                record["description"] = args.description.strip()
            if args.mfr is not None:
                record["mfr"] = args.mfr
            new_package = args.package if args.package is not None else old_package
            record["package"] = new_package
            if old_package and new_package and new_package != old_package:
                record["footprint_risk"] = True
                swap_note = (
                    f"FOOTPRINT CHANGE: {args.swap} moved {old_lcsc} ({old_package}) "
                    f"-> {args.lcsc} ({new_package}). This invalidates the LAYOUT, "
                    "not just the BOM — re-author the block land pattern and rebuild "
                    "every board before ordering."
                )
            notes.append(
                f"{args.swap} is pinned by block {record['block']} — the exact-ref "
                "swap will raise part_drift until that block source pins the same LCSC"
            )

        records = [by_ref[ref] for ref in sorted(by_ref)]
        parts_path = project / PARTS_FILE
        previous_lcsc, previous_ref = read_existing(parts_path)
        failures: list[str] = []
        for record in records:
            carry_forward(
                record,
                previous_lcsc.get(record["lcsc"], {}),
                previous_ref.get(record["ref"], {}),
            )
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
        write_parts_json(parts_path, final)
        out: dict = {
            "ok": True,
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
