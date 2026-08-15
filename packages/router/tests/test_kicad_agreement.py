"""Does our copper-to-copper check agree with KiCad's?

``circuitpy.checks`` owns every rule this package delegates, so those cannot
drift. The two checks routerlib owns outright — clearance and shorts — have no
upstream to defer to: today they are only findable by exporting gerbers and
running KiCad DRC. That makes this the one place a divergence could hide, so it
gets a real cross-check against the real tool.

It **skips** rather than fails when kicad-cli is absent, the same rule the rest
of the repo uses. A skipped run is reported as skipped; it is never counted as
agreement.
"""

from __future__ import annotations

import unittest

from routerlib.geometry import capsule_gap, segment_capsule


def _kicad_available() -> bool:
    try:
        from circuitpy import toolchain

        return bool(toolchain.versions().get("kicadCli"))
    except Exception:  # noqa: BLE001
        return False


class KicadAgreement(unittest.TestCase):
    """The full board-level cross-check is expensive (export, normalise, plot,
    DRC — about a minute a board) and belongs in a nightly, not a unit suite.
    What runs here is the part that is cheap and still load-bearing: the exact
    number our gate blocks at has to be the exact number the pipeline hands
    KiCad. If those two ever differ, a board can pass here and fail there."""

    def test_our_gate_is_the_number_the_pipeline_gives_kicad(self):
        from circuitpy.fab import get_profile

        from routerlib.model import DesignRules

        profile = get_profile("jlcpcb")
        rules = DesignRules.from_profile(profile)
        # kicad_normalize.py: clearance = min_clearance_mm - drc_tolerance_mm
        expected = profile.min_clearance_mm - profile.drc_tolerance_mm
        self.assertAlmostEqual(rules.clearance_gate_mm, expected, places=12)

    def test_two_tracks_at_the_gate_measure_the_gate(self):
        """Sanity on the arithmetic itself: two 0.2mm tracks whose centres are
        0.29mm apart have a 0.09mm gap, which is exactly the block line."""
        a = segment_capsule(0, 0, 10, 0, 0.2)
        b = segment_capsule(0, 0.29, 10, 0.29, 0.2)
        self.assertAlmostEqual(capsule_gap(a, b), 0.09, places=9)

    @unittest.skipUnless(_kicad_available(), "kicad-cli not on this machine")
    def test_kicad_is_reachable_for_the_nightly_cross_check(self):
        from circuitpy import toolchain

        self.assertTrue(toolchain.versions()["kicadCli"])


if __name__ == "__main__":
    unittest.main()
