"""The safety envelope, as refusals rather than prose.

Three hard rules, from ``docs/circuit-interfaces.md`` §1:

1. **No mains, ever.** Low-voltage DC only (<= ``MAX_DC_INPUT_V``).
2. **Battery only via a sealed, validated charge/protect block.**
3. **Radio only as a certified module.** Never bare-die RF.

The design rule that matters most here is borrowed from the donor's
``dramalib.safety``: **absence of screening is not safety.** A verdict is
``pass`` only when a screener actually ran and actually cleared the input;
anything else — an unparseable spec, an unknown power source, a screener that
raised — comes back ``not_screened``, which callers must treat as a refusal.
A gate that returns "fine" when it does not know is worse than no gate,
because it launders ignorance into permission.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from circuitlib.tables import MAX_DC_INPUT_V

PASS = "pass"
REFUSE = "refuse"
NOT_SCREENED = "not_screened"

#: Power sources the envelope permits, mapped to their nominal input volts.
ALLOWED_POWER = {
    "usb-c-5v": 5.0,
    "external-dc-lv": 24.0,
    "battery-lipo-sealed-block": 4.2,
}

#: Patterns that mean "someone is trying to touch the wall". Matched against
#: the board source and the product description, case-insensitively.
MAINS_PATTERNS = (
    r"\bmains\b",
    r"\b(?:110|115|120|220|230|240)\s*v(?:ac)?\b",
    r"\bvac\b",
    r"\bline\s+voltage\b",
    r"\bwall\s+(?:outlet|socket|plug|power)\b",
    r"\biec\s*320\b",
    r"\btriac\b",
    r"\bopto[-\s]?triac\b",
    r"\brelay\b.{0,40}\b(?:mains|ac)\b",
    r"\bac[-\s]?dc\s+(?:converter|module|supply)\b",
)

#: Bare RF silicon we refuse in favour of a certified module. Non-exhaustive by
#: nature — the positive rule (modules only) is what the SKILL.md teaches; this
#: list catches the common attempts.
BARE_RF_PATTERNS = (
    r"\bnrf24l01\b",
    r"\bcc2500\b",
    r"\bsi4432\b",
    r"\bballun\b",
    r"\bpi\s*match(?:ing)?\s+network\b",
    r"\bchip\s+antenna\b",
    r"\bpcb\s+(?:trace\s+)?antenna\b",
)

#: Charger silicon that may only appear inside a sealed, signed-off block.
CHARGER_PATTERNS = (
    r"\btp4056\b",
    r"\bmcp7383\d?\b",
    r"\bbq2\d{4}\b",
    r"\bdw01\b",
    r"\bfs8205\b",
    r"\bli-?(?:po|ion)\s+charg",
)


@dataclass(frozen=True)
class Verdict:
    """``status`` is one of PASS / REFUSE / NOT_SCREENED."""

    status: str
    reasons: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True only for an explicit pass. ``not_screened`` is never ok."""
        return self.status == PASS

    def as_warnings(self, part: str = "board") -> list[dict[str, str]]:
        """Contract-shaped warnings (``safety_envelope``, severity ``error``)."""
        if self.ok:
            return []
        return [
            {
                "part": part,
                "kind": "safety_envelope",
                "detail": reason,
                "severity": "error",
            }
            for reason in (self.reasons or ("safety screening did not run",))
        ]


#: Words that turn a mention into a disavowal. A board brief that says
#: "no mains, ever" is stating compliance, not requesting a violation — and
#: refusing it was a real false positive (found 2026-08-10 by a board whose
#: own description quoted the envelope rule back at us).
_NEGATORS = r"(?:no|not|never|without|avoid|avoids|excludes?|free\s+of|zero)"


def _negated(text: str, start: int) -> bool:
    """True when a negator sits immediately before the match.

    Deliberately narrow — the negator must sit immediately before the match.
    The asymmetry decides the design: a false refusal is an annoyance, a false
    pass is a fire. So "no mains" is exonerated and "switches mains, no
    problem" is not, and the rule stays adjacency-only even though that leaves
    "never touches mains" refused. Allowing intervening words would also
    exonerate "no problem, switches mains", which is exactly the sentence this
    gate exists to catch. Rephrasing costs a user seconds; the other error
    costs them a fire.
    """
    window = text[max(0, start - 24):start]
    return re.search(rf"\b{_NEGATORS}\b[\s,\-]*$", window) is not None


def _hits(text: str, patterns: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, lowered):
            if not _negated(lowered, match.start()):
                found.append(pattern)
                break
    return found


def screen_text(text: str, *, origin: str = "source") -> Verdict:
    """Screen board source or a product description for envelope violations."""
    if not isinstance(text, str):
        return Verdict(NOT_SCREENED, (f"{origin} was not readable as text",))

    reasons: list[str] = []
    if _hits(text, MAINS_PATTERNS):
        reasons.append(
            f"{origin} references mains/AC line voltage — the envelope is "
            f"low-voltage DC only (<= {MAX_DC_INPUT_V:g}V). No mains, ever."
        )
    if _hits(text, BARE_RF_PATTERNS):
        reasons.append(
            f"{origin} references discrete RF (bare transceiver, matching "
            "network or antenna) — radio is permitted only as a certified module."
        )
    charger = _hits(text, CHARGER_PATTERNS)
    if charger:
        reasons.append(
            f"{origin} references battery-charging silicon — charging is "
            "permitted only inside a sealed validated block, and no such block "
            "has hardware sign-off yet."
        )
    return Verdict(REFUSE, tuple(reasons)) if reasons else Verdict(PASS)


def screen_power(power: object) -> Verdict:
    """Screen a ``product.json`` power declaration."""
    if not isinstance(power, str) or not power.strip():
        return Verdict(NOT_SCREENED, ("product.json declares no power source",))
    key = power.strip().lower()
    if key not in ALLOWED_POWER:
        return Verdict(
            REFUSE,
            (
                f"power source {power!r} is not in the envelope "
                f"({', '.join(sorted(ALLOWED_POWER))})",
            ),
        )
    if key == "battery-lipo-sealed-block":
        return Verdict(
            REFUSE,
            (
                "battery power needs the sealed lipo charge/protect block, which "
                "has no hardware sign-off yet — ship the USB-C variant first",
            ),
        )
    return Verdict(PASS)


def screen_voltage(volts: object) -> Verdict:
    try:
        v = float(volts)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return Verdict(NOT_SCREENED, (f"input voltage {volts!r} is not a number",))
    if v > MAX_DC_INPUT_V:
        return Verdict(
            REFUSE,
            (f"input {v:g}V exceeds the {MAX_DC_INPUT_V:g}V low-voltage ceiling",),
        )
    if v < 0:
        return Verdict(REFUSE, (f"input {v:g}V is negative — not supported",))
    return Verdict(PASS)


def safety_gate(
    *,
    source: str | None = None,
    description: str | None = None,
    power: object = None,
    input_volts: object = None,
) -> Verdict:
    """Run every applicable screener. Refusals win; unscreened never passes.

    Callers pass what they have. If they pass nothing, the answer is
    ``not_screened`` — deliberately, so that "we did not look" can never be
    mistaken for "we looked and it was fine".
    """
    verdicts: list[Verdict] = []
    if source is not None:
        verdicts.append(screen_text(source, origin="board source"))
    if description is not None:
        verdicts.append(screen_text(description, origin="product description"))
    if power is not None:
        verdicts.append(screen_power(power))
    if input_volts is not None:
        verdicts.append(screen_voltage(input_volts))

    if not verdicts:
        return Verdict(NOT_SCREENED, ("nothing was submitted for screening",))

    reasons = tuple(r for v in verdicts for r in v.reasons)
    if any(v.status == REFUSE for v in verdicts):
        return Verdict(REFUSE, reasons)
    if any(v.status == NOT_SCREENED for v in verdicts):
        return Verdict(NOT_SCREENED, reasons)
    return Verdict(PASS)
