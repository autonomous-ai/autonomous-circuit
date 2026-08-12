"""toolchain.py — binary resolution, env shaping, tail discipline."""

from __future__ import annotations

import os
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from circuitproj import EnvGuard  # noqa: E402

from circuitpy import toolchain  # noqa: E402


class ToolchainResolution(EnvGuard, unittest.TestCase):
    def test_repo_default_found_by_ancestor_walk(self) -> None:
        d = toolchain.toolchain_dir()
        self.assertTrue((d / "package.json").is_file())
        self.assertEqual(d.name, "toolchain")

    def test_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CIRCUIT_TOOLCHAIN"] = tmp
            self.assertEqual(toolchain.toolchain_dir(), Path(tmp).resolve())

    def test_missing_node_modules_raises_with_setup_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CIRCUIT_TOOLCHAIN"] = tmp
            with self.assertRaises(RuntimeError) as ctx:
                toolchain.node_modules_dir()
            self.assertIn("setup-toolchain.sh", str(ctx.exception))

    def test_cli_exe_exists(self) -> None:
        exe = toolchain.tscircuit_cli_exe()
        self.assertTrue(Path(exe).is_file())
        self.assertTrue(exe.endswith("tscircuit-cli"))

    def test_subprocess_env_shapes_path_and_node_path(self) -> None:
        env = toolchain.subprocess_env()
        modules = toolchain.node_modules_dir()
        self.assertTrue(env["PATH"].startswith(str(modules / ".bin")))
        self.assertEqual(env["NODE_PATH"], str(modules))

    def test_helper_js_paths(self) -> None:
        for name in ("run_all_checks.cjs", "render_review.cjs"):
            self.assertTrue(Path(toolchain.helper_js(name)).is_file())
        with self.assertRaises(RuntimeError):
            toolchain.helper_js("nope.cjs")


class ToolchainVersions(EnvGuard, unittest.TestCase):
    def test_versions_shape(self) -> None:
        versions = toolchain.versions(refresh=True)
        for key in (
            "tscircuit",
            "checks",
            "core",
            "capacityAutorouter",
            "props",
        ):
            with self.subTest(key=key):
                self.assertRegex(str(versions[key]), r"^\d+\.\d+\.\d+$")
        for key in (
            "checksBundleSha256",
            "coreBundleSha256",
            "capacityAutorouterBundleSha256",
            "propsBundleSha256",
        ):
            with self.subTest(key=key):
                self.assertRegex(str(versions[key]), r"^[0-9a-f]{64}$")
        self.assertIn("kicadCli", versions)  # None when kicad absent

    def test_versions_cached(self) -> None:
        first = toolchain.versions(refresh=True)
        second = toolchain.versions()
        self.assertEqual(first, second)


class ToolchainRun(EnvGuard, unittest.TestCase):
    def test_run_node_tail_is_bounded(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            toolchain.run_node(
                ["-e", "console.error('x'.repeat(5000)); process.exit(3)"],
                timeout=30,
            )
        message = str(ctx.exception)
        self.assertLess(len(message), 1000)
        self.assertIn("exit 3", message)

    def test_run_node_timeout_raises_timeout_error(self) -> None:
        with self.assertRaises(TimeoutError):
            toolchain.run_node(["-e", "setTimeout(()=>{}, 60000)"], timeout=0.5)

    def test_timeout_kills_the_cli_process_tree(self) -> None:
        import time

        with tempfile.TemporaryDirectory(prefix="toolchain-timeout-") as tmp:
            marker = Path(tmp) / "orphan-child-wrote-after-timeout"
            child = (
                "const {spawn}=require('node:child_process');"
                "spawn(process.execPath,['-e',"
                + json.dumps(
                    "setTimeout(()=>require('node:fs').writeFileSync("
                    + json.dumps(str(marker))
                    + ", 'leaked'), 700)"
                )
                + "],{stdio:'ignore'});setTimeout(()=>{},60000)"
            )
            with self.assertRaises(TimeoutError):
                toolchain.run_node(["-e", child], timeout=0.2)
            time.sleep(1.0)
            self.assertFalse(
                marker.exists(),
                "a router descendant survived after circuitpy timed out",
            )

    def test_run_cli_check_false_returns_result(self) -> None:
        result = toolchain.run_cli(["--help"], cwd=Path.cwd(), timeout=60, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("build", result.output)

    def test_kicad_exe_probe_never_raises(self) -> None:
        exe = toolchain.kicad_cli_exe()
        self.assertTrue(exe is None or Path(exe).is_file())


if __name__ == "__main__":
    unittest.main()
