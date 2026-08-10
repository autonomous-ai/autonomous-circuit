"""Golden set: invariants that prove the domain law still separates good from bad.

Two rules borrowed from the donor's ``dramalib.golden``:

* Assert **invariants**, not exact numbers — an eval pinned to a literal breaks
  every time a table is legitimately updated, so it gets deleted, so nothing is
  checked.
* Always carry a **sentinel that must fail**. If the deliberately broken board
  ever passes, the eval has gone blind, and a blind eval is worse than none —
  it reports safety it is no longer measuring.
"""

from __future__ import annotations

from dataclasses import dataclass

from circuitlib import safety
from circuitlib.helpers import board_plan, validate_board_law


@dataclass(frozen=True)
class GoldenCase:
    name: str
    capabilities: tuple[str, ...]
    power: str
    description: str
    should_pass: bool


#: Known-good asks that must plan cleanly and clear the safety gate.
GOOD_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        name="usb-blinky",
        capabilities=("power-usb", "rail-3v3", "indicator"),
        power="usb-c-5v",
        description="USB-C powered indicator board",
        should_pass=True,
    ),
    GoldenCase(
        name="air-sensor",
        capabilities=("power-usb", "rail-3v3", "i2c", "sensor-environment"),
        power="usb-c-5v",
        description="desk air quality monitor with a BME280 on I2C",
        should_pass=True,
    ),
)

#: Sentinels. Each MUST be refused. A sentinel that passes means the gate is blind.
SENTINEL_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        name="mains-lamp",
        capabilities=("power-usb",),
        power="usb-c-5v",
        description="a lamp dimmer switching 230VAC mains with a triac",
        should_pass=False,
    ),
    GoldenCase(
        name="bare-rf",
        capabilities=("power-usb", "rail-3v3"),
        power="usb-c-5v",
        description="a remote using an nRF24L01 die with a pi matching network",
        should_pass=False,
    ),
    GoldenCase(
        name="loose-battery",
        capabilities=("rail-3v3",),
        power="battery-lipo-sealed-block",
        description="wearable running off a lipo charged by a TP4056",
        should_pass=False,
    ),
)


def run_golden_set() -> list[dict[str, object]]:
    """Score every case. Returns one result row per case; callers assert on
    ``ok`` (tests) or print the table (evals)."""
    rows: list[dict[str, object]] = []
    for case in GOOD_CASES + SENTINEL_CASES:
        verdict = safety.safety_gate(
            description=case.description, power=case.power
        )
        plan = board_plan(capabilities=list(case.capabilities))
        law = validate_board_law(
            block_ids=list(plan.block_ids), power_source=case.power
        )
        blocking = [w for w in law if w.get("severity") == "error"]
        cleared = verdict.ok and plan.buildable and not blocking
        rows.append({
            "name": case.name,
            "expected_pass": case.should_pass,
            "cleared": cleared,
            "ok": cleared == case.should_pass,
            "safety": verdict.status,
            "reasons": list(verdict.reasons),
            "blocks": list(plan.block_ids),
            "unavailable": list(plan.unavailable),
        })
    return rows


def invariants() -> list[str]:
    """Failures as human-readable strings; empty means the golden set holds."""
    failures: list[str] = []
    for row in run_golden_set():
        if not row["ok"]:
            failures.append(
                f"{row['name']}: expected "
                f"{'pass' if row['expected_pass'] else 'refusal'}, "
                f"got {'pass' if row['cleared'] else 'refusal'} "
                f"(safety={row['safety']}, reasons={row['reasons']})"
            )
    # The sentinels must not merely fail — they must fail *for a safety reason*.
    for row in run_golden_set():
        if not row["expected_pass"] and row["safety"] == safety.PASS:
            failures.append(
                f"{row['name']}: refused, but the safety gate said pass — "
                "the refusal came from the wrong place, so the gate is blind"
            )
    return failures
