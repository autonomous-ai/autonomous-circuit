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
import math
import os
import re
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
    # JLC's documented CPL order (the KiCad help article): Rotation before
    # Layer. The exporter emits Layer first; write_cpl_csv reorders.
    cpl_columns: tuple[str, ...] = ("Designator", "Mid X", "Mid Y", "Rotation", "Layer")
    # DFM limits (mm) — conservative cheap-tier encodings of JLC's caps table.
    # Two bands everywhere: block at the fab's real floor, warn at what we
    # would rather see. A blocking gate set to a preference flags legal boards,
    # and a gate that cries wolf is one everyone learns to skip.
    min_trace_mm: float = 0.10           # JLC 1oz floor
    warn_trace_mm: float = 0.15          # our cheap-tier preference
    # Power nets get their own floor (EE review 2026-08-15, ledger #31): 5V
    # and 3V3 shipped at 0.2mm, same as a button signal — legal by the
    # current maths on that board, wrong as a habit. The current-driven check
    # (`netclass_trace_width`) still blocks a genuine overload; this is the
    # habit line, and it is warn-band on purpose — an EE moves it here, in
    # one place, without touching a check.
    warn_power_trace_mm: float = 0.5
    #: The clearance a copper pour declares about itself in the exported KiCad
    #: project, and therefore the number KiCad's own DRC holds the *filled*
    #: zone to. It is written by the tscircuit converter, not by us, and it is
    #: stricter than this profile's copper floor — which is exactly why it has
    #: to be written down here: a pour that satisfies our 0.1mm and not this
    #: 0.15mm passes our gate and fails the packet the reviewer opens.
    kicad_zone_clearance_mm: float = 0.15
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
    # A via is a hole too, and JLC lists it separately: "Via hole to Track
    # 0.2mm". Added 2026-08-11 after KiCad reported two hole-clearance
    # violations at 0.132mm and 0.148mm on an rp2040-core board that our own
    # stage-4 gate called clean — it only looked at component holes. That
    # blindness has a second cost: the router escalation reads circuit.json,
    # so a defect only KiCad can see arrives too late to retry the route.
    min_via_to_copper_mm: float = 0.20
    # JLC holds 0.2mm copper-to-outline on a routed edge; 0.3mm is the
    # conservative figure for V-cut and bevelled edges. Blocking at 0.3 made a
    # ground pour impossible — the pour fills to exactly 0.2mm and cannot be
    # told otherwise (minBoardEdgeClearance is silently ignored, verified
    # 2026-08-10), so every board with a plane failed on a legal geometry.
    min_edge_clearance_mm: float = 0.2   # routed-edge floor
    warn_edge_clearance_mm: float = 0.3  # what we would rather see
    min_board_mm: float = 3.0            # min board dimension
    standard_thickness_mm: float = 1.6   # JLC standard (toolchain default is 1.4)
    # Silkscreen and solder mask (jlcpcb.com/capabilities, read 2026-08-11).
    # These are not advice: below them JLC prints broken ink, or drops the
    # layer, or lets a mask web burn off and bridge two pads. They are applied
    # to the converted board by `kicad_normalize.normalize_for_fab` before the
    # gerbers are plotted, because `circuit-json-to-kicad` emits silkscreen
    # text at 0.2-0.67mm — under the floor on every board it has ever made.
    min_silk_line_mm: float = 0.15       # thinnest ink JLC will print
    min_silk_text_mm: float = 1.0        # shortest legible character height
    min_mask_sliver_mm: float = 0.20     # thinnest mask web that survives reflow
    # Footprint-IoU bands (supplier_footprint_mismatch_warning): correct 0402
    # parts score ~0.73-0.77, so the blocking band sits well below that.
    iou_error_below: float = 0.5
    iou_warning_below: float = 0.65
    iou_info_below: float = 0.85
    # Fab price tiers (EE review 2026-08-15, ledger #30). The sample subsidy
    # is a price cliff, not a curve — measured 2026-08-15: 5 boards at
    # <=100x100mm 2-layer cost $2; terminal-keyboard's 112x90 quoted $8.90,
    # 4.5x for 12mm. (max_w_mm, max_h_mm, usd_per_5, label); a board fits a
    # tier in either orientation. The stage-4 check names the money when a
    # board misses every tier.
    price_tiers: tuple[tuple[float, float, float, str], ...] = (
        (100.0, 100.0, 2.0, "sample subsidy"),
    )
    price_tier_evidence: str = (
        "112x90mm quoted $8.90/5pcs against $2.00 inside the tier "
        "(measured 2026-08-15)"
    )
    # Cost model lines for ORDER.md (verified ranges, 2026-08).
    cost_lines: tuple[str, ...] = (
        "PCB only, 5x 2-layer <=100x100mm: $2 + shipping (~$4-20 all-in, "
        "24-48h fab); over 100x100 the subsidy is gone — 112x90 quoted "
        "$8.90 (2026-08-15).",
        "Assembled, 5x ESP32-class: ~$75-110 all-in, ~1-2 weeks to door.",
        "Extended parts add a ~$3/line loading fee; Basic parts avoid it.",
    )
    # Which of the standalone `verifylib` findings block `fab.ready`. Kept on
    # the profile, not in the checks, so an EE moves the line here in one
    # place — see VERIFY_BLOCKING_KINDS below for the reasoning per kind.
    verify_blocking: frozenset[str] = frozenset()
    verify_escalated: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# The verify policy: what a standalone check does to `fab.ready`.
# ---------------------------------------------------------------------------
#
# `verifylib` (packages/verify) sees things no other gate can, and each finding
# has to be assigned a consequence. The rule is the one the north star sets:
# **block only what makes the delivered board unusable or the order refused.**
# Everything else advises, with its measurement attached.
#
# Three states, and the default matters most:
#
#   blocking  — the check's own `error` is honoured and stops `fab.ready`
#   escalated — this fab raises the check's `warning` to `error`, with a reason
#   (default) — capped at `warning`, whatever the check said
#
# A kind nobody has classified is capped. That is deliberate: adding a new
# check must never silently move the bar, because the bar improving for a
# reason nobody chose is indistinguishable from the bar breaking.

#: Findings whose `error` severity is honoured. Each one means the fab refuses
#: the order, or the board arrives and cannot do its job.
VERIFY_BLOCKING_KINDS: frozenset[str] = frozenset({
    # The packet does not describe the board. Everything downstream of this is
    # the fab building something we did not design.
    "gerber_missing_layer",
    "gerber_unreadable",
    "gerber_outline_empty",
    "gerber_outline_mismatch",
    "gerber_scale_mismatch",
    "gerber_drill_empty",
    "gerber_drill_missing",
    "gerber_drill_size_mismatch",
    "gerber_drill_plating_mismatch",
    "gerber_pad_missing",
    "gerber_pad_masked_over",
    "gerber_trace_width",
    # The assembly line refuses or silently skips the part.
    "dfa_bottom_side",
    "dfa_board_size",
    "dfa_pin_pitch",
    "dfa_off_board",
    "dfa_courtyard_overlap",
    # The board powers up and destroys itself.
    "dc_led_current",
    "dc_rail_overload",
    "thermal_resistor_power",
    "netclass_trace_width",
    # The router refuses to route the board at all. Over the crystal-net
    # ceiling, tscircuit skips autorouting for the WHOLE board and every trace
    # comes back missing, so the packet describes a board with no copper on it.
    # There is no version of this that ships.
    "crystal_net_too_long",
})

#: Findings this fab raises from `warning` to `error`. Each needs a reason
#: sharper than "it would be nice", because each one stops a board shipping.
VERIFY_ESCALATED_KINDS: frozenset[str] = frozenset({
    # Measured 2026-08-11: 100% of silkscreen strokes on all three example
    # boards are under JLCPCB's 0.15mm floor, 1145 of them at 0.033mm. This is
    # not a few thin labels — the whole layer will print broken or be dropped,
    # and a board where no part carries a reference designator cannot be
    # reviewed, reworked or debugged. It is also a single-place fix in the
    # exporter, which is the definition of a shift-left bug.
    "gerber_silk_line_width",
    # A debug interface that reaches no connector or test point cannot be used
    # once the board is assembled, so the board can never run the firmware it
    # was designed for. "Arrives and is useless" is exactly the bar.
    "review_debug_unreachable",
    # Ink on a solderable surface stops the joint wetting; the fab's own
    # remedy is to clip the silk unasked, which costs the reference designator
    # instead. Same family, same consequence and the same single-place fix as
    # `gerber_silk_line_width` above.
    #
    # Escalated only once that fix existed. Measured 2026-08-13: every board
    # this pipeline had produced carried this (13 strokes on one, 70 on
    # another) and the board source could not clear it — those designators come
    # from the converter, not the TSX. Blocking then would have made every
    # board un-orderable over something no agent could fix. The gerber plot now
    # passes `--subtract-soldermask`, boards come out with zero, and this
    # escalation is the guard that keeps them that way.
    "gerber_silk_over_pad",
})

#: Deliberately NOT escalated, with the reasoning recorded so the next person
#: does not have to re-derive it:
#:
#: * `crystal_net_routed_long` — the copper the router laid exceeds the crystal
#:   ceiling even though the parts are placed inside it (a detour, or vias at a
#:   full board thickness each). Measured 2026-08-12: **all four** boards in
#:   this repo carry one, 10.09mm to 20.93mm against a 10mm line, and every
#:   figure matches tscircuit's own `pcb_trace_too_long_warning` exactly. The
#:   router routed them anyway and the fab will build them. Blocking here would
#:   make every board this repo has ever produced un-orderable over an
#:   oscillator that is marginal, not broken. Contrast `crystal_net_too_long`,
#:   which blocks because the router refuses to lay any copper at all.
#: * `crystal_net_tight` — a connection with under 1mm of slack routes today.
#:   It is one nudge from taking the whole board's routing down, which is why
#:   it is reported at all rather than passed in silence, but a board that
#:   routes is a board that ships. Blocking here would refuse an orderable
#:   board over a risk that has not happened.
#: * `crystal_net_routed_tight` — the same margin, measured on the copper
#:   instead of on the placement. Added 2026-08-17 after weather-badge-9 shipped
#:   with 0.54mm of slack on routed copper while its placement figure showed a
#:   quiet 1.66mm: the margin warning existed but only watched the lower bound,
#:   so the tighter of the two measurements was the silent one. Advisory for the
#:   same reason as `crystal_net_tight` — the board routes.
#:
#: * `gerber_mask_sliver` — escalated on 2026-08-11 and **retracted the same
#:   day on measurement**. All ten sub-0.2mm mask webs on harness-puck sit
#:   inside a single part's own land pattern: 0.114mm and 0.157mm within the
#:   USB-C receptacle's footprint, and 0.1985mm within each of eight 0402
#:   capacitors — which is simply what a 0402 land pattern is. Those dams are
#:   specified by the package, JLCPCB builds them daily, and blocking on them
#:   would have made every board this tool will ever produce permanently
#:   un-orderable. The check is now scoped to webs between *different* parts,
#:   where nobody qualified the geometry; that version fires on none of the
#:   three boards, so there is nothing to escalate yet.
#: * `thermal_regulator` at 96 degC junction — hot, and inside the part's own
#:   125 degC rating with 29 degC to spare. Blocking a part operating within
#:   spec would be a gate set to a preference.
#: * `netclass_trace_width` at 649mA on copper good for 604mA — 7% over an
#:   IPC-2221 figure that already ignores the adjacent plane and is the
#:   conservative direction to be wrong in. It means an 11 degC rise instead of
#:   10. The check still blocks below 70% of required capacity, which is a real
#:   failure rather than a rounding one.
#: * `dfa_edge_clearance` in the 1.0-2.5mm band — JLCPCB assembles these; the
#:   part is placed, not refused. Below 1.0mm it is in the conveyor rail and
#:   the check raises its own error.
#: * `review_decoupling_missing` — the board usually works. Real, and worth a
#:   human's attention, but not worth refusing to let anyone order the board.
#: * `netclass_pair_coupling` (EE review 2026-08-15, finding 3) — report-only
#:   by design. Stage 0c (`circuitpy.diffpair`) now *routes* the pair where it
#:   can, but it refuses rather than regress, and on a congested board there is
#:   sometimes no corridor a pair fits through. Blocking here would make those
#:   boards permanently un-orderable over a defect the pipeline has already
#:   tried to fix and reported honestly.
#: * `netclass_pair_reference` (the same finding's third clause) — a pair with
#:   no ground under it has undefined impedance, and all three example boards
#:   measure **0%**. It is a warning and not an error because the fix is a
#:   ground pour on the board template, which is a design change nobody can
#:   make from inside a repair pass; escalating it would block every board the
#:   tool has ever produced over something no check can repair.

_JLCPCB = FabProfile(
    id="jlcpcb",
    verify_blocking=VERIFY_BLOCKING_KINDS,
    verify_escalated=VERIFY_ESCALATED_KINDS,
)

PROFILES: dict[str, FabProfile] = {"jlcpcb": _JLCPCB}


def apply_verify_policy(findings: list[dict], profile: FabProfile) -> list[dict]:
    """Re-grade standalone findings against this fab's policy.

    The checks say what they measured; the fab profile says what it costs.
    Keeping those apart is what lets an EE move the line without touching a
    check, and lets a check be reused by a fab with different rules.
    """
    out: list[dict] = []
    for raw in findings:
        item = dict(raw)
        kind = str(item.get("kind") or "")
        severity = str(item.get("severity") or "info")
        if kind in profile.verify_escalated:
            item["severity"] = "error"
        elif kind in profile.verify_blocking:
            pass  # the check's own severity stands, error included
        elif severity == "error":
            item["severity"] = "warning"
        out.append(item)
    return out


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
    """Fold the ``parts.json`` lock into BOM rows.

    Match order: the part id equal to the designator (case-insensitive), then
    the designator listed in an entry's own ``refdes``.

    The second half is what makes the lock reach a real board. parts-book's ids
    are readable slugs — ``ams1117-3.3``, ``r-4.7k-0402`` — and it records the
    designators that use them in ``refdes: ["U2"]``. Matching on the id alone
    therefore matched **nothing** on the only board that pins its parts:
    hydrate-coaster locks 19 parts and not one of them is called "U2". Between
    that and a loader that could not read the file's shape at all
    (``spec.load_parts``), the lock has never once reached a BOM row.

    A designator claimed by two entries is left unmatched rather than given to
    whichever sorted first: two locks for one part is a parts-book question,
    and picking one silently is how a board gets ordered with the wrong number.
    """
    by_id = {part_id.lower(): (part_id, entry) for part_id, entry in parts.items()}
    by_refdes: dict[str, tuple[str, dict] | None] = {}
    for part_id, entry in parts.items():
        listed = entry.get("refdes")
        for refdes in listed if isinstance(listed, list) else []:
            key = str(refdes).strip().lower()
            if not key:
                continue
            # None marks a designator two entries both claim.
            by_refdes[key] = None if key in by_refdes else (part_id, entry)
    merged: list[dict] = []
    for row in rows:
        out = dict(row)
        key = row["designator"].lower()
        hit = by_id.get(key) or by_refdes.get(key)
        if hit is not None:
            part_id, entry = hit
            out["lock_id"] = part_id
            out["lock"] = entry
            if not out["lcsc"]:
                out["lcsc"] = str(entry.get("lcsc") or "").strip()
        merged.append(out)
    return merged


def group_bom_rows(rows: list[dict]) -> list[dict]:
    """One BOM line per *part*, designators grouped — JLC's own sample shape
    (EE review 2026-08-15, ledger #32: ours emitted one row per placement, so
    50 identical switches arrived as 50 lines).

    Identity is (comment, value, footprint, lcsc). A row with no identity at
    all (no part number, no comment, no footprint) stays its own line — two
    unknowns are not the same part. Designators keep natural order (SW9 before
    SW10); groups keep first-appearance order. Each group carries ``qty`` and
    the first row's lock."""
    def _natural(designator: str) -> tuple:
        return tuple(
            int(token) if token.isdigit() else token.lower()
            for token in re.findall(r"\d+|\D+", designator)
        )

    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for index, row in enumerate(rows):
        comment = row.get("comment") or row.get("value") or ""
        identity = (comment, row.get("footprint") or "", row.get("lcsc") or "")
        key = identity if any(identity) else ("", "", "", index)
        if key not in groups:
            groups[key] = dict(row, designators=[row["designator"]], qty=1)
            order.append(key)
        else:
            groups[key]["designators"].append(row["designator"])
            groups[key]["qty"] += 1
    out: list[dict] = []
    for key in order:
        group = groups[key]
        group["designators"] = sorted(group["designators"], key=_natural)
        group["designator"] = ",".join(group["designators"])
        out.append(group)
    return out


def bom_summary(rows: list[dict]) -> dict[str, object]:
    """The sidecar's ``bom`` block: lines, orderable, basicParts, and the
    best-effort cost estimate from locked unit prices (omitted when no row
    carries a price — never fabricate a zero).

    ``lines``/``orderable``/``basicParts`` count *grouped* BOM lines — what
    JLC's parts-match table will actually show — while the cost estimate sums
    per placement (fifty switches cost fifty times one switch)."""
    grouped = group_bom_rows(rows)
    lines = len(grouped)
    orderable = sum(1 for row in grouped if row.get("lcsc"))
    basic = sum(1 for row in grouped if (row.get("lock") or {}).get("basic") is True)
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



def catalog_root() -> Path | None:
    """Where the JLCPCB catalog mirror actually is, or None.

    This used to be one hard-coded ``parents[4]`` hop, and it was wrong
    everywhere it mattered. In the repo, ``packages/circuitpy/src/circuitpy/``
    is four levels below the root; in the runtime a build actually uses,
    ``skills/circuitcode/scripts/packages/circuitpy/`` is four levels below
    *skills*, so the same expression pointed at ``skills/packages/...``, which
    has never existed. The catalog came back empty, the Footprint column went
    out blank on every BOM of every board, and nothing anywhere said so —
    ledger #42's class again: a path counted in ``parents`` is a path that
    breaks the moment the file is vendored somewhere else.

    So: look for the directory instead of computing it, and let the caller say
    when it is missing rather than shipping a blank column as if it were an
    answer.
    """
    override = os.environ.get("CIRCUIT_PARTS_CATALOG")
    candidates = [Path(override)] if override else []
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "packages" / "parts-catalog" / "catalog")
        candidates.append(parent / "parts_catalog" / "catalog")
    for candidate in candidates:
        try:
            if candidate.is_dir() and any(candidate.glob("*.json")):
                return candidate
        except OSError:
            continue
    return None


def _catalog_packages() -> dict[str, str]:
    """LCSC number -> package string, from the local JLCPCB catalog mirror.

    Authoritative, unlike inferring a package from copper: the first attempt
    measured the placed footprint and read every 0402 as an 0603, because land
    patterns are larger than the bodies they name.
    """
    root = catalog_root()
    out: dict[str, str] = {}
    if root is None:
        return out
    try:
        files = sorted(root.glob("*.json"))
    except OSError:
        return out
    for file in files:
        try:
            data = json.loads(file.read_text())
        except (OSError, ValueError):
            continue
        rows = data if isinstance(data, list) else data.get("parts") or data.get("items") or []
        for row in rows:
            if isinstance(row, dict) and row.get("lcsc") and row.get("package"):
                out.setdefault(str(row["lcsc"]).strip(), str(row["package"]).strip())
    return out


def describe_components(circuit_json: "Sequence[dict]") -> dict[str, dict]:
    """Per-refdes ``{comment}`` recovered from the circuit JSON.

    The upstream exporter ships `Comment` and `Footprint` **empty** on every
    BOM line (measured 2026-08-15 on all three examples). JLC's parts-match
    table uses those columns to sanity-check the part number against what the
    designer believed they were buying, so an LCSC number with nothing beside
    it removes the fab's only cross-check — and the reviewer's. The first human
    EE review asked for JLC's sample format; format without content is half the
    ask.

    Comment comes from the design itself: `display_resistance`,
    `display_capacitance`, `manufacturer_part_number`, crystal frequency.

    **Package deliberately does not come from geometry.** The first attempt
    measured the placed footprint's copper and quantised it, which read every
    0402 as an 0603 — land patterns are larger than the bodies they name. A
    confidently wrong package is worse than a blank one, because JLC's matcher
    would flag a mismatch that does not exist. Package is looked up by LCSC
    number in the catalog mirror instead, and stays empty when unknown.
    """
    sources = {
        e["source_component_id"]: e
        for e in circuit_json
        if isinstance(e, dict) and e.get("type") == "source_component"
    }
    out: dict[str, dict] = {}
    for src in sources.values():
        name = src.get("name")
        if not name:
            continue
        comment = (
            src.get("display_resistance")
            or src.get("display_capacitance")
            or src.get("display_inductance")
            or src.get("manufacturer_part_number")
            or ""
        )
        if not comment and src.get("frequency"):
            comment = f"{float(src['frequency']) / 1e6:g}MHz"
        if not comment:
            comment = str(src.get("ftype") or "").replace("simple_", "").replace("_", " ")
        out[name] = {
            "comment": str(comment),
            "placeable": _is_placeable(src),
        }
    return out


#: Things that appear in the design as components but cannot be bought or
#: placed. A test point is bare copper with a name; it has no body, no
#: package, and no LCSC number, and handing it to JLC means three red lines in
#: the parts-match table that the user has to work out how to dismiss.
_UNPLACEABLE_FTYPES = frozenset({"simple_test_point", "simple_fiducial"})


def _is_placeable(source_component: dict) -> bool:
    """Whether an assembler could pick this part and put it down.

    Deliberately narrow: only the shapes that are *definitionally* bare copper
    are excluded. "It has no part number" is not the test — a real part
    somebody has not sourced yet still belongs on the BOM, showing red, so
    they go and source it. Dropping those would hide the shortfall instead of
    reporting it, which is the failure mode this whole packet exists to avoid.
    """
    ftype = str(source_component.get("ftype") or "")
    if ftype not in _UNPLACEABLE_FTYPES:
        return True
    supplier = source_component.get("supplier_part_numbers") or {}
    return bool(supplier) or bool(source_component.get("manufacturer_part_number"))


def write_bom_csv(
    rows: list[dict],
    path: Path,
    profile: FabProfile,
    described: dict[str, dict] | None = None,
) -> Path:
    """The shipping BOM, in JLC's documented sample shape (ledger #32):
    ``Comment,Designator,Footprint,LCSC Part #``, one line per part with
    designators grouped (``SW10,SW11,…``) — the format the article at
    jlcpcb.com/help/article/how-to-generate-the-bom-and-centroid-file-from-kicad
    tells KiCad users to hand-rename their export into."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(profile.bom_columns)
        described = described or {}
        packages = _catalog_packages()
        # Bare copper never reaches the fab's parts-match table (see
        # `_is_placeable`). Dropped before grouping, so a group is never split
        # across the boundary.
        rows = [
            row for row in rows
            if described.get(row["designator"], {}).get("placeable", True)
        ]
        for row in group_bom_rows(rows):
            # A grouped row's designators are all the same part, so the first
            # one describes the line.
            first = row["designator"].split(",")[0].strip()
            extra = described.get(first, {})
            comment = row.get("comment") or row.get("value") or extra.get("comment") or ""
            # The lock first, the catalog mirror second. parts-book records the
            # package it chose the part *for* (`package`, "package/footprint
            # class as documented by the block"), and that is the board's own
            # decision; the mirror is a lookup that happens to carry 464 parts
            # and none of the seven the shipped boards were missing. Measured
            # 2026-08-16: 9 of 18 BOM lines on harness-puck had a blank
            # Footprint, and JLC's parts-match table uses that column as the
            # one cross-check between the number you typed and the part you
            # meant.
            lock = row.get("lock") or {}
            footprint = (
                row.get("footprint")
                or str(lock.get("package") or "").strip()
                or packages.get(row.get("lcsc") or "", "")
            )
            writer.writerow([comment, row["designator"], footprint, row.get("lcsc") or ""])
    return path


def write_cpl_csv(exporter_cpl_text: str, path: Path, profile: FabProfile) -> Path:
    """Re-emit the exporter's pick_and_place.csv through the profile columns
    (JLC's documented order — ``Designator,Mid X,Mid Y,Rotation,Layer``; the
    exporter emits Layer before Rotation, so this reorders as well as
    normalizing quoting)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    reader = csv.DictReader(io.StringIO(exporter_cpl_text))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(profile.cpl_columns)
        for raw in reader:
            writer.writerow([
                _cpl_cell(column, (raw.get(column) or "").strip())
                for column in profile.cpl_columns
            ])
    return path


def _cpl_cell(column: str, value: str) -> str:
    """One CPL field, tidied.

    Rotation only. harness-puck shipped ``202.49999999999994`` for a part
    placed at 202.5 degrees — binary floating point arriving in a file a human
    reads and a machine parses. It places identically either way, but a
    reviewer who sees fourteen decimal places on an angle stops trusting every
    other number in the packet, and they are right to: a number printed to
    more precision than it was measured with is a claim nobody made. Wrapped
    into [0, 360) and printed to 4 decimals, which is finer than any
    pick-and-place resolves.
    """
    if column != "Rotation":
        return value
    try:
        angle = float(value)
    except (TypeError, ValueError):
        return value
    if not math.isfinite(angle):
        return value
    return f"{round(angle % 360.0, 4):g}"


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


def kicad_project_json(
    profile: FabProfile, *, clearance_mm: float | None = None
) -> str:
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

    ``clearance_mm`` overrides the copper-to-copper floor — and only that floor
    — so the same board can be asked a second, stricter question without
    changing what the gate grades it against. The tolerance is not subtracted
    from an override: the point of the margin pass is to see the gaps the
    tolerance hides, so it asks for the number as given.
    """
    slack = profile.drc_tolerance_mm
    copper_floor = (
        round(float(clearance_mm), 4)
        if clearance_mm is not None
        else round(profile.min_clearance_mm - slack, 4)
    )
    rules = {
        "min_clearance": copper_floor,
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
        "clearance": copper_floor,
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


def write_kicad_project(
    board_path: Path, profile: FabProfile, *, clearance_mm: float | None = None
) -> Path:
    """Write `<board stem>.kicad_pro` beside a `.kicad_pcb`. kicad-cli picks the
    project up by basename, which is how the rules above reach DRC.

    ``clearance_mm`` is passed straight to `kicad_project_json`; the caller is
    expected to write the real rules back after a margin pass.
    """
    path = board_path.with_suffix(".kicad_pro")
    path.write_text(
        kicad_project_json(profile, clearance_mm=clearance_mm), encoding="utf-8"
    )
    return path
