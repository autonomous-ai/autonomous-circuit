"""The electrical law: one owner for every number circuitcode uses.

Updating a value here updates every helper and every check that cites it.
**Do not transcribe these numbers into board sources — import the table or
call the helper.** A number that appears as a literal in a board file is a
number nobody can update.

Sources, all recorded in ``docs/circuit-research-2026-08-10.md``:

* DFM limits — jlcpcb.com/capabilities, 2-layer 1oz process (read 2026-08-10),
  tightened where the research doc says "encode as" (we pre-filter more
  conservatively than JLC's own floor; JLC's upload check stays the authority).
* Cost model — JLCPCB economy PCBA fee schedule (read 2026-08-10).
* Footprint-IoU bands — measured: correct 0402 parts score 0.73–0.77 against
  their own supplier footprint, so a high threshold would false-positive.
* Ampacity — IPC-2221 external-layer formula (computed, not tabulated; see
  ``helpers.trace_width_for``).
"""

from __future__ import annotations

# --- Rails -----------------------------------------------------------------
# name -> (nominal V, tolerance fraction, note)
RAILS: dict[str, tuple[float, float, str]] = {
    "V5": (5.0, 0.05, "USB VBUS; 4.75-5.25V per USB 2.0"),
    "V3_3": (3.3, 0.03, "logic rail from ldo-3v3"),
    "VBAT": (3.7, 0.14, "single-cell LiPo, 3.0-4.2V — sealed block only"),
    "GND": (0.0, 0.0, "ground"),
}

#: Anything above this at a board input is out of the envelope (see safety.py).
MAX_DC_INPUT_V = 24.0

# --- JLCPCB 2-layer DFM (blocking floors unless named *_WARN) --------------
MIN_TRACE_WIDTH_MM = 0.127        # JLC floor 0.10; we block below 5mil
WARN_TRACE_WIDTH_MM = 0.15
MIN_TRACE_SPACING_MM = 0.127
# Vias and component through-holes are separate JLC rules. A via is a routing
# feature and is specced far finer than any component hole; checking one against
# the other's floor flags every routed board. Block at JLC's true floor, warn at
# the cheap-tier number we would rather see.
MIN_VIA_DIAMETER_MM = 0.30
WARN_VIA_DIAMETER_MM = 0.45
MIN_VIA_DRILL_MM = 0.15
WARN_VIA_DRILL_MM = 0.30
MIN_VIA_ANNULAR_MM = 0.075        # implied by a 0.3mm pad on a 0.15mm hole
WARN_VIA_ANNULAR_MM = 0.10
MIN_PTH_DRILL_MM = 0.30           # component through-hole
MIN_PTH_ANNULAR_MM = 0.20         # PTH annular ring (JLC spec)
MIN_PAD_TRACE_CLEARANCE_MM = 0.10
MIN_VIA_HOLE_TO_HOLE_MM = 0.20
MIN_PAD_HOLE_TO_HOLE_MM = 0.45
MIN_NPTH_MM = 0.50
MIN_COPPER_TO_EDGE_MM = 0.30
MIN_BOARD_EDGE_MM = 3.0
# --- JLCPCB fab economics (measured 2026-08-15; ledger #30) ----------------
# The sample subsidy is a price cliff, not a curve: 5 boards at <=100x100mm
# 2-layer cost $2; terminal-keyboard's 112x90 quoted $8.90 — 4.5x for 12mm.
# The planner targets the tier unless the design cannot fit.
JLC_SAMPLE_TIER_MM = (100.0, 100.0)
JLC_SAMPLE_TIER_USD = 2.0
BOARD_THICKNESS_MM = 1.6          # set explicitly: the toolchain default is 1.4
BOARD_THICKNESS_RANGE_MM = (0.4, 4.5)
WARN_SILK_LINE_MM = 0.15
WARN_SILK_TEXT_MM = 1.0
DEFAULT_LAYERS = 2
COPPER_OZ = 1

# --- Footprint-vs-supplier-part copper IoU ---------------------------------
# Measured on known-correct parts: ~0.73-0.77. Bands chosen so correct parts
# do not trip the blocking gate.
IOU_ERROR_BELOW = 0.50
IOU_WARN_BELOW = 0.65
IOU_INFO_BELOW = 0.85

# --- Decoupling law --------------------------------------------------------
DECOUPLE_PER_POWER_PIN = "100nF"  # one local cap per IC power pin, closest pad
BULK_PER_RAIL = "10uF"            # one bulk cap per rail entry point
I2C_PULLUP_OHMS = 4700            # 4.7k at 3V3, 100-400kHz, short bus
USB_DP_DM_SERIES_OHMS = 27.4      # RP2040 hardware design guide
USB_CC_PULLDOWN_OHMS = 5100       # USB-C spec 4.5.1.2, Rd sink advertising 5V

# --- JLCPCB economy PCBA cost model ---------------------------------------
PCB_5X_2LAYER_USD = 2.00          # locked promo, <=100x100mm
SHIPPING_SLOW_USD = (1.50, 3.00)
SHIPPING_FAST_USD = (16.00, 25.00)
ASSEMBLY_SETUP_USD = 8.00
STENCIL_USD = 1.50
SMT_JOINT_USD = 0.0017
EXTENDED_PART_FEE_USD = 3.00      # per unique BOM line that is not Basic/preferred
ECONOMY_MAX_ASSEMBLED = 30
#: What to quote a user for the standard prototype run, all-in.
COST_BAND_BARE_5X_USD = (4.0, 20.0)
COST_BAND_ASSEMBLED_5X_USD = (75.0, 110.0)
LEAD_TIME_DAYS = (7, 14)

# --- Loop discipline -------------------------------------------------------
MAX_REPAIR_ITERATIONS = 4         # past this you are guessing at taste, not fixing
BUILD_WALL_CLOCK_S = 300

__all__ = [n for n in dir() if not n.startswith("_")]
