# The safety gate — likeness + compliance, at UGC scale

**Load when:** locking a character sheet, before publishing/exporting, wiring
distribution, or reasoning about legal/platform risk. When millions of non-
producers create, two risks scale: an AI face that resembles a real person, and
content that trips a platform's red lines. This is the pre-publish gate. Backbone:
`dramalib.safety`.

## Screener, not judge (the posture that keeps us honest)

The gate produces **evidence for a human**, never an automatic legal ruling.
Face-similarity is feature-distance, not infringement — a high score is a *reason
to look*, a low score is *not proof of safety*. Every verdict is reviewable, and
"we didn't check" is its own state (`not_screened`), distinct from `pass`. This is
the liability-correct stance: we surface risk and log the decision; a human (and,
at the top, legal) owns the call.

## 1. The likeness gate

`dramalib.safety.likeness_gate(images=…, screener=…)`. The screener backend (a
face detector + embedding compared to a public-figure reference library —
buildable later; the interface is fixed now) returns matches; the gate maps the
top similarity to a verdict:

- **`pass`** — below threshold; proceed (still logged).
- **`escalate`** — at/above threshold; a human reviews the evidence before ship.
- **`regenerate`** — reroll the face pinned away from the match.
- **`licensed_exception`** — an intended, rights-cleared likeness; logged with
  the license.
- **`not_screened`** — no backend wired. **Never treated as safe.** Until the
  screener exists, this is what an unscreened image returns — we never assert a
  safety we didn't check.

**Gate at three checkpoints** (`SAFETY_CHECKPOINTS`, cheapest fix first):
`character_sheet_lock` → `first_frame` (the video model can drift a face) →
`pre_ad_publish` (posters/ad-cuts reach the most people).

**Out of scope for the face gate** (need their own checks): name, voice,
performance style, signature costume, and copying a specific photo.

## 2. The compliance scan

`dramalib.safety.compliance_scan(text=…)` — a deterministic text pre-check that
flags platform red-line categories (`DEFAULT_RED_LINES` — a minimal clinical
starter; **ops extends it per platform** — 抖音/红果/ReelShort each publish their
own — and per market) plus any caller-supplied banned terms. Run it on prompts and
dialogue *before* a hosted call, so we don't spend render money on content an app
will reject, and so obviously-prohibited material is stopped at the door. Also a
flag-for-human, not a verdict.

## 3. Provenance (design now, wire at export)

Attach **C2PA** content-credentials to every published output (AI-generated,
tool, timestamp). Table-stakes for a platform distributing synthetic video, and it
supports the EU AI Act Art. 50 deepfake-transparency obligation. Not built here;
wire it at the export stage.

## Legal anchors (why this exists)

Right-of-publicity / likeness: **CA Civil Code §3344**; biometric/sensitive data:
**PIPL**, **GDPR Art. 9**; synthetic-media transparency: **EU AI Act Art. 50**.
None of this is legal advice — it's why the gate is *evidence + human review +
logged exception*, not an automated pass/fail.

## Pitfalls

- **Treating `not_screened` as `pass`.** The single most dangerous mistake — it
  ships unchecked faces. Wire the screener before scale; until then, flag it.
- **Automating the legal call.** The gate never decides infringement; it escalates.
- **A static banned list.** Red lines differ by platform and market and change;
  `DEFAULT_RED_LINES` is a starter to extend in ops, not the whole policy.
- **Screening only the sheet.** The video model drifts the face and the ad-cut is
  the widest surface — screen at all three checkpoints.
