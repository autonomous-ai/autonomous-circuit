"""Spec-time validation for ``product.json.powerBudget``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuitpy import spec
from circuitpy.errors import ProjectShapeError
from circuitpy.power_intent import validate_power_budget


def policy() -> dict:
    return {
        "usb": {
            "rawVbusNet": "VBUS_RAW",
            "protectedVbusNet": "V5",
            "rawAttachCapacitanceMaxUf": 10,
            "sourceCurrentMaxMa": 500,
            "fixedOperationalLoadMa": 100,
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
                "maxTripMa": 497,
            },
            "firmwareLimitedLoads": [
                {
                    "match": "D1[0-7]",
                    "perDevicePhysicalPeakMa": 60,
                    "aggregateOperationalMaxMa": 300,
                }
            ],
        }
    }


def regulator_policy() -> dict:
    return {
        "regulators": [
            {
                "profile": "ap7361c-33e-c500795-v1",
                "ref": "U2",
                "inputNet": "V5",
                "outputNet": "V3_3",
                "inputCapRef": "C2",
                "outputCapRef": "C3",
                "maxAmbientC": 50,
            }
        ]
    }


def test_valid_contract_is_copied() -> None:
    raw = policy()
    resolved = validate_power_budget(raw)
    assert resolved == raw
    resolved["usb"]["rawVbusNet"] = "changed"
    assert raw["usb"]["rawVbusNet"] == "VBUS_RAW"


def test_audited_regulator_contract_is_copied() -> None:
    raw = regulator_policy()
    resolved = validate_power_budget(raw)
    assert resolved == raw
    resolved["regulators"][0]["outputNet"] = "changed"
    assert raw["regulators"][0]["outputNet"] == "V3_3"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.update({"regulators": {}}), "must be a list"),
        (
            lambda p: p["regulators"][0].update({"profile": "invented"}),
            "audited regulator profile",
        ),
        (
            lambda p: p["regulators"][0].update({"outputNet": "V5"}),
            "must differ from inputNet",
        ),
        (
            lambda p: p["regulators"][0].update({"outputCapRef": "C2"}),
            "distinct regulator/input-cap/output-cap",
        ),
        (
            lambda p: p["regulators"].append(dict(p["regulators"][0])),
            "duplicates regulator reference",
        ),
        (
            lambda p: p["regulators"][0].update({"thetaJa": 1}),
            "unknown member",
        ),
        (
            lambda p: p["regulators"][0].update({"maxAmbientC": 49}),
            "between 50 and 85",
        ),
        (
            lambda p: p["regulators"][0].update({"maxAmbientC": 86}),
            "between 50 and 85",
        ),
    ],
)
def test_unreviewed_or_ambiguous_regulator_contracts_fail_closed(
    mutate, message: str
) -> None:
    raw = regulator_policy()
    mutate(raw)
    with pytest.raises(ProjectShapeError, match=message):
        validate_power_budget(raw)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p.update({"usbb": {}}), "unknown member"),
        (lambda p: p["usb"].update({"rawVbusNet": "V5"}), "must differ"),
        (lambda p: p["usb"].update({"rawAttachCapacitanceMaxUf": 0}), "positive"),
        (lambda p: p["usb"].update({"sourceCurrentMaxMa": float("inf")}), "finite"),
        (
            lambda p: p["usb"]["currentLimiter"].update({"minTripMa": 498}),
            "must not exceed maxTripMa",
        ),
        (
            lambda p: p["usb"]["currentLimiter"].update({"maxTripMa": 501}),
            "must not exceed sourceCurrentMaxMa",
        ),
        (
            lambda p: p["usb"]["currentLimiter"].pop("settingResistor"),
            "settingResistor.*must be an object",
        ),
        (
            lambda p: p["usb"]["currentLimiter"]["settingResistor"].update(
                {"resistanceOhms": 0}
            ),
            "settingResistor.resistanceOhms.*positive",
        ),
        (
            lambda p: p["usb"].update({"fixedOperationalLoadMa": 101}),
            "above the limiter",
        ),
        (
            lambda p: p["usb"]["firmwareLimitedLoads"][0].update({"match": []}),
            "non-empty string",
        ),
    ],
)
def test_invalid_contracts_fail_closed(mutate, message: str) -> None:
    raw = policy()
    mutate(raw)
    with pytest.raises(ProjectShapeError, match=message):
        validate_power_budget(raw)


def test_load_product_resolves_power_budget(tmp_path: Path) -> None:
    payload = {
        "name": "usb-board",
        "description": "fixture",
        "power": "usb-c-5v",
        "layers": 2,
        "fab": "jlcpcb",
        "assembly": True,
        "powerBudget": policy(),
    }
    # ``policy`` is itself the top-level powerBudget object.
    payload["powerBudget"] = policy()
    (tmp_path / "product.json").write_text(json.dumps(payload), encoding="utf-8")
    product = spec.load_product(tmp_path)
    assert product.power_budget["usb"]["currentLimiter"]["ref"] == "U7"


def test_omitted_contract_preserves_existing_products(tmp_path: Path) -> None:
    payload = {
        "name": "legacy-board",
        "power": "usb-c-5v",
        "layers": 2,
        "fab": "jlcpcb",
        "assembly": False,
    }
    (tmp_path / "product.json").write_text(json.dumps(payload), encoding="utf-8")
    assert spec.load_product(tmp_path).power_budget == {}


def test_usb_budget_requires_a_usb_power_source(tmp_path: Path) -> None:
    payload = {
        "name": "dc-board",
        "power": "external-dc-lv",
        "layers": 2,
        "fab": "jlcpcb",
        "assembly": False,
        "powerBudget": policy(),
    }
    (tmp_path / "product.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProjectShapeError, match="requires product power"):
        spec.load_product(tmp_path)
