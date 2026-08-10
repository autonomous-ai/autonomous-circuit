"""The seeded-defect sentinel — if this board passes, the eval went blind.

Builds a deliberately broken board (trace to a nonexistent port + two
parts placed on top of each other) with the real toolchain and asserts the
error elements ACTUALLY appear in circuit.json. This guards the whole
"parse artifacts, never exit codes" gauntlet: the CLI exits 0 on these
defects (verified 2026-08-10), so a scanner that stopped finding them
would silently pass everything.
"""

from __future__ import annotations

SENTINEL_TSX = """
export default () => (
  <board width="20mm" height="20mm" thickness="1.6mm">
    <resistor name="R1" resistance="1k" footprint="0805" pcbX={0} pcbY={0} />
    <resistor name="R2" resistance="1k" footprint="0805" pcbX={0.2} pcbY={0} />
    <trace name="T_bad" from=".R1 > .pin3" to="net.GND" />
    <trace name="T_ok" from=".R1 > .pin1" to=".R2 > .pin1" />
  </board>
)
"""


def test_sentinel_trips(farm):
    bench_path = farm.root / "testbench" / "_sentinel.tsx"
    bench_path.write_text(SENTINEL_TSX)
    elements = farm.circuit_json("_sentinel")
    kinds = {e["type"] for e in elements if e["type"].endswith("_error")}
    assert "source_trace_not_connected_error" in kinds, (
        f"sentinel bad-port defect not detected — eval went blind (got {kinds})"
    )
    overlap_kinds = {
        "pcb_footprint_overlap_error",
        "pcb_pad_pad_clearance_error",
        "pcb_courtyard_overlap_error",
    }
    assert kinds & overlap_kinds, (
        f"sentinel overlap defect not detected — eval went blind (got {kinds})"
    )
