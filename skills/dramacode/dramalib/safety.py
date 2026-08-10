"""Pre-publish safety — the likeness gate + a text-side compliance scan.

When millions of non-producers create, two risks scale with them: (1) an AI face
that resembles a real person (right-of-publicity / likeness exposure — PIPL, GDPR
Art. 9, EU AI Act Art. 50, CA Civil Code §3344), and (2) content that violates a
platform's red lines (wasted hosted calls + rejected uploads). This module is the
**framework + the offline-computable half**:

- ``likeness_gate`` — a *pluggable* screener. The real backend (a face detector +
  embedding vs. a public-figure library) wires in later; until then the gate
  returns ``not_screened`` — **never a false ``pass``** (absence of screening is
  not safety). Philosophy, from the research: a **screener, not a judge** —
  similarity is feature-distance, not infringement; it produces *evidence for a
  human*, never an automatic legal ruling.
- ``compliance_scan`` — deterministic text pre-check for platform red lines +
  caller-supplied banned terms, so we don't spend render money on a prompt an app
  will reject. Also a flag-for-human, not a verdict. Default list is a minimal
  clinical starter; ops extends it per target platform/market.
"""

from __future__ import annotations

# Verdict states (cheapest fix first). ``not_screened`` is distinct from ``pass``.
LIKENESS_VERDICTS = ("pass", "regenerate", "escalate", "licensed_exception", "not_screened")

# Gate at three points — cheapest-to-fix first (research: sheet < frame < ad).
SAFETY_CHECKPOINTS = ("character_sheet_lock", "first_frame", "pre_ad_publish")


def likeness_gate(*, images, screener=None, threshold: float = 0.6) -> dict:
    """Screen candidate character images for resemblance to real people.

    ``screener``: a callable ``images -> [{"name", "similarity", "source"}]``
    (the real detector+embedding backend, injected). ``threshold``: similarity at
    or above which to ``escalate`` for human review.

    Returns ``{"verdict", "evidence", "top_similarity", "note"}``. With NO screener
    wired, returns ``not_screened`` — we never assert safety we didn't check.
    """
    if screener is None:
        return {
            "verdict": "not_screened",
            "evidence": [],
            "top_similarity": None,
            "note": "no likeness screener wired — treat as UNSCREENED, not safe",
        }
    matches = list(screener(images) or [])
    top = max((float(m.get("similarity", 0.0)) for m in matches), default=0.0)
    verdict = "escalate" if top >= threshold else "pass"
    return {
        "verdict": verdict,
        "evidence": matches,
        "top_similarity": top,
        "note": "similarity is feature-distance, not infringement — evidence for a human",
    }


# Minimal clinical red-line categories → stem list. A STARTER — ops extends per
# platform (抖音/红果/ReelShort each publish their own) and market. Stems are
# matched case-insensitively as substrings.
DEFAULT_RED_LINES: dict[str, tuple[str, ...]] = {
    "minor_safety": ("underage", "child sex", "minor sex"),
    "non_consent": ("nonconsensual", "non-consensual", "rape"),
    "graphic_gore": ("dismember", "decapitat", "mutilat"),
    "real_person_defamation": (),  # populate with claimant names per legal review
}


def compliance_scan(*, text, banned_terms=(), red_lines: dict | None = None) -> list[dict]:
    """Flag red-line categories + caller-supplied banned terms in a prompt/line.

    Returns ``[{"category", "term", "severity"}]`` (empty = clean). A flag-for-
    human pre-check, not a verdict — it stops obvious spend-wasters and platform
    rejections before a hosted call, and routes the rest to review.
    """
    hay = str(text or "").lower()
    out: list[dict] = []
    lines = DEFAULT_RED_LINES if red_lines is None else red_lines
    for category, stems in lines.items():
        for stem in stems:
            if stem and stem.lower() in hay:
                out.append({"category": category, "term": stem, "severity": "blocker"})
    for term in banned_terms:
        if term and str(term).lower() in hay:
            out.append({"category": "banned_term", "term": term, "severity": "warning"})
    return out
