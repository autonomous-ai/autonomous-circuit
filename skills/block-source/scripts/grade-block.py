#!/usr/bin/env python3
"""Grade a sourced block's provenance, so "verified" is a check and not a claim.

`servo-header` shipped with its evidence written out — the LCSC number, the
95.84% copper IoU, the URL the pin order came from and the date it was read.
Nothing enforced that. The next sourced block could carry a confident sentence
and no number, and the only reader who would notice is the person paying the
fab bill.

So: this grades the `## Provenance` table. It cannot check that a number is
*true* — that is why every field cites where it came from — but it can refuse a
block that never wrote one down. **Silence is never a pass**: a missing field
is reported by name, and an empty table fails exactly as loudly as a wrong one.

One JSON line on stdout, last line wins:

    {"ok": false, "id": "esp32-c3-mini", "class": "certified-module",
     "missing": ["peak_ma"], "problems": [...]}

Exit status is 0 when it ran and 1 when the block does not grade — but read the
JSON, not `$?`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

#: Every field the provenance table must carry. `n/a` is legal only where
#: `_NA_OK` says so — everywhere else a blank is an unfinished block.
REQUIRED = (
    "class",
    "mpn",
    "lcsc",
    "certification",
    "footprint_source",
    "footprint_iou",
    "typical_ma",
    "peak_ma",
    "v_in",
    "keepout",
    "pin_source",
    "verified",
)

CLASSES = ("interconnect", "certified-module", "integrated-module")

#: Required for `integrated-module` and meaningless for the others, so it is
#: checked by class rather than listed in REQUIRED — putting it there would
#: fail every block sourced before this class existed, which is a migration
#: dressed up as a rule.
BY_CLASS_REQUIRED = {"integrated-module": ("integration",)}

#: What each class is allowed to answer `n/a` to, and nothing else.
#:
#: A passive header has no rail, no antenna and no certificate. A certified
#: module answers everything. An **integrated module** may skip the
#: certificate — a display does not radiate and no lab will ever issue it one —
#: but it pays for that with `integration`, which is the sentence that replaces
#: the certificate as evidence: what the module carries, so that nothing active
#: is left for the board to add. That row is required for exactly this class
#: and `n/a` for the other two, so a blank cannot drift between them.
_NA_OK = {
    "interconnect": {"certification", "integration", "typical_ma", "peak_ma",
                     "v_in", "keepout"},
    "certified-module": {"integration"},
    "integrated-module": {"certification", "keepout"},
    # NB: `integration` is deliberately absent here — the one row
    # this class cannot wave away.
}

_NA_RE = re.compile(r"^n/?a\b", re.I)
_ROW_RE = re.compile(r"^\s*\|\s*`?([a-z_]+)`?\s*\|(.*?)\|\s*$")
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_LCSC_RE = re.compile(r"\bC\d{3,}\b")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def parse_provenance(text: str) -> dict[str, str]:
    """The rows of the `## Provenance` table, lowercased keys to raw values.

    Scoped to that one section on purpose: a BLOCK.md is full of tables, and a
    parts table with an `mpn` column would otherwise answer for the provenance
    one — a false pass, which is the only failure mode that matters here.
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*#{1,6}\s+provenance\b", line, re.I):
            start = i + 1
            break
    if start is None:
        return {}
    fields: dict[str, str] = {}
    for line in lines[start:]:
        if re.match(r"^\s*#{1,6}\s+", line):
            break
        m = _ROW_RE.match(line)
        if not m:
            continue
        key, value = m.group(1).lower(), m.group(2).strip()
        # The header separator (|---|---|) and the header row itself.
        if not value or set(value) <= set("-: "):
            continue
        if key in ("field", "value"):
            continue
        fields[key] = value.strip(" `")
    return fields


def _is_na(value: str) -> bool:
    return bool(_NA_RE.match(value))


def _number(value: str) -> float | None:
    m = _NUM_RE.search(value)
    return float(m.group(0)) if m else None


def grade(text: str, block_id: str) -> dict:
    fields = parse_provenance(text)
    missing: list[str] = []
    problems: list[str] = []

    if not fields:
        return {
            "ok": False,
            "id": block_id,
            "class": None,
            "missing": list(REQUIRED),
            "problems": [
                "no `## Provenance` table — a sourced block records where every "
                "number came from, or it is not a sourced block"
            ],
        }

    klass = fields.get("class", "").strip().lower()
    if klass not in CLASSES:
        problems.append(
            f"class is {klass or '(blank)'!r}; a sourced block is one of "
            f"{', '.join(CLASSES)} — anything else is a part whose circuit "
            f"you would have to invent, and that is a gaps entry, not a block"
        )
    na_ok = _NA_OK.get(klass, set())

    for key in REQUIRED + BY_CLASS_REQUIRED.get(klass, ()):
        value = fields.get(key, "").strip()
        if not value:
            missing.append(key)
            continue
        if _is_na(value) and key not in na_ok:
            problems.append(
                f"{key} is `n/a`, which a {klass or 'sourced'} block may not "
                f"answer — the number is what makes the block composable"
            )

    if "lcsc" not in missing and not _LCSC_RE.search(fields.get("lcsc", "")):
        problems.append(
            f"lcsc {fields['lcsc']!r} is not a C-number; a part nobody can "
            f"order is a board nobody can assemble"
        )

    iou = fields.get("footprint_iou", "")
    if "footprint_iou" not in missing:
        value = _number(iou)
        if value is None:
            problems.append(
                f"footprint_iou {iou!r} carries no number — record what "
                f"`tscircuit-cli import --jlcpcb` printed"
            )
        elif not 0 <= value <= 100:
            problems.append(f"footprint_iou {value} is not a percentage")

    typical, peak = fields.get("typical_ma", ""), fields.get("peak_ma", "")
    if not _is_na(typical) and not _is_na(peak):
        t, p = _number(typical), _number(peak)
        if t is not None and p is not None and p < t:
            problems.append(
                f"peak_ma ({p}) is below typical_ma ({t}) — the rail and the "
                f"bulk cap are sized on the burst, so this is the number the "
                f"board would get wrong"
            )

    if "certification" not in missing:
        cert = fields["certification"]
        if klass == "certified-module" and not re.search(r"[A-Za-z0-9]{4,}", cert):
            problems.append(
                "certification carries no identifier — a module without one is "
                "bare silicon wearing a daughterboard"
            )

    if klass == "integrated-module" and "integration" not in missing:
        # The row that stands in for a certificate. "It is a module" restates
        # the class; the claim being made is that nothing active is left for
        # the board to add, and that is a list of parts and a page, or it is
        # nothing.
        integration = fields["integration"]
        if not re.search(r"\d", integration):
            problems.append(
                "integration names no part and no page — it is the evidence "
                "that replaces the certificate for a class that cannot have "
                "one, so it lists what the module carries or the block does "
                "not grade"
            )

    if "verified" not in missing and not _ISO_RE.search(fields["verified"]):
        problems.append(
            f"verified {fields['verified']!r} is not an ISO date; stock, price "
            f"and footprints all drift, so the date is part of the claim"
        )

    for key in ("pin_source", "footprint_source"):
        if key in missing:
            continue
        if not re.search(r"\d|http|datasheet|easyeda|footprinter", fields[key], re.I):
            problems.append(
                f"{key} {fields[key]!r} cites nothing checkable — name the "
                f"datasheet page, or the URL and the date it was read"
            )

    return {
        "ok": not missing and not problems,
        "id": block_id,
        "class": klass or None,
        "missing": missing,
        "problems": problems,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({
            "ok": False, "id": None, "class": None, "missing": [],
            "problems": ["usage: grade-block.py <path-to-BLOCK.md>"],
        }))
        return 1
    path = Path(argv[1]).expanduser()
    if path.is_dir():
        path = path / "BLOCK.md"
    if not path.is_file():
        print(json.dumps({
            "ok": False, "id": None, "class": None, "missing": [],
            "problems": [f"no BLOCK.md at {path}"],
        }))
        return 1
    result = grade(path.read_text(encoding="utf-8"), path.parent.name)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
