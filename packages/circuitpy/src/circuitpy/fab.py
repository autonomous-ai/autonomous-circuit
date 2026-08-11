"""Fab profile abstraction (contract §1): gerber zip layout, BOM/CPL column
mapping, the DFM limit table, the ORDER.md template + cost model, and the
fab-ready rule. v1 ships ``jlcpcb`` only; the fab side never touches the
network (packet + walkthrough, no ordering API).

BOM/CPL source: the circuit-json exporter's ``gerbers`` zip carries ``bom.csv``
(``Designator,Comment,Value,Footprint[,JLCPCB Part #]`` — the part column only
when the parts engine ran) and ``pick_and_place.csv``
(``Designator,Mid X,Mid Y,Layer,Rotation``). This module remaps them into the
profile's shipping columns and merges the ``parts.json`` lock.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from circuitpy.errors import ProjectShapeError

GERBER_MEMBER_SUFFIXES = (".gbr", ".drl", ".xln", ".gtl", ".gbl", ".gts", ".gbs",
                          ".gto", ".gbo", ".gko", ".gml", ".gm1", ".txt")

# Column names the circuit-json exporter uses for the supplier part number.
_EXPORTER_LCSC_COLUMNS = ("JLCPCB Part #", "LCSC Part #", "LCSC")


@dataclass(frozen=True)
class FabProfile:
    """Everything one fab needs. Single owner of every DFM number
    (R2 gauntlet table, JLCPCB 2-layer economy)."""

    id: str
    zip_name: str = "gerbers.zip"
    bom_name: str = "bom.csv"
    cpl_name: str = "cpl.csv"
    order_name: str = "ORDER.md"
    glb_name: str = "board.glb"
    bom_columns: tuple[str, ...] = ("Comment", "Designator", "Footprint", "LCSC Part #")
    cpl_columns: tuple[str, ...] = ("Designator", "Mid X", "Mid Y", "Layer", "Rotation")
    # DFM limits (mm) — conservative cheap-tier encodings of JLC's caps table.
    # Two bands everywhere: block at the fab's real floor, warn at what we
    # would rather see. A blocking gate set to a preference flags legal boards,
    # and a gate that cries wolf is one everyone learns to skip.
    min_trace_mm: float = 0.10           # JLC 1oz floor
    warn_trace_mm: float = 0.15          # our cheap-tier preference
    min_clearance_mm: float = 0.10       # JLC 1oz floor
    warn_clearance_mm: float = 0.127
    #: Slack when handing our floors to a *different* geometry engine. The
    #: router targets 0.1mm and KiCad measures the same copper at 0.0958mm —
    #: a 4um disagreement between two implementations, three orders of
    #: magnitude below anything a fab can hold. Without this the second
    #: substrate rejects every board it is given, which would make the check
    #: worthless rather than strict. Our own stage-4 gate keeps the exact floor.
    drc_tolerance_mm: float = 0.01
    # Vias and component through-holes are DIFFERENT JLC rules; conflating them
    # false-positives on every routed board (the router's own vias are finer
    # than any component hole). Block at JLC's true floor, warn at the
    # conservative cheap-tier number we would rather see.
    min_via_diameter_mm: float = 0.3     # JLC min via pad
    warn_via_diameter_mm: float = 0.45
    min_via_drill_mm: float = 0.15       # JLC min via hole
    warn_via_drill_mm: float = 0.3
    min_via_annular_mm: float = 0.075    # implied by 0.3 pad / 0.15 hole
    warn_via_annular_mm: float = 0.1
    min_pth_drill_mm: float = 0.3        # component through-hole
    min_pth_annular_mm: float = 0.2      # PTH annular ring (JLC spec)
    # Copper-to-hole is TWO rules, and reading jlcpcb.com/capabilities
    # (2026-08-11) they differ by 40%: a non-plated mounting hole needs 0.20mm
    # to a track, a plated hole needs 0.28mm (0.35mm recommended). We had one
    # 0.20mm number standing in for both, which is exactly right for the
    # mounting holes and too permissive for every plated one — a board could
    # clear our gate and still be marginal at the fab.
    min_npth_to_copper_mm: float = 0.20
    min_pth_to_copper_mm: float = 0.28
    warn_pth_to_copper_mm: float = 0.35
    # JLC holds 0.2mm copper-to-outline on a routed edge; 0.3mm is the
    # conservative figure for V-cut and bevelled edges. Blocking at 0.3 made a
    # ground pour impossible — the pour fills to exactly 0.2mm and cannot be
    # told otherwise (minBoardEdgeClearance is silently ignored, verified
    # 2026-08-10), so every board with a plane failed on a legal geometry.
    min_edge_clearance_mm: float = 0.2   # routed-edge floor
    warn_edge_clearance_mm: float = 0.3  # what we would rather see
    min_board_mm: float = 3.0            # min board dimension
    standard_thickness_mm: float = 1.6   # JLC standard (toolchain default is 1.4)
    # Footprint-IoU bands (supplier_footprint_mismatch_warning): correct 0402
    # parts score ~0.73-0.77, so the blocking band sits well below that.
    iou_error_below: float = 0.5
    iou_warning_below: float = 0.65
    iou_info_below: float = 0.85
    # Cost model lines for ORDER.md (verified ranges, 2026-08).
    cost_lines: tuple[str, ...] = (
        "PCB only, 5x 2-layer: $2 + shipping (~$4-20 all-in, 24-48h fab).",
        "Assembled, 5x ESP32-class: ~$75-110 all-in, ~1-2 weeks to door.",
        "Extended parts add a ~$3/line loading fee; Basic parts avoid it.",
    )


PROFILES: dict[str, FabProfile] = {"jlcpcb": FabProfile(id="jlcpcb")}


def get_profile(fab_id: str) -> FabProfile:
    profile = PROFILES.get(fab_id)
    if profile is None:
        raise ProjectShapeError(
            f"unknown fab profile {fab_id!r} (available: {', '.join(sorted(PROFILES))})"
        )
    return profile


# ---------------------------------------------------------------------------
# Exporter packet parsing + lock merge.
# ---------------------------------------------------------------------------


def parse_exporter_bom(text: str) -> list[dict]:
    """Rows from the circuit-json exporter's bom.csv, normalized to
    ``{designator, comment, value, footprint, lcsc}`` (lcsc may be "")."""
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for raw in reader:
        if not raw:
            continue
        lcsc = ""
        for column in _EXPORTER_LCSC_COLUMNS:
            candidate = (raw.get(column) or "").strip()
            if candidate:
                lcsc = candidate
                break
        designator = (raw.get("Designator") or "").strip()
        if not designator:
            continue
        rows.append(
            {
                "designator": designator,
                "comment": (raw.get("Comment") or "").strip(),
                "value": (raw.get("Value") or "").strip(),
                "footprint": (raw.get("Footprint") or "").strip(),
                "lcsc": lcsc,
            }
        )
    return rows


def merge_parts_lock(rows: list[dict], parts: dict[str, dict]) -> list[dict]:
    """Fold the ``parts.json`` lock into BOM rows. Match order: exact part id
    == designator (case-insensitive). A matched row gains ``lock`` (the lock
    entry) and, when the exporter had no part number, its locked ``lcsc``.
    The v1 lock keys parts by component name/refdes; fuzzier mapping belongs
    to parts-book, not here."""
    by_id = {part_id.lower(): (part_id, entry) for part_id, entry in parts.items()}
    merged: list[dict] = []
    for row in rows:
        out = dict(row)
        hit = by_id.get(row["designator"].lower())
        if hit is not None:
            part_id, entry = hit
            out["lock_id"] = part_id
            out["lock"] = entry
            if not out["lcsc"]:
                out["lcsc"] = str(entry.get("lcsc") or "").strip()
        merged.append(out)
    return merged


def bom_summary(rows: list[dict]) -> dict[str, object]:
    """The sidecar's ``bom`` block: lines, orderable, basicParts, and the
    best-effort cost estimate from locked unit prices (omitted when no row
    carries a price — never fabricate a zero)."""
    lines = len(rows)
    orderable = sum(1 for row in rows if row.get("lcsc"))
    basic = sum(1 for row in rows if (row.get("lock") or {}).get("basic") is True)
    summary: dict[str, object] = {
        "lines": lines,
        "orderable": orderable,
        "basicParts": basic,
    }
    total = 0.0
    priced = 0
    for row in rows:
        price = (row.get("lock") or {}).get("price")
        if isinstance(price, (int, float)) and price >= 0:
            total += float(price)
            priced += 1
    if priced:
        summary["estimatedCostUsd"] = round(total, 2)
    return summary


# ---------------------------------------------------------------------------
# Packet writers.
# ---------------------------------------------------------------------------


def write_bom_csv(rows: list[dict], path: Path, profile: FabProfile) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(profile.bom_columns)
        for row in rows:
            comment = row.get("comment") or row.get("value") or ""
            writer.writerow(
                [comment, row["designator"], row.get("footprint") or "", row.get("lcsc") or ""]
            )
    return path


def write_cpl_csv(exporter_cpl_text: str, path: Path, profile: FabProfile) -> Path:
    """Re-emit the exporter's pick_and_place.csv through the profile columns
    (they already match JLC's — this normalizes header order + quoting)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    reader = csv.DictReader(io.StringIO(exporter_cpl_text))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(profile.cpl_columns)
        for raw in reader:
            writer.writerow([(raw.get(column) or "").strip() for column in profile.cpl_columns])
    return path


def repackage_gerbers(source_zip: Path, dest_zip: Path) -> Path:
    """The tscircuit exporter's zip carries bom/cpl csvs inside; the shipping
    gerber zip is gerber+drill members only (consistent layout across the
    kicad-cli and tscircuit sources)."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source_zip) as src, zipfile.ZipFile(
        dest_zip, "w", zipfile.ZIP_DEFLATED
    ) as dst:
        for member in src.namelist():
            lower = member.lower()
            if lower.endswith(GERBER_MEMBER_SUFFIXES) and not lower.endswith(".csv"):
                dst.writestr(member, src.read(member))
    return dest_zip


def zip_directory_gerbers(source_dir: Path, dest_zip: Path) -> Path:
    """Zip every kicad-cli-exported gerber/drill file in ``source_dir``."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as dst:
        for member in sorted(source_dir.iterdir()):
            if member.is_file():
                dst.write(member, member.name)
    return dest_zip


def write_order_md(
    path: Path,
    *,
    product_name: str,
    assembly: bool,
    profile: FabProfile,
    board_width_mm: float,
    board_height_mm: float,
    layers: int,
    bom: dict[str, object],
) -> Path:
    """The exact-clicks JLCPCB walkthrough (R3, verified flow) including the
    placement-preview warning. Written only when the packet is fab-ready."""
    orderable = bom.get("orderable", 0)
    lines = bom.get("lines", 0)
    cost = bom.get("estimatedCostUsd")
    cost_line = (
        f"- Estimated parts cost (locked unit prices): ~${cost}/board.\n"
        if isinstance(cost, (int, float))
        else ""
    )
    assembly_steps = (
        f"""3. Toggle **PCB Assembly** on: PCBA Type **Economic**, Assembly Side **Top**,
   Qty **2** (or 5) -> **Confirm**.
4. Next -> gerber preview renders -> Next.
5. **Add BOM File** -> `{profile.bom_name}`; **Add CPL File** -> `{profile.cpl_name}` ->
   **Process BOM & CPL**.
6. Parts-match table: every line should show a matched C-number and stock
   ({orderable}/{lines} lines carry part numbers in this packet). Shortfalls show
   red — either accept "Do Not Place" or swap the part in chat and re-export.
7. Next -> **component placement preview**. **This screen is the safety net:**
   JLCPCB auto-rotates known parts and its rotation conventions differ from the
   CPL's — eyeball pin-1 orientation on every IC/module, connector orientation,
   and polarized parts before continuing.
8. **Save to Cart** -> checkout, pick Global Standard Direct (cheap) or DHL (fast).
"""
        if assembly
        else """3. Leave PCB Assembly off (bare-PCB order).
4. Next -> gerber preview renders -> verify the outline and drills -> Next.
5. **Save to Cart** -> checkout, pick Global Standard Direct (cheap) or DHL (fast).
"""
    )
    cost_block = "\n".join(f"- {line}" for line in profile.cost_lines)
    text = f"""# Order: {product_name}

{board_width_mm:g} x {board_height_mm:g} mm, {layers} layer(s), verified fab packet
({profile.id}). Files in this folder: `{profile.zip_name}`, `{profile.bom_name}`{
    f", `{profile.cpl_name}`" if assembly else ""}.

## Walkthrough (JLCPCB economy{" PCBA" if assembly else ""})

1. cart.jlcpcb.com/quote -> **Add gerber file** -> drop `{profile.zip_name}`
   (layers + size auto-detect; verify {layers} layers, {board_width_mm:g} x {board_height_mm:g} mm).
2. Options: Qty **5**, {profile.standard_thickness_mm:g} mm, HASL, green — leave the rest default.
{assembly_steps}
## Cost + turnaround

{cost_block}
{cost_line}
Order status: JLCPCB emails + your account page (no API in v1).
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fab_ready(warnings: list[dict], gerber_source: str) -> bool:
    """Contract: fab-ready = zero ``error``-severity warnings AND the gerbers
    came from kicad-cli."""
    if gerber_source != "kicad-cli":
        return False
    return not any(w.get("severity") == "error" for w in warnings)


def kicad_project_json(profile: FabProfile) -> str:
    """A `.kicad_pro` carrying this fab's design rules.

    Why this exists (measured 2026-08-10): `circuit-json-to-kicad` emits a
    `.kicad_pcb` with no project file, so `kicad-cli pcb drc` falls back to
    KiCad's stock defaults — min track 0.2mm, netclass clearance 0.2mm — and
    judges the board against rules no fab uses. On a correct skeleton board
    that produced **207 findings, 124 of them `track_width`** on perfectly
    legal 0.15mm traces. Writing this file beside the board before DRC drops
    the same board to **50 findings**, and what survives is real: thin via
    annular rings that our own stage-4 gate independently flags.

    Without it the second-substrate check is noise, and a noisy gate is one
    everybody learns to ignore.
    """
    slack = profile.drc_tolerance_mm
    rules = {
        "min_clearance": round(profile.min_clearance_mm - slack, 4),
        "min_connection": 0.0,
        "min_copper_edge_clearance": round(profile.min_edge_clearance_mm - slack, 4),
        "min_hole_clearance": 0.2,
        "min_hole_to_hole": 0.2,
        "min_silk_clearance": 0.0,
        "min_text_height": 0.8,
        "min_text_thickness": 0.08,
        "min_through_hole_diameter": profile.min_via_drill_mm,
        "min_track_width": round(profile.min_trace_mm - slack, 4),
        "min_via_annular_width": profile.min_via_annular_mm,
        "min_via_diameter": profile.min_via_diameter_mm,
    }
    netclass = {
        "name": "Default",
        "clearance": round(profile.min_clearance_mm - slack, 4),
        "track_width": profile.warn_trace_mm,
        "via_diameter": 0.6,
        "via_drill": 0.3,
        "microvia_diameter": 0.3,
        "microvia_drill": 0.1,
    }
    payload = {
        "board": {"design_settings": {"rules": rules}},
        "net_settings": {"classes": [netclass]},
        "meta": {"filename": "board.kicad_pro", "version": 3},
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def write_kicad_project(board_path: Path, profile: FabProfile) -> Path:
    """Write `<board stem>.kicad_pro` beside a `.kicad_pcb`. kicad-cli picks the
    project up by basename, which is how the rules above reach DRC."""
    path = board_path.with_suffix(".kicad_pro")
    path.write_text(kicad_project_json(profile), encoding="utf-8")
    return path
