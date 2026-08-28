"""A route only exists where the agent is told about it.

This repo has already paid for the other outcome twice. The catalog lives in
*two* prose tables — `circuit-analysis/SKILL.md` and the planner prompt in
`driver.mjs` — and a block that reached only one of them was invisible to
half the pipeline. Worse is the split where one surface says a capability is
sourceable and another still says "never offer wireless": the agent reads both,
believes the refusal, and the person hears no again. That is not a
half-working feature, it is a feature that looks present and is not.

So: every surface that would send an agent to refuse a missing block must also
name the one route that does not refuse it.
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

#: Each surface, and the thing it must not still say on its own.
SURFACES = (
    REPO / "skills" / "circuit-analysis" / "SKILL.md",
    REPO / "skills" / "circuitcode" / "SKILL.md",
    REPO / "viewer" / "src" / "server" / "circuit" / "driver.mjs",
)


class TheRouteIsReachable(unittest.TestCase):
    def test_every_surface_names_block_source(self):
        silent = [p.relative_to(REPO) for p in SURFACES
                  if "block-source" not in p.read_text()]
        self.assertEqual(
            [], silent,
            f"these never mention block-source: {silent}. An agent reading "
            f"only this file still believes a missing block is a dead end.",
        )

    def test_the_planner_prompt_no_longer_forbids_what_it_can_now_source(self):
        """The exact sentence that made wireless unofferable regardless."""
        text = (REPO / "viewer/src/server/circuit/driver.mjs").read_text()
        self.assertNotIn(
            "So never offer wireless, a battery, a", text,
            "the flat denylist is back: it forbids offering wireless even "
            "after a certified module has been sourced and graded.",
        )

    def test_mains_and_battery_are_still_refused_outright(self):
        """Opening one door is not opening the envelope."""
        text = (REPO / "viewer/src/server/circuit/driver.mjs").read_text()
        for phrase in ("mains is never offered", "sealed charge/protect block"):
            self.assertIn(phrase, text, f"lost the refusal: {phrase!r}")

    def test_the_skill_refuses_bare_rf_in_its_own_words(self):
        """A sourcing skill that forgot this would be the invention door."""
        text = (REPO / "skills/block-source/SKILL.md").read_text()
        for phrase in ("Bare RF silicon", "BARE_RF_PATTERNS", "mains"):
            self.assertIn(phrase, text)

    def test_the_skill_does_not_tell_anyone_to_edit_the_safety_tables(self):
        """`circuitpy.spec` and `circuitlib.safety` move together or not at
        all — the drift surfaced once already, and only by luck."""
        text = (REPO / "skills/block-source/SKILL.md").read_text()
        self.assertIn("Never edit `BARE_RF_PATTERNS`", text)


if __name__ == "__main__":
    unittest.main()
