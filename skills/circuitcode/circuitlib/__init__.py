"""circuitlib — the domain law circuitcode composes with.

The split, mirroring the donor: **the pipeline (circuitpy) owns mechanics and
hard gates** — run the toolchain, harvest ERC/DRC, export the packet, emit the
sidecar. **circuitlib owns law and craft** — the block registry, the electrical
tables, the safety envelope, the soft board-law warnings, the planner. Rule of
thumb: if a number shapes what the agent *writes*, it lives here; if it gates
what the pipeline *accepts*, it lives there.

Import what you need; never retype a number:

    from circuitlib import tables, safety
    from circuitlib.blocks import BLOCKS, block_for
    from circuitlib.helpers import (
        board_plan, validate_board_law, trace_width_for, decoupling_for,
        usb_power_budget_for_plan, estimate_cost, fab_profile,
    )
"""

from circuitlib import blocks, golden, helpers, safety, starter, tables
from circuitlib.blocks import (
    BLOCKS,
    AttachmentPort,
    Block,
    Part,
    UsbSourceContract,
    block_for,
)
from circuitlib.helpers import (
    BoardPlan,
    board_plan,
    clearance_for,
    decoupling_for,
    estimate_cost,
    fab_profile,
    power_budget,
    trace_width_for,
    usb_power_budget_for_plan,
    validate_board_law,
)
from circuitlib.safety import Verdict, safety_gate
from circuitlib.starter import StarterProject, protected_usb_indicator_starter

__all__ = [
    "tables", "blocks", "safety", "helpers", "golden", "starter",
    "BLOCKS", "AttachmentPort", "Block", "Part", "UsbSourceContract", "block_for",
    "BoardPlan", "board_plan", "validate_board_law", "trace_width_for",
    "clearance_for", "decoupling_for", "power_budget",
    "usb_power_budget_for_plan", "estimate_cost",
    "fab_profile", "safety_gate", "Verdict",
    "StarterProject", "protected_usb_indicator_starter",
]
