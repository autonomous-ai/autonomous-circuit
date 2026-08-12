"""Regressions for tscircuit's local autorouter cache identity.

The pinned core once hashed only SimpleRouteJson. Building Pipeline7 and then
Pipeline9 in one directory returned Pipeline7 copper for both, even though a
cold Pipeline9 route was different. This fixture permanently proves that a
strategy change reaches the solver rather than a stale cache entry.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from circuitpy import toolchain  # noqa: E402


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "router-cache-fixture.tsx"


def _write_case(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FIXTURE, root / "fixture.tsx")
    (root / "package.json").write_text(
        '{"name":"router-cache-regression","private":true}\n', encoding="utf-8"
    )
    for version, filename in (
        ("beta_pipeline7", "pipeline7.tsx"),
        ("beta_pipeline9", "pipeline9.tsx"),
    ):
        (root / filename).write_text(
            'import { RouterCacheFixture } from "./fixture"\n'
            f'export default () => <RouterCacheFixture version="{version}" />\n',
            encoding="utf-8",
        )
    for effort, filename in (("1x", "effort1.tsx"), ("5x", "effort5.tsx")):
        (root / filename).write_text(
            'import { RouterCacheFixture } from "./fixture"\n'
            f'export default () => <RouterCacheFixture version="beta_pipeline7" effort="{effort}" />\n',
            encoding="utf-8",
        )


def _build(root: Path, stem: str) -> list[dict]:
    result = toolchain.run_cli(
        ["build", f"{stem}.tsx", "--disable-parts-engine"],
        cwd=root,
        timeout=180,
        check=False,
    )
    output = root / "dist" / stem / "circuit.json"
    if not output.is_file():
        raise AssertionError(
            f"tscircuit wrote no {output}; output tail: {result.output[-800:]}"
        )
    elements = json.loads(output.read_text(encoding="utf-8"))
    errors = [
        element
        for element in elements
        if isinstance(element, dict) and str(element.get("type", "")).endswith("_error")
    ]
    if errors:
        raise AssertionError(
            f"{stem} fixture has parsed errors: {[e.get('type') for e in errors]}"
        )
    return elements


def _copper_fingerprint(elements: list[dict]) -> str:
    copper = []
    for element in elements:
        if element.get("type") == "pcb_trace":
            copper.append(
                {
                    "type": "pcb_trace",
                    "source_trace_id": element.get("source_trace_id"),
                    "route": element.get("route"),
                }
            )
        elif element.get("type") == "pcb_via":
            copper.append(
                {
                    "type": "pcb_via",
                    "x": element.get("x"),
                    "y": element.get("y"),
                    "layers": element.get("layers"),
                }
            )
    copper.sort(key=lambda item: json.dumps(item, sort_keys=True))
    payload = json.dumps(copper, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RouterCacheIdentity(unittest.TestCase):
    def test_pipeline_change_cannot_reuse_previous_copper(self) -> None:
        try:
            toolchain.tscircuit_cli_exe()
        except RuntimeError as exc:  # pragma: no cover - developer without install
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory(prefix="router-cache-shared-") as shared_tmp:
            shared = Path(shared_tmp)
            _write_case(shared)
            pipeline7 = _build(shared, "pipeline7")
            pipeline9_after_pipeline7 = _build(shared, "pipeline9")

            with tempfile.TemporaryDirectory(prefix="router-cache-cold-") as cold_tmp:
                cold = Path(cold_tmp)
                _write_case(cold)
                cold_pipeline9 = _build(cold, "pipeline9")

            p7_hash = _copper_fingerprint(pipeline7)
            shared_p9_hash = _copper_fingerprint(pipeline9_after_pipeline7)
            cold_p9_hash = _copper_fingerprint(cold_pipeline9)
            self.assertNotEqual(p7_hash, shared_p9_hash)
            self.assertEqual(shared_p9_hash, cold_p9_hash)

            route_cache = shared / ".tscircuit" / "cache"
            self.assertTrue(route_cache.is_dir())
            # Both strategies have durable entries instead of one colliding
            # SRJ-only entry. Capacity's internal caches may add more files.
            self.assertGreaterEqual(len(list(route_cache.glob("*.json"))), 2)

    def test_effort_change_is_a_cold_configuration_keyed_comparison(self) -> None:
        """A nominal 5x retry may not receive the preceding 1x copper.

        This deliberately makes no claim that 5x is better. It proves the
        prerequisite for any future performance claim: identical source is
        compared in a shared directory and a fresh directory, and the 5x
        artifact is invariant to whether the 1x build ran first.
        """

        try:
            toolchain.tscircuit_cli_exe()
        except RuntimeError as exc:  # pragma: no cover - developer without install
            self.skipTest(str(exc))

        with tempfile.TemporaryDirectory(prefix="router-effort-shared-") as shared_tmp:
            shared = Path(shared_tmp)
            _write_case(shared)
            effort1 = _build(shared, "effort1")
            effort5_after_effort1 = _build(shared, "effort5")

            with tempfile.TemporaryDirectory(prefix="router-effort-cold-") as cold_tmp:
                cold = Path(cold_tmp)
                _write_case(cold)
                cold_effort5 = _build(cold, "effort5")

            shared_5x_hash = _copper_fingerprint(effort5_after_effort1)
            cold_5x_hash = _copper_fingerprint(cold_effort5)
            self.assertEqual(shared_5x_hash, cold_5x_hash)

            route_cache = shared / ".tscircuit" / "cache"
            self.assertTrue(route_cache.is_dir())
            self.assertGreaterEqual(
                len(list(route_cache.glob("*.json"))),
                2,
                "1x and 5x must occupy distinct whole-phase cache entries",
            )

            # Record both artifacts in the assertion path even when this easy
            # fixture legitimately produces identical copper at both efforts.
            self.assertTrue(_copper_fingerprint(effort1))
            self.assertTrue(shared_5x_hash)


if __name__ == "__main__":
    unittest.main()
