"""Golden-block testbench harness.

Builds each ``testbench/<id>.tsx`` board with the REAL pinned toolchain
(``toolchain/node_modules/.bin/tscircuit-cli`` — never ``npx tsci``, an
unrelated package) into a session tempdir, parses the produced
``circuit.json`` (never the exit code — the CLI exits 0 even with real
errors), and hands tests a small graph API for topology assertions.

Offline discipline: every build runs ``--disable-parts-engine`` — parts are
pinned in the blocks via ``supplierPartNumbers``, so no network is needed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
GB_ROOT = TESTS_DIR.parent                 # packages/golden-blocks
REPO_ROOT = GB_ROOT.parents[1]
SNAPSHOT_DIR = TESTS_DIR / "snapshots"

BUILD_TIMEOUT_S = 300.0

#: Blocks the full-gauntlet suite covers (test_gauntlet.py). Same list as
#: test_blocks.BLOCK_IDS, kept here so the gauntlet does not import the other
#: test module.
BLOCK_IDS_FOR_GAUNTLET = [
    "usb-c-power",
    "ldo-3v3",
    "status-led",
    "sw-tact",
    "i2c-bus",
    "rp2040-core",
    "usb-c-data",
    "sensor-bme280",
    "ws2812-chain",
]


def _toolchain_bin_dir() -> Path:
    """env CIRCUIT_TOOLCHAIN > repo default (contract §1 Toolchain)."""
    override = os.environ.get("CIRCUIT_TOOLCHAIN", "").strip()
    root = Path(override) if override else REPO_ROOT / "toolchain"
    return root / "node_modules" / ".bin"


def pytest_collection_modifyitems(config, items):
    if not (_toolchain_bin_dir() / "tscircuit-cli").exists():
        skip = pytest.mark.skip(
            reason="pinned toolchain missing — run scripts/setup-toolchain.sh"
        )
        for item in items:
            item.add_marker(skip)


class BuildFarm:
    """Copies blocks/ + testbench/ into a tempdir and builds each bench
    once per session."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="golden-blocks-bench-"))
        shutil.copytree(GB_ROOT / "blocks", self.root / "blocks")
        shutil.copytree(GB_ROOT / "testbench", self.root / "testbench")
        self._built: dict[str, list[dict]] = {}

    def circuit_json(self, bench_id: str) -> list[dict]:
        if bench_id not in self._built:
            self._built[bench_id] = self._build(bench_id)
        return self._built[bench_id]

    def _build(self, bench_id: str) -> list[dict]:
        source = self.root / "testbench" / f"{bench_id}.tsx"
        assert source.exists(), f"no testbench for block {bench_id!r}"
        bin_dir = _toolchain_bin_dir()
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}" + env.get("PATH", "")
        # The tscircuit-cli shim silently exits 0 when its runner (tsx/bun)
        # is missing from PATH — the artifact gate below catches that too.
        # Relative source path with cwd at the workspace root — the CLI
        # mirrors the source path under dist/ (an absolute path makes it
        # write somewhere else entirely).
        proc = subprocess.run(
            [str(bin_dir / "tscircuit-cli"), "build",
             f"testbench/{bench_id}.tsx", "--disable-parts-engine"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_S,
        )
        out_path = self.root / "dist" / "testbench" / bench_id / "circuit.json"
        assert out_path.exists(), (
            f"{bench_id}: no circuit.json produced "
            f"(exit={proc.returncode})\nstdout: {proc.stdout[-1500:]}\n"
            f"stderr: {proc.stderr[-1500:]}"
        )
        return json.loads(out_path.read_text())

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


@pytest.fixture(scope="session")
def farm():
    f = BuildFarm()
    try:
        yield f
    finally:
        f.cleanup()


class CircuitGraph:
    """Netlist-level view over a built circuit.json."""

    def __init__(self, elements: list[dict]) -> None:
        self.elements = elements
        self.components = {
            e["name"]: e for e in elements if e["type"] == "source_component"
        }
        self.nets = {
            e["name"]: e for e in elements if e["type"] == "source_net"
        }
        self.ports = [e for e in elements if e["type"] == "source_port"]
        self._id_to_component = {
            e["source_component_id"]: e["name"]
            for e in elements
            if e["type"] == "source_component"
        }
        # Union-find over port ids + net ids via source_trace membership,
        # plus declared component-internal ties (`internallyConnectedPins` →
        # source_component_internal_connection). A switch's two same-terminal
        # pads are permanently bonded inside the part, so they are one
        # electrical group whether or not board copper visits both — which is
        # exactly the diagonal-wiring shape sw-tact ships (ledger #29).
        self._parent: dict[str, str] = {}
        for e in elements:
            if e["type"] == "source_trace":
                members = list(e.get("connected_source_port_ids") or []) + [
                    f"net:{nid}" for nid in (e.get("connected_source_net_ids") or [])
                ]
            elif e["type"] == "source_component_internal_connection":
                members = list(e.get("source_port_ids") or [])
            else:
                continue
            for m in members[1:]:
                self._union(members[0], m)

    # -- union-find -------------------------------------------------------
    def _find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def _union(self, a: str, b: str) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[ra] = rb

    # -- resolution -------------------------------------------------------
    def port_id(self, ref: str) -> str:
        """``"J1.VBUS1"`` → source_port_id (name or any port hint)."""
        comp_name, pin = ref.split(".", 1)
        comp = self.components.get(comp_name)
        assert comp is not None, f"no component {comp_name!r}"
        cid = comp["source_component_id"]
        for p in self.ports:
            if p.get("source_component_id") != cid:
                continue
            if p.get("name") == pin or pin in (p.get("port_hints") or []):
                return p["source_port_id"]
        raise AssertionError(f"no port {pin!r} on {comp_name!r}")

    def net_key(self, name: str) -> str:
        net = self.nets.get(name)
        assert net is not None, f"no net {name!r} (have {sorted(self.nets)})"
        return f"net:{net['source_net_id']}"

    def connected(self, a: str, b: str) -> bool:
        """``connected("J1.VBUS1", "net.V5")`` — same electrical group?"""
        def key(ref: str) -> str:
            if ref.startswith("net."):
                return self.net_key(ref[4:])
            return self.port_id(ref)
        return self._find(key(a)) == self._find(key(b))

    # -- inventories ------------------------------------------------------
    def errors(self) -> list[dict]:
        return [e for e in self.elements if e["type"].endswith("_error")]

    def warnings(self) -> list[dict]:
        return [e for e in self.elements if e["type"].endswith("_warning")]

    def lcsc(self) -> dict[str, str]:
        """refdes → pinned LCSC number (first jlcpcb supplier number)."""
        out = {}
        for name, comp in self.components.items():
            nums = (comp.get("supplier_part_numbers") or {}).get("jlcpcb") or []
            if nums:
                out[name] = nums[0]
        return out

    def summary(self) -> dict:
        """Stable snapshot summary (element counts + netlist identity)."""
        from collections import Counter
        return {
            "components": {
                name: {
                    "ftype": comp.get("ftype"),
                    "lcsc": (comp.get("supplier_part_numbers") or {})
                    .get("jlcpcb", [None])[0],
                }
                for name, comp in sorted(self.components.items())
            },
            "nets": sorted(self.nets),
            "traceCount": sum(
                1 for e in self.elements if e["type"] == "source_trace"
            ),
            "elementCounts": dict(sorted(
                Counter(e["type"] for e in self.elements).items()
            )),
        }


@pytest.fixture(scope="session")
def graph(farm):
    cache: dict[str, CircuitGraph] = {}

    def get(bench_id: str) -> CircuitGraph:
        if bench_id not in cache:
            cache[bench_id] = CircuitGraph(farm.circuit_json(bench_id))
        return cache[bench_id]

    return get
