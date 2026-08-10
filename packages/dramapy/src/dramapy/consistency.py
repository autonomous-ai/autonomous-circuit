"""Pre-i2v consistency gate — verify the keyframe actually contains every
expected character BEFORE spending on image-to-video.

This is the cheapest point to catch the crowded-shot character-drop (e.g. a
4-character collision keyframe that renders the hero as the wrong person): the
keyframe is a single cheap image, i2v is the expensive stage. A gate here re-rolls
the *keyframe* until every cast member is present, so the drift never reaches i2v
(or a manual audit).

**Off by default.** The provider only runs the gate when ``VIDEO_CONSISTENCY_GATE``
is truthy, which wires a VLM-backed checker; without it the provider behaves
exactly as before. The checker also **fails safe** — any error or an unparseable
answer returns "unknown", which is treated as present (never block or loop on a
flaky check). The real value needs a working vision model; the gate logic + the
fail-safe are what make it safe to ship on the critical path.
"""

from __future__ import annotations

import os

from dramapy.errors import ProviderError

# A lightweight vision-QA model on fal. VERIFY THE ID + RESPONSE SHAPE AT
# WIRE-TIME (override with VIDEO_VLM_MODEL); a wrong id just makes the gate a
# safe no-op (every check returns "unknown").
DEFAULT_VLM_MODEL = "fal-ai/moondream2"

_GATE_ENV = "VIDEO_CONSISTENCY_GATE"
_MODEL_ENV = "VIDEO_VLM_MODEL"


def gate_enabled(env=None) -> bool:
    env = env or os.environ
    return str(env.get(_GATE_ENV, "")).strip().lower() in {"1", "on", "true", "yes"}


class VlmConsistencyChecker:
    """Asks a VLM whether each expected character is present in the keyframe.
    ``missing_characters`` returns the names judged CONFIDENTLY ABSENT (empty =
    all present or undeterminable — we only act on a confident "no")."""

    def __init__(self, client, *, model: str | None = None) -> None:
        self._client = client
        self._model = model or DEFAULT_VLM_MODEL

    def missing_characters(self, image_url: str, characters, *, budget_s: float = 120.0) -> list[str]:
        missing: list[str] = []
        for c in characters:
            name = getattr(c, "name", "") or ""
            look = getattr(c, "look", "") or ""
            if not name:
                continue
            if self._present(image_url, name, look, budget_s) is False:
                missing.append(name)
        return missing

    def _present(self, image_url: str, name: str, look: str, budget_s: float):
        """True / False / None(unknown). Fail-safe: any error → None."""
        question = (
            f"Is this character clearly present in the image: {name}"
            + (f" ({look})" if look else "")
            + "? Answer with only YES or NO."
        )
        try:
            resp = self._client.run(
                self._model,
                {"image_url": image_url, "prompt": question},
                budget_s=budget_s,
                label=f"consistency-check {name}",
            )
        except ProviderError:
            return None
        text = _answer_text(resp).strip().lower()
        if not text:
            return None
        if text.startswith("no") or " no" in text[:8] or "not present" in text or "absent" in text:
            return False
        if text.startswith("yes") or "present" in text:
            return True
        return None


def _answer_text(resp) -> str:
    """Pull the answer string from a VLM response of unknown exact shape."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        for k in ("output", "answer", "text", "response", "caption", "result"):
            v = resp.get(k)
            if isinstance(v, str):
                return v
    return ""


def make_checker(client, env=None):
    """A checker when the gate is enabled, else None (default → no gate)."""
    env = env or os.environ
    if not gate_enabled(env):
        return None
    return VlmConsistencyChecker(client, model=env.get(_MODEL_ENV) or DEFAULT_VLM_MODEL)
