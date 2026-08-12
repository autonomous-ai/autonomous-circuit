"""Planner-derived protected public starter contract."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "scripts" / "packages")
)

from circuitpy import spec
from circuitpy.errors import ProjectShapeError
from circuitlib.blocks import BLOCKS
from circuitlib.starter import (
    DESIGN_PROFILE,
    EXPECTED_BLOCKS,
    protected_usb_indicator_starter,
)


def test_starter_is_resolved_from_the_protected_planner_and_registry() -> None:
    starter = protected_usb_indicator_starter()
    assert starter.plan.buildable
    assert starter.plan.unmet == ()
    assert starter.plan.unavailable == ()
    assert starter.block_ids == EXPECTED_BLOCKS
    assert starter.placement == {
        "width_mm": 46.9,
        "height_mm": 36.8,
        "placements": {
            "usb-c-data": (0.0, -11.3),
            "usb-power-entry": (-9.16, 10.47),
            "status-led": (-1.86, 9.69),
            "ldo-3v3": (5.81, 10.75),
        },
        "holes": [
            {"name": "H1", "diameter_mm": 3.2, "pcbX": -20.25, "pcbY": -15.2},
            {"name": "H2", "diameter_mm": 3.2, "pcbX": 20.25, "pcbY": 15.2},
        ],
        "warnings": [],
    }
    assert BLOCKS["usb-power-entry"].attachment("protected_output").selector == ".U7 > .OUT"
    assert BLOCKS["ldo-3v3"].attachment("regulated_output").selector == ".U2 > .VOUT"
    assert set(starter.parts) == {
        "C1", "C2", "C3", "C24", "J1", "LED1", "R1", "R2", "R20",
        "R3", "R4", "R31", "R32", "U1", "U2", "U7",
    }
    assert starter.parts["LED1"]["lcsc"] == "C2297"
    assert starter.parts["R20"]["lcsc"] == "C11702"
    assert starter.parts["R3"]["lcsc"] == "C25100"
    assert starter.parts["R4"]["lcsc"] == "C25100"
    assert starter.parts["U2"] == {
        "lcsc": "C500795",
        "basic": False,
        "description": "AP7361C-33E-13 SOT-223",
        "block": "ldo-3v3",
    }
    assert starter.parts["C2"]["lcsc"] == "C19702"
    assert starter.parts["C3"]["lcsc"] == "C19702"


def test_starter_product_is_the_machine_profile_not_advice() -> None:
    product = protected_usb_indicator_starter().product
    assert product["designProfile"] == DESIGN_PROFILE
    assert product["designProfileSourceSha256"] == hashlib.sha256(
        protected_usb_indicator_starter().board_source.encode("utf-8")
    ).hexdigest()
    assert product["schematicPolicy"] == {
        "placement": "explicit",
        "flow": "left-to-right",
    }
    assert product["layout"]["boardSizeMm"] == [46.9, 36.8]
    classes = {rule["name"]: rule for rule in product["layout"]["netClasses"]}
    assert classes["POWER"]["nets"] == ["V5", "V3_3"]
    assert classes["USB_ATTACH_POWER"]["nets"] == ["VBUS_RAW"]
    assert classes["CONTROL_SIGNAL"]["nets"] == ["USB_POWER_FAULT"]
    assert "minViaOuterDiameterMm" not in classes["CONTROL_SIGNAL"]
    assert product["powerBudget"]["usb"]["fixedOperationalLoadMa"] == 13.0
    assert product["powerBudget"]["regulators"] == [{
        "profile": "ap7361c-33e-c500795-v1",
        "ref": "U2",
        "inputNet": "V5",
        "outputNet": "V3_3",
        "inputCapRef": "C2",
        "outputCapRef": "C3",
        "maxAmbientC": 60.0,
    }]
    limiter = product["powerBudget"]["usb"]["currentLimiter"]
    assert limiter["ref"] == "U7"
    assert limiter["settingResistor"]["ref"] == "R31"


def test_starter_source_owns_one_protected_tree_and_signal_sized_defaults() -> None:
    source = protected_usb_indicator_starter().board_source
    for required in (
        'externalRawPowerTrunkPort="IN"',
        'externalPowerTrunkPort="OUT"',
        'externalInputPowerTrunkPort="VIN"',
        'externalRailAttachmentPort="R"',
        '<GndPlanes layers={["top", "bottom"]}',
        'source=".U7 > .OUT" net="V5"',
        'externalPowerTrunkPort="VOUT"',
        'source=".U2 > .VOUT" net="V3_3"',
        'from=".N15 > .pin1" to=".N21 > .pin1"',
        'minTraceToPadEdgeClearance="0.15mm"',
        'minViaEdgeToPadEdgeClearance="0.15mm"',
        'minViaPadDiameter="0.6mm" minViaHoleDiameter="0.3mm"',
        'viaOuterDiameterMm={0.8} viaHoleDiameterMm={0.5}',
        'schX={-12} schY={0}',
        'schX={12} schY={0}',
    ):
        assert required in source
    # 0.8/0.5 belongs to authored power paths; it is not a board/phase default.
    assert 'minViaPadDiameter="0.8mm"' not in source
    assert '<autoroutingphase phaseIndex={6} minVia' not in source


def test_starter_refuses_planner_drift_instead_of_emitting_a_partial_board() -> None:
    clean = protected_usb_indicator_starter()
    changed = replace(clean.plan, block_ids=clean.plan.block_ids[:-1])
    with patch("circuitlib.starter.board_plan", return_value=changed):
        with pytest.raises(ValueError, match="planner closure changed"):
            protected_usb_indicator_starter()


def test_starter_names_are_required() -> None:
    with pytest.raises(ValueError, match="name must be non-empty"):
        protected_usb_indicator_starter(name=" ")
    with pytest.raises(ValueError, match="description must be non-empty"):
        protected_usb_indicator_starter(description="")


def test_static_project_template_cannot_drift_from_the_public_generator() -> None:
    starter = protected_usb_indicator_starter()
    skill_root = Path(__file__).resolve().parents[1]
    template = skill_root / "templates" / "project_skeleton"
    assert (template / "boards" / "main.tsx").read_text() == starter.board_source
    assert json.loads((template / "product.json").read_text()) == starter.product
    assert json.loads((template / "parts.json").read_text()) == starter.parts


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("<UsbPowerEntry ", "<MissingUsbPowerEntry "),
        ("<GndPlanes layers=", "<MissingGndPlanes layers="),
        ('<PowerTrunk name="V5_MAIN"', '<MissingPowerTrunk name="V5_MAIN"'),
        ("schX={-12} schY={0}", "schX={-11} schY={0}"),
    ],
    ids=("protector", "ground-pour", "power-tree", "schematic-anchor"),
)
def test_profile_source_identity_refuses_removed_real_board_behavior(
    tmp_path: Path, old: str, new: str
) -> None:
    starter = protected_usb_indicator_starter()
    assert starter.product["designProfileSourceSha256"] == (
        spec.PROTECTED_USB_INDICATOR_SOURCE_SHA256
    )
    (tmp_path / "product.json").write_text(json.dumps(starter.product))
    (tmp_path / "parts.json").write_text(json.dumps(starter.parts))
    product = spec.load_product(tmp_path)
    assert old in starter.board_source
    modified = starter.board_source.replace(old, new, 1)
    with pytest.raises(ProjectShapeError, match="board source differs"):
        spec.validate_profile_source_identity(
            product, hashlib.sha256(modified.encode("utf-8")).hexdigest()
        )
