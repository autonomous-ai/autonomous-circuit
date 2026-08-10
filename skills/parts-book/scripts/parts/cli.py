"""``python scripts/parts <project_dir> [--lookup] [--add …] [--swap …]``.

Locks the BOM identities of an Autonomous Circuit board project into
``parts.json`` — the file this skill owns **wholly** (contract §3: no
guarded-block markers, because circuitcode never writes parts.json and
parts-book never writes TSX).

What it does:

  * reads the golden blocks the project was created with
    (``<project>/blocks/<id>/<id>.tsx`` + ``BLOCK.md``; falls back to the
    repo's ``packages/golden-blocks/blocks`` when the project has none)
    and derives one candidate slot per **unique orderable part** — the
    TSX ``supplierPartNumbers`` are the part lock, BLOCK.md's parts table
    supplies refdes, package, Basic status, and the human description,
  * carries previously checked stock/price forward by LCSC number so an
    offline re-sync never erases a lookup,
  * with ``--lookup``, refreshes stock / unit price / Basic status from
    jlcsearch (90s timeout, retries, on-disk cache) and degrades to a
    ``lookup_note`` when the network is unavailable or slow,
  * rewrites ``parts.json`` whole, sorted by part id.

Prints a single JSON line on stdout::

  {"ok": true, "parts": [{"id": "ams1117-3.3", "lcsc": "C6186",
                          "stock_checked": "2026-08-10", "basic": true}]}

Never call this inside a build loop: a cold jlcsearch query takes 47-90s
(measured 2026-08-10).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

PARTS_FILE = "parts.json"
PRODUCT_FILE = "product.json"
SCHEMA_VERSION = 1

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


def parse_block_md(path: Path) -> dict[str, dict]:
    """LCSC number -> the row BLOCK.md's parts table documents for it."""
    rows: dict[str, dict] = {}
    if not path.is_file():
        return rows
    in_parts = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_parts = stripped.lower().startswith("## parts")
            continue
        if not in_parts or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 5 or set(cells[0]) <= set("-: "):
            continue
        refdes_cell, part_cell, lcsc_cell, pkg_cell, basic_cell = cells[:5]
        note = cells[5] if len(cells) > 5 else ""
        lcsc_match = _LCSC_RE.search(lcsc_cell)
        if not lcsc_match:
            continue
        lcsc = f"C{lcsc_match.group(1)}"
        refdes = [r.strip() for r in refdes_cell.split(",") if r.strip()]
        if lcsc in rows:
            # One part used by several rows (C1525 covers three in
            # rp2040-core) — keep the first description, union the refdes.
            for r in refdes:
                if r not in rows[lcsc]["refdes"]:
                    rows[lcsc]["refdes"].append(r)
            continue
        description = re.sub(r"\*\*", "", part_cell).strip()
        head = description.split(",")[0].strip()
        mfr = head if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+-]*", head) else ""
        rows[lcsc] = {
            "refdes": refdes,
            "description": description,
            "mfr": mfr,
            "package": re.sub(r"\*\*", "", pkg_cell).strip(),
            "basic": re.sub(r"\*\*", "", basic_cell).strip().lower().startswith("yes"),
            "note": note,
        }
    return rows


def _part_id(entry: dict) -> str:
    """Readable, deterministic slug. Passives read by value, ICs by mfr."""
    tags = entry.get("tags") or []
    tag = tags[0] if tags else ""
    value = entry.get("value") or ""
    pkg = entry.get("footprint") or entry.get("package") or ""
    short_pkg = pkg if re.fullmatch(r"[\w.-]{1,12}", pkg or "") else ""
    prefix = {
        "resistor": "r", "capacitor": "c", "led": "led", "crystal": "y",
        "inductor": "l", "diode": "d",
    }.get(tag, "")
    if prefix and value:
        return _slug("-".join(p for p in (prefix, value, short_pkg) if p))
    mfr = entry.get("mfr") or ""
    if mfr:
        return _slug(mfr)
    if tag == "pushbutton":
        return _slug(f"sw-{entry['lcsc']}")
    return _slug(entry["lcsc"])


def collect_candidates(blocks_dir: Path) -> tuple[list[dict], list[str]]:
    """Candidate slots from every block in ``blocks_dir``, plus drift notes.

    Two passes, because a block may compose a component another block
    defines (``usb-c-data`` imports the connector and ESD array from
    ``usb-c-power``): pass 1 reads every TSX — the part lock — pass 2 folds
    in each BLOCK.md's parts table and only then calls a documented part
    that no TSX anywhere pins a drift.
    """
    notes: list[str] = []
    if not blocks_dir.is_dir():
        return [], [f"no blocks directory at {blocks_dir} — nothing to lock"]

    block_dirs = sorted(p for p in blocks_dir.iterdir() if p.is_dir())
    sources: dict[str, dict] = {}          # lcsc -> what the TSX says
    for block_dir in block_dirs:
        bid = block_dir.name
        tsx = block_dir / f"{bid}.tsx"
        if not tsx.is_file():
            found = sorted(block_dir.glob("*.tsx"))
            if not found:
                continue
            tsx = found[0]
        for lcsc, entry in scan_block_tsx(tsx).items():
            merged = sources.setdefault(lcsc, {
                "lcsc": lcsc, "tags": [], "mfr": "", "footprint": "",
                "value": "", "refdes": [], "blocks": [], "package": "",
                "description": "", "basic": False, "basic_documented": False,
            })
            for tag in entry["tags"]:
                if tag not in merged["tags"]:
                    merged["tags"].append(tag)
            merged["mfr"] = merged["mfr"] or entry["mfr"]
            merged["footprint"] = merged["footprint"] or entry["footprint"]
            merged["value"] = merged["value"] or entry["value"]
            merged["refdes"] = list(dict.fromkeys(merged["refdes"] + entry["refdes"]))
            if bid not in merged["blocks"]:
                merged["blocks"].append(bid)

    for block_dir in block_dirs:
        bid = block_dir.name
        for lcsc, doc in sorted(parse_block_md(block_dir / "BLOCK.md").items()):
            entry = sources.get(lcsc)
            if entry is None:
                notes.append(
                    f"{bid}: BLOCK.md documents {lcsc} but no block TSX pins it"
                )
                continue
            entry["package"] = entry["package"] or doc["package"]
            entry["mfr"] = entry["mfr"] or doc["mfr"]
            entry["description"] = entry["description"] or doc["description"]
            entry["basic"] = entry["basic"] or doc["basic"]
            entry["basic_documented"] = True
            entry["refdes"] = list(dict.fromkeys(entry["refdes"] + doc["refdes"]))
            if bid not in entry["blocks"]:
                entry["blocks"].append(bid)

    parts: dict[str, dict] = {}
    for lcsc in sorted(sources):
        entry = sources[lcsc]
        pid = _part_id(entry)
        if pid in parts:
            notes.append(
                f"id collision: {pid} maps to both {parts[pid]['lcsc']} and "
                f"{lcsc} — give one of them a distinct part id via --add/--swap"
            )
            pid = _slug(f"{pid}-{lcsc}")
        parts[pid] = {
            "id": pid,
            "lcsc": lcsc,
            "mfr": entry["mfr"],
            "package": entry["package"] or entry["footprint"],
            "basic": entry["basic"],
            "basic_documented": entry["basic_documented"],
            "description": entry["description"],
            "refdes": entry["refdes"],
            "blocks": entry["blocks"],
            "source": "block-default",
        }
    return [parts[k] for k in sorted(parts)], notes


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


def read_existing(path: Path) -> dict[str, dict]:
    """Previously written records, keyed by LCSC number."""
    if not path.is_file():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    parts = blob.get("parts") if isinstance(blob, dict) else blob
    if not isinstance(parts, list):
        return {}
    out = {}
    for part in parts:
        if isinstance(part, dict) and part.get("lcsc"):
            out[str(part["lcsc"])] = part
    return out


def carry_forward(record: dict, previous: dict) -> None:
    """Keep the last lookup's numbers so an offline re-sync loses nothing."""
    for key in ("stock", "unit_price_usd", "stock_checked", "preferred",
                "datasheet_url"):
        if record.get(key) in (None, "") and previous.get(key) not in (None, ""):
            record[key] = previous[key]
    if previous.get("source") == "jlcsearch" and record.get("source") == "block-default":
        record["source"] = "jlcsearch-cached"
    if previous.get("basic") and not record.get("basic_documented"):
        record["basic"] = True
    for key in ("override", "footprint_risk", "swapped_from"):
        if key in previous and key not in record:
            record[key] = previous[key]


def finalize(record: dict) -> dict:
    """The on-disk shape — snake_case, the client's PartsPanel reads it."""
    out = {
        "id": record["id"],
        "lcsc": record["lcsc"],
        "mfr": record.get("mfr", ""),
        "package": record.get("package", ""),
        "basic": bool(record.get("basic", False)),
        "stock": record.get("stock"),
        "unit_price_usd": record.get("unit_price_usd"),
        "stock_checked": record.get("stock_checked"),
        "datasheet_url": record.get("datasheet_url") or _lcsc_url(record["lcsc"]),
        "refdes": record.get("refdes", []),
        "blocks": record.get("blocks", []),
        "source": record.get("source", "block-default"),
    }
    for key in ("description", "preferred", "override", "footprint_risk",
                "swapped_from", "lookup_mismatch"):
        if record.get(key):
            out[key] = record[key]
    return out


def write_parts_json(path: Path, parts: list[dict]) -> None:
    """Whole-file rewrite. parts-book owns this file; nothing is merged in."""
    payload = {
        "version": SCHEMA_VERSION,
        "generator": "parts-book",
        "updated": _today(),
        "summary": {
            "lines": len(parts),
            "basic": sum(1 for p in parts if p.get("basic")),
            "extended": sum(1 for p in parts if not p.get("basic")),
            "checked": sum(1 for p in parts if p.get("stock_checked")),
        },
        "parts": parts,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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
                   help="Blocks directory (default: <project>/blocks, then the "
                        "repo's packages/golden-blocks/blocks).")
    p.add_argument("--add", metavar="ID", default=None,
                   help="Add a glue part no block owns (requires --lcsc).")
    p.add_argument("--swap", metavar="ID", default=None,
                   help="Point an existing part id at a different orderable "
                        "number (requires --lcsc).")
    p.add_argument("--lcsc", default=None, help="Exact LCSC number, e.g. C6186.")
    p.add_argument("--mfr", default=None)
    p.add_argument("--package", default=None)
    p.add_argument("--refdes", default=None,
                   help="Comma-separated refdes for --add.")
    p.add_argument("--timeout", type=float, default=LOOKUP_TIMEOUT_S)
    p.add_argument("--retries", type=int, default=LOOKUP_RETRIES)
    p.add_argument("--max-age-days", type=float, default=CACHE_MAX_AGE_DAYS,
                   help="Cache entries older than this are refetched.")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore the on-disk cache for this run.")
    return p


def _repo_blocks_dir() -> Path:
    # cli.py lives at skills/parts-book/scripts/parts/cli.py -> repo root is 4 up.
    return Path(__file__).resolve().parents[4] / "packages" / "golden-blocks" / "blocks"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    project = args.project
    if project.is_file() and project.name == PRODUCT_FILE:
        project = project.parent
    product_json = project / PRODUCT_FILE
    if not product_json.is_file():
        return _err(f"no {PRODUCT_FILE} found at {product_json} — parts-book "
                    "locks the BOM of a board project, so point it at the "
                    "project directory")

    if (args.add or args.swap) and not args.lcsc:
        return _err("--add/--swap require --lcsc with one exact orderable "
                    "number (never a part family)")
    if args.lcsc and not re.fullmatch(r"C\d+", args.lcsc):
        return _err(f"--lcsc must be one exact LCSC number like C6186, "
                    f"got {args.lcsc!r}")

    blocks_dir = args.blocks or (project / "blocks")
    if not blocks_dir.is_dir():
        blocks_dir = _repo_blocks_dir()

    records, notes = collect_candidates(blocks_dir)
    by_id = {r["id"]: r for r in records}

    if args.add:
        pid = _slug(args.add)
        if pid in by_id:
            return _err(f"part id {pid!r} already exists (pinned to "
                        f"{by_id[pid]['lcsc']}) — use --swap to repoint it")
        record = {
            "id": pid,
            "lcsc": args.lcsc,
            "mfr": args.mfr or "",
            "package": args.package or "",
            "basic": False,
            "refdes": [r.strip() for r in (args.refdes or "").split(",") if r.strip()],
            "blocks": [],
            "source": "manual",
            "override": True,
        }
        by_id[pid] = record
        records = [by_id[k] for k in sorted(by_id)]

    swap_note = None
    if args.swap:
        pid = _slug(args.swap)
        record = by_id.get(pid)
        if record is None:
            return _err(f"no part id {pid!r} to swap (have: "
                        f"{', '.join(sorted(by_id)) or 'none'})")
        old_lcsc = record["lcsc"]
        old_package = record.get("package", "")
        record["swapped_from"] = old_lcsc
        record["lcsc"] = args.lcsc
        record["override"] = True
        record["source"] = "manual"
        record["stock"] = None
        record["unit_price_usd"] = None
        record["stock_checked"] = None
        record["datasheet_url"] = _lcsc_url(args.lcsc)
        if args.mfr is not None:
            record["mfr"] = args.mfr
        new_package = args.package if args.package is not None else old_package
        if old_package and new_package and new_package != old_package:
            record["package"] = new_package
            record["footprint_risk"] = True
            swap_note = (
                f"FOOTPRINT CHANGE: {pid} moved {old_lcsc} ({old_package}) -> "
                f"{args.lcsc} ({new_package}). This invalidates the LAYOUT, not "
                "just the BOM — the block's land pattern must be re-authored "
                "and every board rebuilt before ordering."
            )
        else:
            record["package"] = new_package
        if record.get("blocks"):
            notes.append(
                f"{pid} is pinned by block(s) {', '.join(record['blocks'])} — "
                "the swap lives in parts.json only until the block TSX is "
                "updated; the pipeline will raise part_drift until then"
            )

    # Duplicate-id / duplicate-number refusals (one part = one number).
    seen_ids: dict[str, str] = {}
    seen_lcsc: dict[str, str] = {}
    for record in records:
        pid, lcsc = record["id"], record["lcsc"]
        if pid in seen_ids and seen_ids[pid] != lcsc:
            return _err(f"duplicate part id {pid!r} pinned to both "
                        f"{seen_ids[pid]} and {lcsc}")
        if lcsc in seen_lcsc and seen_lcsc[lcsc] != pid:
            return _err(f"{lcsc} is pinned by two part ids "
                        f"({seen_lcsc[lcsc]} and {pid}) — one part is one id")
        seen_ids[pid] = lcsc
        seen_lcsc[lcsc] = pid

    parts_path = project / PARTS_FILE
    previous = read_existing(parts_path)

    lookup_note = None
    failures: list[str] = []
    for record in records:
        if args.lookup:
            try:
                component = lookup_lcsc(
                    record["lcsc"], timeout=args.timeout, retries=args.retries,
                    max_age_days=args.max_age_days, use_cache=not args.no_cache,
                )
                apply_component(record, component)
            except Exception as exc:
                failures.append(str(exc))
        carry_forward(record, previous.get(record["lcsc"], {}))

    if failures:
        lookup_note = (
            f"{len(failures)} of {len(records)} parts could not be refreshed "
            f"(first: {failures[0]}); wrote the last known values as candidate "
            "slots. jlcsearch cold queries take 47-90s — retry, or lock the "
            "BOM from the block defaults."
        )

    final = [finalize(r) for r in records]
    write_parts_json(parts_path, final)

    out: dict = {
        "ok": True,
        "parts": [
            {"id": p["id"], "lcsc": p["lcsc"],
             "stock_checked": p["stock_checked"], "basic": p["basic"]}
            for p in final
        ],
    }
    if lookup_note:
        out["lookup_note"] = lookup_note
    if swap_note:
        notes.insert(0, swap_note)
    if notes:
        out["notes"] = notes
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
