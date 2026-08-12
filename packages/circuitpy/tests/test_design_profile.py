"""Fail-closed machine profile for the public protected USB starter."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from circuitpy import spec
from circuitpy.errors import ProjectShapeError


def protected_product() -> dict:
    return {
        "name": "protected-starter",
        "description": "generated protected USB indicator",
        "power": "usb-c-5v",
        "envelopeMm": [46.9, 36.8],
        "layers": 2,
        "fab": "jlcpcb",
        "assembly": True,
        "assemblyTier": "standard",
        "designProfile": "protected-usb-indicator-v1",
        "designProfileSourceSha256": spec.PROTECTED_USB_INDICATOR_SOURCE_SHA256,
        "schematicPolicy": {
            "placement": "explicit",
            "flow": "left-to-right",
        },
        "layout": {
            "boardSizeMm": [46.9, 36.8],
            "boardSizeToleranceMm": 0.1,
            "minCopperClearanceMm": 0.15,
            "decoupling": {"maxDistanceMm": 2, "exclude": ["U1"]},
            "groundPlanes": {
                "layers": ["top", "bottom"],
                "maxRoutedLengthMm": 20,
                "maxFanoutLengthMm": 2,
                "stitchingPitchMm": 10,
            },
            "componentSides": [
                {"match": ["LED1", "R20"], "side": "bottom"},
                {"match": "*", "side": "top"},
            ],
            "edgeConnectors": [
                {
                    "ref": "J1",
                    "edge": "bottom",
                    "alignment": "center",
                    "edgeToleranceMm": 2.0,
                    "centerToleranceMm": 0.1,
                }
            ],
            "netClasses": [
                {
                    "name": "POWER",
                    "nets": ["V5", "V3_3"],
                    "minTrunkWidthMm": 0.8,
                    "minNeckdownWidthMm": 0.2,
                    "maxNeckdownLengthMm": 2,
                    "minViaOuterDiameterMm": 0.8,
                    "minViaHoleDiameterMm": 0.5,
                },
                {
                    "name": "USB_ATTACH_POWER",
                    "nets": ["VBUS_RAW"],
                    "minTrunkWidthMm": 0.8,
                    "minNeckdownWidthMm": 0.2,
                    "maxNeckdownLengthMm": 2,
                    "minViaOuterDiameterMm": 0.8,
                    "minViaHoleDiameterMm": 0.5,
                },
                {
                    "name": "CONTROL_SIGNAL",
                    "nets": ["USB_POWER_FAULT"],
                    "minTrunkWidthMm": 0.25,
                    "minNeckdownWidthMm": 0.15,
                    "maxNeckdownLengthMm": 1,
                },
            ],
        },
        "powerBudget": {
            "usb": {
                "rawVbusNet": "VBUS_RAW",
                "protectedVbusNet": "V5",
                "rawAttachCapacitanceMaxUf": 10,
                "sourceCurrentMaxMa": 500,
                "fixedOperationalLoadMa": 13,
                "currentLimiter": {
                    "ref": "U7",
                    "lcsc": "C55266",
                    "inputPin": "IN",
                    "outputPin": "OUT",
                    "settingPin": "ILIM",
                    "settingResistor": {
                        "ref": "R31",
                        "lcsc": "C32297",
                        "resistanceOhms": 59000,
                        "returnNet": "GND",
                    },
                    "minTripMa": 400.6,
                    "maxTripMa": 500,
                },
                "firmwareLimitedLoads": [],
            },
            "regulators": [
                {
                    "profile": "ap7361c-33e-c500795-v1",
                    "ref": "U2",
                    "inputNet": "V5",
                    "outputNet": "V3_3",
                    "inputCapRef": "C2",
                    "outputCapRef": "C3",
                    "maxAmbientC": 60,
                }
            ],
        },
    }


def protected_parts() -> dict:
    return {
        ref: {
            "lcsc": lcsc,
            "basic": basic,
            "description": f"protected profile part {ref}",
            "block": block_id,
        }
        for ref, (lcsc, basic, block_id) in spec.PROTECTED_USB_INDICATOR_PARTS.items()
    }


def load(tmp_path: Path, payload: dict) -> spec.ResolvedProduct:
    (tmp_path / "product.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "parts.json").write_text(
        json.dumps(protected_parts()), encoding="utf-8"
    )
    return spec.load_product(tmp_path)


def test_protected_profile_resolves_exact_contract_and_blocks(tmp_path: Path) -> None:
    product = load(tmp_path, protected_product())
    assert product.design_profile == "protected-usb-indicator-v1"
    assert product.schematic_policy["placement"] == "explicit"
    assert spec.required_blocks_for_product(product) == (
        "ldo-3v3",
        "status-led",
        "usb-c-data",
        "usb-c-power",
        "usb-power-entry",
    )


def test_protected_profile_accepts_only_reviewed_parts_book_enrichment(
    tmp_path: Path,
) -> None:
    enriched = protected_parts()
    enriched["U2"].update(
        {
            "mfr": "Diodes Incorporated",
            "package": "SOT-223",
            "stock": 1494,
            "unit_price_usd": 0.12,
            "stock_checked": "2026-08-12",
            "datasheet_url": "https://www.diodes.com/assets/Datasheets/AP7361C.pdf",
            "source": "parts-book",
            "preferred": True,
            "override": False,
            "footprint_risk": "manufacturer-land-override",
            "swapped_from": "C6186",
        }
    )
    (tmp_path / "product.json").write_text(json.dumps(protected_product()))
    (tmp_path / "parts.json").write_text(json.dumps(enriched))
    assert spec.load_product(tmp_path).design_profile == "protected-usb-indicator-v1"

    enriched["U2"]["unreviewed_field"] = "must fail closed"
    (tmp_path / "parts.json").write_text(json.dumps(enriched))
    with pytest.raises(ProjectShapeError, match="U2 does not match"):
        spec.load_product(tmp_path)


@pytest.mark.parametrize(
    "missing",
    [
        "layout",
        "powerBudget",
        "schematicPolicy",
        "designProfileSourceSha256",
    ],
)
def test_protected_profile_refuses_missing_contracts(
    tmp_path: Path, missing: str
) -> None:
    payload = protected_product()
    del payload[missing]
    with pytest.raises(ProjectShapeError, match=missing):
        load(tmp_path, payload)


def test_unknown_design_profile_is_refused(tmp_path: Path) -> None:
    payload = protected_product()
    payload["designProfile"] = "sounds-safe-v99"
    with pytest.raises(ProjectShapeError, match="designProfile.*must be one of"):
        load(tmp_path, payload)


def test_protected_profile_refuses_missing_or_wrong_parts_lock(tmp_path: Path) -> None:
    (tmp_path / "product.json").write_text(
        json.dumps(protected_product()), encoding="utf-8"
    )
    with pytest.raises(ProjectShapeError, match="exact generated parts.json"):
        spec.load_product(tmp_path)

    parts = protected_parts()
    parts["U2"]["lcsc"] = "C6186"
    (tmp_path / "parts.json").write_text(json.dumps(parts), encoding="utf-8")
    with pytest.raises(ProjectShapeError, match="entry U2"):
        spec.load_product(tmp_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda p: p["layout"].update({"minCopperClearanceMm": 0.14}),
            "minCopperClearanceMm",
        ),
        (
            lambda p: p["layout"]["groundPlanes"].update({"layers": ["top"]}),
            "top/bottom ground",
        ),
        (
            lambda p: p["layout"]["netClasses"][0].update(
                {"minViaHoleDiameterMm": 0.3}
            ),
            "0.8/0.5mm power vias",
        ),
        (
            lambda p: p["layout"]["netClasses"][2].update(
                {"minViaOuterDiameterMm": 0.8, "minViaHoleDiameterMm": 0.5}
            ),
            "must not inflate signal vias",
        ),
        (
            lambda p: p["powerBudget"]["usb"]["currentLimiter"].update(
                {"ref": "U99"}
            ),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p["powerBudget"]["usb"].update(
                {"rawAttachCapacitanceMaxUf": 9.9}
            ),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p["powerBudget"]["usb"].update(
                {"sourceCurrentMaxMa": 499.0}
            ),
            "sourceCurrentMaxMa",
        ),
        (
            lambda p: p["powerBudget"]["usb"]["currentLimiter"].update(
                {"lcsc": "C99999"}
            ),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p["powerBudget"]["usb"]["currentLimiter"][
                "settingResistor"
            ].update({"lcsc": "C99999"}),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p["powerBudget"]["usb"]["currentLimiter"][
                "settingResistor"
            ].update({"resistanceOhms": 60000}),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p["powerBudget"]["usb"]["currentLimiter"].update(
                {"minTripMa": 401.0}
            ),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p["powerBudget"]["usb"]["currentLimiter"].update(
                {"maxTripMa": 499.0}
            ),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p["powerBudget"]["regulators"][0].update(
                {"profile": "ap7361c-lookalike-v1"}
            ),
            "audited regulator profile",
        ),
        (
            lambda p: p["powerBudget"]["regulators"][0].update(
                {"ref": "U99"}
            ),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p["powerBudget"]["usb"].update(
                {
                    "firmwareLimitedLoads": [
                        {
                            "match": "D1",
                            "perDevicePhysicalPeakMa": 1,
                            "aggregateOperationalMaxMa": 1,
                        }
                    ]
                }
            ),
            "exact VBUS_RAW",
        ),
        (
            lambda p: p.update({"envelopeMm": [47.0, 38.7]}),
            "exact 46.9x36.8mm",
        ),
        (
            lambda p: p["layout"]["netClasses"].append(
                deepcopy(p["layout"]["netClasses"][0])
            ),
            "unique names",
        ),
        (
            lambda p: p["layout"]["netClasses"].append(
                {
                    "name": "DECORATIVE",
                    "nets": ["LED_K"],
                    "minTrunkWidthMm": 0.25,
                    "minNeckdownWidthMm": 0.15,
                    "maxNeckdownLengthMm": 1,
                }
            ),
            "requires exactly",
        ),
    ],
)
def test_protected_profile_refuses_weakened_or_wrong_contracts(
    tmp_path: Path, mutate, message: str
) -> None:
    payload = deepcopy(protected_product())
    mutate(payload)
    with pytest.raises(ProjectShapeError, match=message):
        load(tmp_path, payload)


def test_legacy_products_still_omit_the_profile_contracts(tmp_path: Path) -> None:
    payload = {"name": "legacy", "power": "usb-c-5v", "layers": 2}
    product = load(tmp_path, payload)
    assert product.design_profile is None
    assert product.layout == {}
    assert product.power_budget == {}
    assert spec.required_blocks_for_product(product) == ()


def test_profile_source_identity_binds_real_generated_board_behavior(
    tmp_path: Path,
) -> None:
    product = load(tmp_path, protected_product())
    spec.validate_profile_source_identity(
        product, spec.PROTECTED_USB_INDICATOR_SOURCE_SHA256
    )
    with pytest.raises(ProjectShapeError, match="board source differs"):
        spec.validate_profile_source_identity(product, "0" * 64)
