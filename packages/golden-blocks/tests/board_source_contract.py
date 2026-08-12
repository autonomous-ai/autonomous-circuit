"""Small self-contained JSX board-tag helpers for golden acceptance tests.

The golden suite intentionally does not import circuitpy internals.  These
helpers keep its source classification and gauntlet rewrite on one conservative
contract: only a definite true disables routing, and a gauntlet override is the
last board prop so an earlier spread cannot silently turn routing back off.
"""

from __future__ import annotations

import re


def _strip_js_comments(source: str) -> str:
    """Blank JS comments while retaining strings and byte positions."""

    output = list(source)
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'", "`"}:
            quote = char
            index += 1
            continue
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = len(source) if end < 0 else end
            output[index:end] = " " * (end - index)
            index = end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = len(source) if end < 0 else end + 2
            output[index:end] = [
                "\n" if value == "\n" else " " for value in source[index:end]
            ]
            index = end
            continue
        index += 1
    return "".join(output)


def board_opening_span(source: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` for the real JSX board opening tag."""

    uncommented = _strip_js_comments(source)
    match = re.search(r"<board\b", uncommented)
    if match is None:
        return None
    quote: str | None = None
    escaped = False
    brace_depth = 0
    for index in range(match.start(), len(uncommented)):
        char = uncommented[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == "{":
            brace_depth += 1
        elif char == "}" and brace_depth:
            brace_depth -= 1
        elif char == ">" and brace_depth == 0:
            return match.start(), index + 1
    return None


def _skip_attribute_value(tag: str, index: int) -> int:
    while index < len(tag) and tag[index].isspace():
        index += 1
    if index >= len(tag) or tag[index] != "=":
        return index
    index += 1
    while index < len(tag) and tag[index].isspace():
        index += 1
    if index >= len(tag):
        return index
    if tag[index] in {'"', "'", "`"}:
        quote = tag[index]
        index += 1
        escaped = False
        while index < len(tag):
            char = tag[index]
            index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                break
        return index
    if tag[index] == "{":
        depth = 0
        quote: str | None = None
        escaped = False
        while index < len(tag):
            char = tag[index]
            index += 1
            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in {'"', "'", "`"}:
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
        return index
    while index < len(tag) and not tag[index].isspace() and tag[index] != ">":
        index += 1
    return index


def _direct_attribute_span(tag: str, name: str) -> tuple[int, int] | None:
    """Locate one top-level JSX prop, ignoring strings and spread objects."""

    tag = _strip_js_comments(tag)
    index = len("<board")
    while index < len(tag):
        while index < len(tag) and tag[index].isspace():
            index += 1
        if index >= len(tag) or tag[index] in ">/":
            return None
        if tag[index] == "{":
            index = _skip_attribute_value("=" + tag[index:], 0) + index - 1
            continue
        start = index
        while index < len(tag) and (
            tag[index].isalnum() or tag[index] in "_:-"
        ):
            index += 1
        if index == start:
            index += 1
            continue
        attribute = tag[start:index]
        end = _skip_attribute_value(tag, index)
        if attribute == name:
            horizontal_start = start
            while horizontal_start > 0 and tag[horizontal_start - 1] in " \t":
                horizontal_start -= 1
            return horizontal_start, end
        index = end
    return None


def _top_level_spread_positions(tag: str) -> list[int]:
    """Return JSX spread positions, excluding strings and nested prop values."""

    tag = _strip_js_comments(tag)
    positions: list[int] = []
    index = len("<board")
    while index < len(tag):
        while index < len(tag) and tag[index].isspace():
            index += 1
        if index >= len(tag) or tag[index] in ">/":
            break
        if tag[index] == "{":
            if tag[index + 1 :].lstrip().startswith("..."):
                positions.append(index)
            index = _skip_attribute_value("=" + tag[index:], 0) + index - 1
            continue
        start = index
        while index < len(tag) and (
            tag[index].isalnum() or tag[index] in "_:-"
        ):
            index += 1
        if index == start:
            index += 1
            continue
        index = _skip_attribute_value(tag, index)
    return positions


def _routing_control(source: str) -> tuple[str, tuple[int, int] | None, tuple[int, int]]:
    span = board_opening_span(source)
    if span is None:
        raise AssertionError("golden testbench has no <board> opening tag")
    tag = source[slice(*span)]
    attribute_span = _direct_attribute_span(tag, "routingDisabled")
    spread_positions = _top_level_spread_positions(tag)
    if attribute_span is None:
        # A spread can supply routingDisabled without a reviewable direct
        # value. Classification stays conservative (routed), but a gauntlet
        # rewrite must not guess what it evaluates to.
        return ("dynamic" if spread_positions else "absent"), None, span
    if any(position > attribute_span[0] for position in spread_positions):
        return "dynamic", attribute_span, span
    attribute = _strip_js_comments(tag[slice(*attribute_span)]).strip()
    if "=" not in attribute:
        return "disabled", attribute_span, span
    value = attribute.split("=", 1)[1].strip()
    if re.fullmatch(r"(?:\{\s*true\s*\}|['\"]true['\"])", value):
        return "disabled", attribute_span, span
    if re.fullmatch(r"(?:\{\s*false\s*\}|['\"]false['\"])", value):
        return "enabled", attribute_span, span
    if re.fullmatch(r"\{[^{}]*\?\?\s*false\s*\}", value, re.S):
        return "default-false", attribute_span, span
    return "dynamic", attribute_span, span


def routing_is_definitely_disabled(source: str) -> bool:
    """True only for a direct bare/literal-true board prop."""

    state, _, _ = _routing_control(source)
    return state == "disabled"


def force_routing_enabled(source: str) -> str:
    """Enable a definitely-disabled bench without guessing dynamic JSX.

    Literal false and the supported zero-prop ``?? false`` wrapper already
    route and remain byte-identical. Bare/literal true becomes literal false.
    A dynamic expression or spread is unknowable at this source boundary and
    fails closed instead of being mutilated by a text replacement.
    """

    state, attribute_span, span = _routing_control(source)
    if state in {"absent", "enabled", "default-false"}:
        return source
    if state == "dynamic":
        raise AssertionError(
            "golden gauntlet cannot override a dynamic routingDisabled control; "
            "the fixture must expose a literal false or a zero-prop ?? false default"
        )
    assert attribute_span is not None
    start, end = span
    tag = source[start:end]
    left, right = attribute_span
    replacement = " routingDisabled={false}"
    if right < len(tag) and tag[right] in "/>":
        replacement += " "
    tag = tag[:left] + replacement + tag[right:]
    return source[:start] + tag + source[end:]
