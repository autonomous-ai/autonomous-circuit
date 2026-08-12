"""export_cache.py — key identity + store/lookup roundtrip."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import circuitproj  # noqa: E402  (sys.path bootstrap)

from circuitpy import export_cache  # noqa: E402

VERSIONS = {
    "tscircuit": "0.0.2279",
    "checks": "0.0.152",
    "checksBundleSha256": "c" * 64,
    "coreBundleSha256": "a" * 64,
    "kicadCli": None,
}
REVISION = "pipeline-a"


class Keys(unittest.TestCase):
    def test_key_is_stable(self) -> None:
        a = export_cache.export_key(
            circuit_json_sha="abc", kind="gerbers", versions=VERSIONS, fab="jlcpcb",
            pipeline_revision=REVISION,
        )
        b = export_cache.export_key(
            circuit_json_sha="abc", kind="gerbers", versions=dict(VERSIONS), fab="jlcpcb",
            pipeline_revision=REVISION,
        )
        self.assertEqual(a, b)

    def test_every_input_changes_the_key(self) -> None:
        base = export_cache.export_key(
            circuit_json_sha="abc", kind="gerbers", versions=VERSIONS, fab="jlcpcb",
            pipeline_revision=REVISION,
        )
        variants = [
            export_cache.export_key(
                circuit_json_sha="abd", kind="gerbers", versions=VERSIONS, fab="jlcpcb",
                pipeline_revision=REVISION,
            ),
            export_cache.export_key(
                circuit_json_sha="abc", kind="glb", versions=VERSIONS, fab="jlcpcb",
                pipeline_revision=REVISION,
            ),
            export_cache.export_key(
                circuit_json_sha="abc",
                kind="gerbers",
                versions={**VERSIONS, "tscircuit": "0.0.9999"},
                fab="jlcpcb",
                pipeline_revision=REVISION,
            ),
            export_cache.export_key(
                circuit_json_sha="abc",
                kind="gerbers",
                versions={**VERSIONS, "checksBundleSha256": "d" * 64},
                fab="jlcpcb",
                pipeline_revision=REVISION,
            ),
            export_cache.export_key(
                circuit_json_sha="abc",
                kind="gerbers",
                versions={**VERSIONS, "coreBundleSha256": "b" * 64},
                fab="jlcpcb",
                pipeline_revision=REVISION,
            ),
            export_cache.export_key(
                circuit_json_sha="abc", kind="gerbers", versions=VERSIONS, fab="other",
                pipeline_revision=REVISION,
            ),
            export_cache.export_key(
                circuit_json_sha="abc", kind="gerbers", versions=VERSIONS, fab="jlcpcb",
                pipeline_revision="pipeline-b",
            ),
        ]
        self.assertEqual(len({base, *variants}), 8)


class StoreLookup(unittest.TestCase):
    def test_roundtrip_and_empty_files_never_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact.zip"
            artifact.write_bytes(b"payload")
            key = "k" * 64
            self.assertIsNone(export_cache.lookup(root, key, ".zip"))
            stored = export_cache.store(root, key, ".zip", artifact)
            self.assertIsNotNone(stored)
            hit = export_cache.lookup(root, key, ".zip")
            self.assertIsNotNone(hit)
            self.assertEqual(hit.read_bytes(), b"payload")  # type: ignore[union-attr]
            # cache lives under .circuit/export-cache/
            self.assertIn(".circuit", str(hit))
            hit.write_bytes(b"")  # type: ignore[union-attr]
            self.assertIsNone(export_cache.lookup(root, key, ".zip"))

    def test_sha256_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f"
            path.write_bytes(b"hello")
            self.assertEqual(
                export_cache.sha256_file(path),
                "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
            )


if __name__ == "__main__":
    unittest.main()
