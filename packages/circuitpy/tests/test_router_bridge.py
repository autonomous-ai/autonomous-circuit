"""The router bridge's switch: which engine a build gets, and why."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import router_bridge, toolchain  # noqa: E402


class EngineSwitchTest(unittest.TestCase):

    def _engine(self, value, *, installed: bool) -> str:
        env = {k: v for k, v in os.environ.items() if k != router_bridge.ROUTER_ENV}
        if value is not None:
            env[router_bridge.ROUTER_ENV] = value
        jar = "/toolchain/freerouting/freerouting-2.4.1.jar" if installed else None
        java = "/toolchain/freerouting/jre/bin/java" if installed else None
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(toolchain, "freerouting_jar", return_value=jar), \
                mock.patch.object(toolchain, "java_exe", return_value=java):
            return router_bridge.engine()

    def test_unset_means_freerouting_when_the_toolchain_has_it(self):
        self.assertEqual(self._engine(None, installed=True), "freerouting")
        self.assertEqual(self._engine("", installed=True), "freerouting")

    def test_unset_means_off_without_the_jar(self):
        self.assertEqual(self._engine(None, installed=False), "off")

    def test_an_explicit_off_keeps_the_shipped_router_even_when_installed(self):
        for v in ("off", "shipped", "tscircuit", "0"):
            self.assertEqual(self._engine(v, installed=True), "off", v)

    def test_the_other_engines_are_still_selectable(self):
        self.assertEqual(self._engine("fr", installed=False), "freerouting")
        self.assertEqual(self._engine("portfolio", installed=True), "portfolio")
        self.assertEqual(self._engine("force", installed=True), "portfolio-force")
        self.assertEqual(self._engine("nonsense", installed=True), "off")


class PourResetTest(unittest.TestCase):

    def test_only_pours_with_holes_are_touched_and_counted(self):
        elements = [
            {"type": "pcb_copper_pour", "brep_shape": {"outer_ring": {"vertices": []}, "inner_rings": [{"vertices": []}]}},
            {"type": "pcb_copper_pour", "brep_shape": {"outer_ring": {"vertices": []}, "inner_rings": []}},
            {"type": "pcb_trace", "route": []},
        ]
        self.assertEqual(router_bridge._reset_pours(elements), 1)
        self.assertEqual(elements[0]["brep_shape"]["inner_rings"], [])
        self.assertEqual(router_bridge._reset_pours(elements), 0)


if __name__ == "__main__":
    unittest.main()
