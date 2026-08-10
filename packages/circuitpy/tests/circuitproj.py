"""Shared scaffolding for circuitpy tests: real projects on disk, real builds
with the pinned toolchain, NO mocks of circuitpy itself (donor test
discipline). Each test module carries its own sys.path bootstrap header;
this module repeats it so files also work standalone."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Env that could redirect toolchain/fab selection or force regeneration —
# popped for every test so runs are hermetic. CIRCUIT_PARTS_ENGINE stays
# (conftest pins it off suite-wide).
ENV_KEYS = (
    "CIRCUIT_TOOLCHAIN",
    "CIRCUIT_FAB",
    "CIRCUIT_FORCE_REGEN",
    "CIRCUIT_WALL_CLOCK_S",
)


class EnvGuard:
    """unittest mixin: isolate pipeline-related environment per test."""

    def setUp(self) -> None:  # type: ignore[override]
        super().setUp()  # type: ignore[misc]
        self._saved_env = {
            key: os.environ.pop(key) for key in ENV_KEYS if key in os.environ
        }

    def tearDown(self) -> None:  # type: ignore[override]
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(self._saved_env)
        super().tearDown()  # type: ignore[misc]


def load_fixture(name: str) -> list:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Board sources (adapted from the verified R2 bench boards).
# --------------------------------------------------------------------------

GOOD_TSX = """export default () => (
  <board width="20mm" height="20mm" thickness={1.6}>
    <resistor name="R1" resistance="1k" footprint="0402" pcbX={-5} pcbY={0} />
    <led name="LED1" footprint="0402" pcbX={5} pcbY={0} />
    <trace name="T1" from=".R1 > .pin2" to=".LED1 > .anode" />
    <trace name="T2" from=".LED1 > .cathode" to="net.GND" />
    <trace name="T3" from=".R1 > .pin1" to="net.VCC" />
  </board>
)
"""

BAD_PORT_TSX = """export default () => (
  <board width="20mm" height="20mm" thickness={1.6}>
    <resistor name="R1" resistance="1k" footprint="0402" pcbX={-5} pcbY={0} />
    <led name="LED1" footprint="0402" pcbX={5} pcbY={0} />
    <trace name="T1" from=".R1 > .pin3" to=".LED1 > .anode" />
    <trace name="T2" from=".LED1 > .cathode" to="net.GND" />
  </board>
)
"""

COMPILE_ERROR_TSX = """export default () => (
  <board width="20mm" height="20mm"
    this is not valid TSX at all
"""

BLOCK_IMPORT_TSX = """import { LedIndicator } from "../blocks/led-indicator"

export default () => (
  <board width="20mm" height="20mm" thickness={1.6}>
    <LedIndicator name="LED_BLOCK" pcbX={0} pcbY={0} />
  </board>
)
"""

LED_BLOCK_TSX = """export const LedIndicator = (props: { name: string; pcbX: number; pcbY: number }) => (
  <group>
    <resistor name={`${props.name}_R`} resistance="1k" footprint="0402"
      pcbX={props.pcbX - 3} pcbY={props.pcbY} />
    <led name={`${props.name}_D`} footprint="0402"
      pcbX={props.pcbX + 3} pcbY={props.pcbY} />
    <trace from={`.${props.name}_R > .pin2`} to={`.${props.name}_D > .anode`} />
  </group>
)
"""

# Project-skeleton config files. NOTE for the skills track: workspace projects
# have no node_modules — TSX eval needs these two files next to boards/
# (jsx react-jsx + the tscircuit types); the project_skeleton must match.
TSCONFIG_JSON = """{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "jsx": "react-jsx",
    "moduleResolution": "node",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "allowSyntheticDefaultImports": true,
    "types": ["tscircuit"]
  },
  "include": ["**/*.ts", "**/*.tsx"],
  "exclude": ["node_modules", "dist", ".circuit", ".claude"]
}
"""

TSCIRCUIT_CONFIG_JSON = """{
  "$schema": "https://cdn.jsdelivr.net/npm/@tscircuit/cli/types/tscircuit.config.schema.json"
}
"""


def product_dict(
    *,
    name: str = "test-board",
    description: str = "circuitpy test board",
    power: str = "usb-c-5v",
    envelope_mm: tuple[float, float] | None = (60.0, 40.0),
    layers: int = 2,
    fab: str = "jlcpcb",
    assembly: bool = True,
) -> dict:
    payload: dict = {
        "name": name,
        "description": description,
        "power": power,
        "layers": layers,
        "fab": fab,
        "assembly": assembly,
    }
    if envelope_mm is not None:
        payload["envelopeMm"] = list(envelope_mm)
    return payload


def write_project(
    root: Path,
    *,
    tsx: str = GOOD_TSX,
    product: dict | None = None,
    parts: dict | None = None,
    blocks: dict[str, str] | None = None,
) -> Path:
    """Write a real contract-shaped project: product.json (+ optional
    parts.json), boards/main.tsx, optional blocks/, and the skeleton config
    files TSX eval needs. Returns the project root."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "product.json").write_text(
        json.dumps(product if product is not None else product_dict(), indent=2),
        encoding="utf-8",
    )
    if parts is not None:
        (root / "parts.json").write_text(json.dumps(parts, indent=2), encoding="utf-8")
    boards = root / "boards"
    boards.mkdir(parents=True, exist_ok=True)
    (boards / "main.tsx").write_text(tsx, encoding="utf-8")
    for rel, source in (blocks or {}).items():
        block_path = root / "blocks" / rel
        block_path.parent.mkdir(parents=True, exist_ok=True)
        block_path.write_text(source, encoding="utf-8")
    (root / "tsconfig.json").write_text(TSCONFIG_JSON, encoding="utf-8")
    (root / "tscircuit.config.json").write_text(TSCIRCUIT_CONFIG_JSON, encoding="utf-8")
    return root


def kicad_available() -> bool:
    from circuitpy import toolchain

    return toolchain.kicad_cli_exe() is not None
