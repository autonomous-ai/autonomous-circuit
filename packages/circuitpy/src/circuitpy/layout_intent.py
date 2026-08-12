"""Validation for the product-level physical-layout contract.

``product.json.envelopeMm`` is only a maximum.  It cannot express the facts a
real product depends on: an exact mechanical outline, which population belongs
on each assembly side, where a connector reaches the enclosure, which layers
carry ground planes, which supply pins own local bypass loops, or where a power
rail may neck down.  The optional
``product.json.layout`` object carries those decisions and the independent
verifier measures the compiled board against them.
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, NoReturn

from circuitpy.errors import ProjectShapeError

_LAYERS = {"top", "bottom", "inner1", "inner2"}
_SIDES = {"top", "bottom"}
_EDGES = {"top", "bottom", "left", "right"}
_ZONE_CONTAINMENT = {"center", "courtyard"}
_ZONE_SHAPES = {"circle", "annulus", "rect"}
_KEYS = {
    "boardSizeMm",
    "boardSizeToleranceMm",
    "componentSides",
    "componentZones",
    "decoupling",
    "edgeConnectors",
    "groundPlanes",
    "minCopperClearanceMm",
    "netClasses",
}


def _fail(path: str, detail: str) -> "NoReturn":
    raise ProjectShapeError(f"product.json '{path}' {detail}")


def _positive(value: object, path: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, f"must be a {'non-negative' if allow_zero else 'positive'} number (got {value!r})")
    number = float(value)
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        _fail(path, f"must be a {'non-negative' if allow_zero else 'positive'} number (got {value!r})")
    return number


def _point(value: object, path: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        _fail(path, f"must be [x, y] in board millimetres (got {value!r})")
    coordinates: list[float] = []
    for index, coordinate in enumerate(value):
        if (
            isinstance(coordinate, bool)
            or not isinstance(coordinate, (int, float))
            or not math.isfinite(float(coordinate))
        ):
            _fail(f"{path}[{index}]", f"must be a finite number (got {coordinate!r})")
        coordinates.append(float(coordinate))
    return coordinates[0], coordinates[1]


def _strings(value: object, path: str) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if (
        isinstance(value, list)
        and value
        and all(isinstance(item, str) and item for item in value)
    ):
        return list(value)
    _fail(path, f"must be a non-empty string or list of strings (got {value!r})")


def validate_layout(raw: object) -> dict[str, Any]:
    """Return a defensive copy of a valid layout contract.

    Unknown keys fail closed. A misspelled constraint that silently does not
    run is worse than a malformed product file stopping before the router.
    """

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _fail("layout", f"must be an object (got {type(raw).__name__})")
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        _fail("layout", f"contains unknown member(s): {', '.join(unknown)}")

    if "boardSizeMm" in raw:
        size = raw["boardSizeMm"]
        if not isinstance(size, list) or len(size) != 2:
            _fail("layout.boardSizeMm", f"must be [width, height] in mm (got {size!r})")
        _positive(size[0], "layout.boardSizeMm[0]")
        _positive(size[1], "layout.boardSizeMm[1]")
    if "boardSizeToleranceMm" in raw:
        _positive(raw["boardSizeToleranceMm"], "layout.boardSizeToleranceMm", allow_zero=True)
    if "minCopperClearanceMm" in raw:
        _positive(raw["minCopperClearanceMm"], "layout.minCopperClearanceMm")

    decoupling = raw.get("decoupling")
    if decoupling is not None:
        if not isinstance(decoupling, dict):
            _fail("layout.decoupling", "must be an object")
        _positive(
            decoupling.get("maxDistanceMm"),
            "layout.decoupling.maxDistanceMm",
        )
        if "exclude" in decoupling:
            _strings(decoupling["exclude"], "layout.decoupling.exclude")
        overrides = decoupling.get("overrides")
        if overrides is not None:
            if not isinstance(overrides, list) or not overrides:
                _fail("layout.decoupling.overrides", "must be a non-empty list")
            for index, override in enumerate(overrides):
                path = f"layout.decoupling.overrides[{index}]"
                if not isinstance(override, dict):
                    _fail(path, f"must be an object (got {type(override).__name__})")
                _strings(override.get("match"), f"{path}.match")
                _positive(
                    override.get("maxDistanceMm"),
                    f"{path}.maxDistanceMm",
                )
                source = override.get("source")
                if not isinstance(source, str) or not source.strip():
                    _fail(
                        f"{path}.source",
                        "must cite a non-empty manufacturer reference URI or document identifier",
                    )
                unknown_override = sorted(
                    set(override) - {"match", "maxDistanceMm", "source"}
                )
                if unknown_override:
                    _fail(
                        path,
                        f"contains unknown member(s): {', '.join(unknown_override)}",
                    )
        unknown_rule = sorted(
            set(decoupling) - {"maxDistanceMm", "exclude", "overrides"}
        )
        if unknown_rule:
            _fail(
                "layout.decoupling",
                f"contains unknown member(s): {', '.join(unknown_rule)}",
            )

    sides = raw.get("componentSides")
    if sides is not None:
        if not isinstance(sides, list) or not sides:
            _fail("layout.componentSides", "must be a non-empty list")
        for index, rule in enumerate(sides):
            path = f"layout.componentSides[{index}]"
            if not isinstance(rule, dict):
                _fail(path, f"must be an object (got {type(rule).__name__})")
            _strings(rule.get("match"), f"{path}.match")
            if rule.get("side") not in _SIDES:
                _fail(f"{path}.side", f"must be top or bottom (got {rule.get('side')!r})")
            unknown_rule = sorted(set(rule) - {"match", "side"})
            if unknown_rule:
                _fail(path, f"contains unknown member(s): {', '.join(unknown_rule)}")

    zones = raw.get("componentZones")
    if zones is not None:
        if not isinstance(zones, list) or not zones:
            _fail("layout.componentZones", "must be a non-empty list")
        for index, rule in enumerate(zones):
            path = f"layout.componentZones[{index}]"
            if not isinstance(rule, dict):
                _fail(path, f"must be an object (got {type(rule).__name__})")
            _strings(rule.get("match"), f"{path}.match")
            containment = rule.get("containment")
            if (
                not isinstance(containment, str)
                or containment not in _ZONE_CONTAINMENT
            ):
                _fail(
                    f"{path}.containment",
                    f"must be center or courtyard (got {containment!r})",
                )
            shape = rule.get("shape")
            if not isinstance(shape, dict):
                _fail(f"{path}.shape", "must be an object")
            kind = shape.get("kind")
            if not isinstance(kind, str) or kind not in _ZONE_SHAPES:
                _fail(
                    f"{path}.shape.kind",
                    f"must be one of {', '.join(sorted(_ZONE_SHAPES))}",
                )
            _point(shape.get("center"), f"{path}.shape.center")
            shape_keys = {"kind", "center"}
            if kind == "circle":
                _positive(shape.get("radiusMm"), f"{path}.shape.radiusMm")
                shape_keys.add("radiusMm")
            elif kind == "annulus":
                inner = _positive(
                    shape.get("innerRadiusMm"),
                    f"{path}.shape.innerRadiusMm",
                    allow_zero=True,
                )
                outer = _positive(
                    shape.get("outerRadiusMm"),
                    f"{path}.shape.outerRadiusMm",
                )
                if inner >= outer:
                    _fail(
                        f"{path}.shape.innerRadiusMm",
                        "must be less than outerRadiusMm",
                    )
                shape_keys.update({"innerRadiusMm", "outerRadiusMm"})
            else:
                _positive(shape.get("widthMm"), f"{path}.shape.widthMm")
                _positive(shape.get("heightMm"), f"{path}.shape.heightMm")
                shape_keys.update({"widthMm", "heightMm"})
            unknown_shape = sorted(set(shape) - shape_keys)
            if unknown_shape:
                _fail(
                    f"{path}.shape",
                    f"contains unknown member(s): {', '.join(unknown_shape)}",
                )
            unknown_rule = sorted(set(rule) - {"match", "shape", "containment"})
            if unknown_rule:
                _fail(path, f"contains unknown member(s): {', '.join(unknown_rule)}")

    connectors = raw.get("edgeConnectors")
    if connectors is not None:
        if not isinstance(connectors, list) or not connectors:
            _fail("layout.edgeConnectors", "must be a non-empty list")
        for index, rule in enumerate(connectors):
            path = f"layout.edgeConnectors[{index}]"
            if not isinstance(rule, dict):
                _fail(path, f"must be an object (got {type(rule).__name__})")
            if not isinstance(rule.get("ref"), str) or not rule["ref"]:
                _fail(f"{path}.ref", "must be a non-empty reference designator")
            if rule.get("edge") not in _EDGES:
                _fail(f"{path}.edge", f"must be one of {', '.join(sorted(_EDGES))}")
            if rule.get("alignment", "center") != "center":
                _fail(f"{path}.alignment", "currently supports only 'center'")
            for key, default in (("edgeToleranceMm", 1.0), ("centerToleranceMm", 0.5)):
                _positive(rule.get(key, default), f"{path}.{key}", allow_zero=True)
            unknown_rule = sorted(
                set(rule)
                - {"ref", "edge", "alignment", "edgeToleranceMm", "centerToleranceMm"}
            )
            if unknown_rule:
                _fail(path, f"contains unknown member(s): {', '.join(unknown_rule)}")

    planes = raw.get("groundPlanes")
    if planes is not None:
        if not isinstance(planes, dict):
            _fail("layout.groundPlanes", "must be an object")
        layers = _strings(planes.get("layers"), "layout.groundPlanes.layers")
        invalid = sorted(set(layers) - _LAYERS)
        if invalid:
            _fail("layout.groundPlanes.layers", f"contains invalid layer(s): {', '.join(invalid)}")
        if len(set(layers)) != len(layers):
            _fail("layout.groundPlanes.layers", "must not contain duplicates")
        if "maxRoutedLengthMm" in planes:
            _positive(
                planes["maxRoutedLengthMm"],
                "layout.groundPlanes.maxRoutedLengthMm",
                allow_zero=True,
            )
        if "maxFanoutLengthMm" in planes:
            _positive(
                planes["maxFanoutLengthMm"],
                "layout.groundPlanes.maxFanoutLengthMm",
            )
        if "stitchingPitchMm" in planes:
            _positive(planes["stitchingPitchMm"], "layout.groundPlanes.stitchingPitchMm")
        unknown_rule = sorted(
            set(planes)
            - {
                "layers",
                "maxRoutedLengthMm",
                "maxFanoutLengthMm",
                "stitchingPitchMm",
            }
        )
        if unknown_rule:
            _fail("layout.groundPlanes", f"contains unknown member(s): {', '.join(unknown_rule)}")

    classes = raw.get("netClasses")
    if classes is not None:
        if not isinstance(classes, list) or not classes:
            _fail("layout.netClasses", "must be a non-empty list")
        for index, rule in enumerate(classes):
            path = f"layout.netClasses[{index}]"
            if not isinstance(rule, dict):
                _fail(path, f"must be an object (got {type(rule).__name__})")
            _strings(rule.get("nets"), f"{path}.nets")
            trunk = _positive(rule.get("minTrunkWidthMm"), f"{path}.minTrunkWidthMm")
            neckdown = _positive(
                rule.get("minNeckdownWidthMm", trunk), f"{path}.minNeckdownWidthMm"
            )
            _positive(
                rule.get("maxNeckdownLengthMm", 0),
                f"{path}.maxNeckdownLengthMm",
                allow_zero=True,
            )
            via_outer = None
            via_hole = None
            if "minViaOuterDiameterMm" in rule:
                via_outer = _positive(
                    rule["minViaOuterDiameterMm"],
                    f"{path}.minViaOuterDiameterMm",
                )
            if "minViaHoleDiameterMm" in rule:
                via_hole = _positive(
                    rule["minViaHoleDiameterMm"],
                    f"{path}.minViaHoleDiameterMm",
                )
            if via_outer is not None and via_hole is not None and via_outer <= via_hole:
                _fail(
                    f"{path}.minViaOuterDiameterMm",
                    "must be greater than minViaHoleDiameterMm",
                )
            if neckdown > trunk:
                _fail(f"{path}.minNeckdownWidthMm", "must not exceed minTrunkWidthMm")
            unknown_rule = sorted(
                set(rule)
                - {
                    "name",
                    "nets",
                    "minTrunkWidthMm",
                    "minNeckdownWidthMm",
                    "maxNeckdownLengthMm",
                    "minViaOuterDiameterMm",
                    "minViaHoleDiameterMm",
                }
            )
            if unknown_rule:
                _fail(path, f"contains unknown member(s): {', '.join(unknown_rule)}")
            if "name" in rule and (not isinstance(rule["name"], str) or not rule["name"]):
                _fail(f"{path}.name", "must be a non-empty string")

    return deepcopy(raw)
