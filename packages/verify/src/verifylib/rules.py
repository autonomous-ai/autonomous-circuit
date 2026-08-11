"""Every number this package checks against, with its source.

Single owner, "never transcribe — import", same discipline as
``circuitlib.tables``. A number that appears in two places will disagree in two
places. Each entry names where it came from and the date it was read, because a
fab capability table is a moving target and a stale floor is worse than none.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# JLCPCB assembly (Economic PCBA tier).
# Read 2026-08-11 from jlcpcb.com/capabilities/pcb-assembly-capabilities.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssemblyRules:
    """What the pick-and-place line needs, as opposed to what the fab needs.

    The fab's rules are about copper; these are about a machine reaching a part
    with a nozzle while a conveyor grips the board edges. A board can be
    perfectly DRC-clean and still be unassemblable, which is why none of our
    four existing detection sources can see any of this.
    """

    #: Component body to board edge. The conveyor rails grip this strip and
    #: depanel routing passes through it.
    body_to_edge_mm: float = 2.5
    #: Below this a part is not merely close to the edge, it is in the rail.
    body_to_edge_hard_mm: float = 1.0
    #: Clearance between adjacent SMD parts for the placement head.
    smd_to_smd_mm: float = 0.3
    #: Finest IC pin pitch the Economic line will place (Standard does 0.35).
    min_pin_pitch_mm: float = 0.4
    #: Smallest board the assembly line accepts.
    min_board_mm: float = 10.0
    #: Economic PCBA places one side only; a bottom-side part is silently
    #: skipped rather than rejected, which is the worst way to fail.
    smt_sides: int = 1
    #: Standard PCBA wants rails + fiducials; Economic does not. Recorded so
    #: the day we move tiers this is a flag flip, not a rediscovery.
    requires_fiducials: bool = False
    rail_width_mm: float = 5.0
    fiducial_diameter_mm: float = 1.0
    fiducial_to_edge_mm: float = 3.85
    tooling_hole_mm: float = 2.0


JLCPCB_ECONOMIC = AssemblyRules()
JLCPCB_STANDARD = AssemblyRules(
    min_pin_pitch_mm=0.35,
    smt_sides=2,
    requires_fiducials=True,
)

ASSEMBLY_TIERS = {"economic": JLCPCB_ECONOMIC, "standard": JLCPCB_STANDARD}


# ---------------------------------------------------------------------------
# JLCPCB fabrication, 2-layer 1oz. Read 2026-08-11 from
# jlcpcb.com/capabilities/pcb-capabilities. These mirror
# ``circuitpy.fab.FabProfile``; they are repeated here because this package
# must be runnable standalone, and any drift between the two is a bug the
# cross-check test in tests/test_rules_agree.py catches.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FabRules:
    min_trace_mm: float = 0.10
    min_clearance_mm: float = 0.10
    min_via_drill_mm: float = 0.15
    min_via_diameter_mm: float = 0.25
    min_pth_drill_mm: float = 0.30
    min_pth_annular_mm: float = 0.20
    min_npth_to_copper_mm: float = 0.20
    min_pth_to_copper_mm: float = 0.28
    min_edge_clearance_mm: float = 0.20
    min_board_mm: float = 3.0
    standard_thickness_mm: float = 1.6
    #: Solder mask. A web of mask thinner than this burns off in the oven and
    #: the two pads it separated become one joint.
    min_mask_sliver_mm: float = 0.20
    min_mask_opening_gap_mm: float = 0.10
    #: Silkscreen. Under these the ink is illegible or JLC drops the layer.
    min_silk_line_mm: float = 0.15
    min_silk_text_mm: float = 1.0
    copper_oz: float = 1.0


JLCPCB_2LAYER = FabRules()


# ---------------------------------------------------------------------------
# IPC-2221B current capacity.
# I = k * dT^0.44 * A^0.725, A in mil^2 — the same constants circuitlib uses.
# ---------------------------------------------------------------------------

IPC2221_K_EXTERNAL = 0.048
IPC2221_K_INTERNAL = 0.024
MM_PER_MIL = 0.0254
#: Thickness of one ounce of copper, in mils.
OZ_THICKNESS_MIL = 1.378
#: Temperature rise we design to. 10 degC is the conventional conservative
#: target; IPC's chart runs to 100.
DEFAULT_DELTA_T_C = 10.0


def ipc2221_width_mm(
    current_a: float,
    *,
    delta_t_c: float = DEFAULT_DELTA_T_C,
    layer: str = "external",
    copper_oz: float = 1.0,
) -> float:
    """Minimum conductor width for a current, by IPC-2221B.

    Deliberately duplicated from ``circuitlib.helpers.trace_width_for`` rather
    than imported: that module lives inside a skill runtime this package must
    not depend on, and the constants are pinned above so the two cannot drift
    silently — ``tests/test_rules.py`` asserts they agree.
    """
    if current_a <= 0:
        return 0.0
    k = IPC2221_K_EXTERNAL if layer == "external" else IPC2221_K_INTERNAL
    area_mil2 = (current_a / (k * delta_t_c**0.44)) ** (1 / 0.725)
    return (area_mil2 / (OZ_THICKNESS_MIL * copper_oz)) * MM_PER_MIL


def ipc2221_current_a(
    width_mm: float,
    *,
    delta_t_c: float = DEFAULT_DELTA_T_C,
    layer: str = "external",
    copper_oz: float = 1.0,
) -> float:
    """The inverse: how much current a given width carries at ``delta_t_c``."""
    if width_mm <= 0:
        return 0.0
    k = IPC2221_K_EXTERNAL if layer == "external" else IPC2221_K_INTERNAL
    area_mil2 = (width_mm / MM_PER_MIL) * OZ_THICKNESS_MIL * copper_oz
    return k * delta_t_c**0.44 * area_mil2**0.725


# ---------------------------------------------------------------------------
# Component tolerances, for corner analysis.
# Standard commodity parts at JLCPCB Basic-library grade.
# ---------------------------------------------------------------------------

#: Fractional tolerance by part class. Ceramic capacitors are the loose one and
#: it gets worse: an X7R MLCC also loses capacitance with applied DC bias, an
#: effect the datasheet tolerance does not include.
TOLERANCE = {
    "resistor": 0.01,     # 1% thick film, the default Basic part
    "capacitor": 0.10,    # X7R/X5R; Y5V is far worse and we do not stock it
    "inductor": 0.20,
    "led_vf": 0.10,       # forward-voltage spread across a reel
    "rail": 0.05,         # a nominal 3.3V LDO output, over line/load/temp
}

#: Ambient extremes a desk object actually sees. Not automotive, not benign.
AMBIENT_MIN_C = 0.0
AMBIENT_NOMINAL_C = 25.0
AMBIENT_MAX_C = 45.0


# ---------------------------------------------------------------------------
# Packages whose zero-rotation convention differs between EDA output and
# JLCPCB's own library. Auto-correction is imperfect, so the placement preview
# is the safety net -- this list turns "eyeball everything" into "eyeball
# these". Seed from the Fabrication-Toolkit / Bouni rotation databases.
# ---------------------------------------------------------------------------

ROTATION_PRONE_HINTS = (
    "sot-23", "sot23", "sot-223", "sot223", "sot-89",
    "soic", "sop-", "sop8", "tssop", "msop", "ssop",
    "to-252", "to252", "dpak", "to-263",
    "usbc", "usb-c", "jst", "pinrow", "header",
    "led", "sod-", "sod123", "sma", "smb",
    "electrolytic", "tantalum", "crystal", "hc-49",
)

#: Refdes prefixes whose parts are polarised — a 180 degree error is fatal
#: rather than cosmetic.
POLARISED_PREFIXES = ("D", "LED", "U", "Q", "J", "Y")
