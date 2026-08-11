"""The finding contract, shared by every check in this package.

A finding is the pipeline's warning shape verbatim
(``{part, kind, detail, severity}``, contract §1) so a check can be wired into
``circuitpy.checks`` without translation. Two rules carry over from the donor
discipline and one is new here:

* **A check never raises.** A verifier that explodes is a verifier that gets
  deleted. Wrap the body in :func:`never_raises` and a crash becomes one
  ``check_failed`` warning instead of a broken build.
* **The measurement goes in the detail.** Not "too close to the edge" —
  "1.80mm from the board edge, the line needs 2.5mm". A finding a human cannot
  check is a finding a human learns to ignore.
* **Coverage is reported next to findings.** Every check says what it could
  *not* see. Silence must never read as a pass; that is exactly how a check
  that always finds nothing gets mistaken for coverage we do not have.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Callable, Iterable

Finding = dict  # {"part": str, "kind": str, "detail": str, "severity": str}

SEVERITIES = ("error", "warning", "info")


def finding(part: str, kind: str, detail: str, severity: str = "warning") -> Finding:
    if severity not in SEVERITIES:
        severity = "warning"
    return {"part": part, "kind": kind, "detail": detail, "severity": severity}


def check_failed(detail: str, part: str = "board") -> Finding:
    return finding(part, "check_failed", detail, "warning")


def never_raises(fn: Callable[..., list[Finding]]) -> Callable[..., list[Finding]]:
    """Turn any exception into a single ``check_failed`` finding."""

    @functools.wraps(fn)
    def wrapped(*args, **kwargs) -> list[Finding]:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — that is the entire point
            return [check_failed(f"{fn.__name__} raised {type(exc).__name__}: {exc}")]

    return wrapped


@dataclass
class Coverage:
    """What a check could and could not see.

    ``examined`` / ``total`` are whatever unit the check works in (components,
    nets, layers). ``blind`` names the things it had to skip, so the report can
    say "12 of 14 nets" instead of implying it looked at all of them.
    """

    unit: str
    examined: int = 0
    total: int = 0
    blind: list[str] = field(default_factory=list)

    def skip(self, what: str) -> None:
        if what not in self.blind:
            self.blind.append(what)

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "unit": self.unit,
            "examined": self.examined,
            "total": self.total,
        }
        if self.blind:
            out["blind"] = list(self.blind)
        return out


@dataclass
class CheckResult:
    """One check's whole answer: what it found and what it could see."""

    name: str
    findings: list[Finding] = field(default_factory=list)
    coverage: Coverage | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> int:
        return sum(1 for f in self.findings if f.get("severity") == "error")

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"name": self.name, "findings": self.findings}
        if self.coverage is not None:
            out["coverage"] = self.coverage.as_dict()
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def dedupe(findings: Iterable[Finding]) -> list[Finding]:
    """Drop exact duplicates; first occurrence wins, order preserved."""
    seen: set[tuple[str, str, str]] = set()
    out: list[Finding] = []
    for f in findings:
        key = (str(f.get("kind")), str(f.get("part")), str(f.get("detail")))
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out
