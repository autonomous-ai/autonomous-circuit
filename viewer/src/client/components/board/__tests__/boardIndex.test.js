import assert from "node:assert/strict";
import test from "node:test";

import {
  boxIsReal,
  buildBoardIndex,
  distance,
  elementId,
  emptyBoardIndex,
  hitTestPcb,
  inflateBox,
  pcbElementBox,
  pcbElementContains,
  resolveSelection,
  schematicElementBox,
  traceLength,
  unionBox,
} from "../../../lib/boardIndex.js";

// A miniature but structurally faithful board: two parts (R1, LED1) wired
// through one trace, a GND net reaching both, and one via. Same element shapes
// and the same id/connectivity conventions the real pipeline emits — see
// packages/circuitpy/tests/fixtures/good.circuit.json.
const NET_SIG = "sub_connectivity_net0";
const NET_GND = "sub_connectivity_net1";

function fixture() {
  return [
    { type: "pcb_board", pcb_board_id: "pcb_board_0", center: { x: 0, y: 0 }, width: 20, height: 10, thickness: 1.6, num_layers: 2 },

    { type: "source_component", source_component_id: "sc_r1", name: "R1", ftype: "simple_resistor", display_resistance: "1kΩ", supplier_part_numbers: { jlcpcb: ["C25905"] } },
    { type: "source_component", source_component_id: "sc_led1", name: "LED1", ftype: "simple_led", supplier_part_numbers: { jlcpcb: ["C2297"] } },

    { type: "source_net", source_net_id: "snet_gnd", name: "GND", is_ground: true, is_power: false, subcircuit_connectivity_map_key: NET_GND },
    { type: "source_trace", source_trace_id: "st_sig", name: "TR_R1_LED", subcircuit_connectivity_map_key: NET_SIG },

    { type: "source_port", source_port_id: "sp_r1_1", source_component_id: "sc_r1", pin_number: 1, subcircuit_connectivity_map_key: NET_SIG },
    { type: "source_port", source_port_id: "sp_r1_2", source_component_id: "sc_r1", pin_number: 2, subcircuit_connectivity_map_key: NET_GND },
    { type: "source_port", source_port_id: "sp_led_1", source_component_id: "sc_led1", pin_number: 1, subcircuit_connectivity_map_key: NET_SIG },
    { type: "source_port", source_port_id: "sp_led_2", source_component_id: "sc_led1", pin_number: 2, subcircuit_connectivity_map_key: NET_GND },

    { type: "schematic_component", schematic_component_id: "schc_r1", source_component_id: "sc_r1", center: { x: -2, y: 0 }, size: { width: 1, height: 0.5 } },
    { type: "schematic_component", schematic_component_id: "schc_led1", source_component_id: "sc_led1", center: { x: 2, y: 0 }, size: { width: 1, height: 0.5 } },
    { type: "schematic_port", schematic_port_id: "schp_r1_1", schematic_component_id: "schc_r1", source_port_id: "sp_r1_1", center: { x: -1.5, y: 0 } },
    { type: "schematic_trace", schematic_trace_id: "scht_sig", source_trace_id: "st_sig", subcircuit_connectivity_map_key: NET_SIG, edges: [{ from: { x: -1.5, y: 0 }, to: { x: 1.5, y: 0 } }] },
    { type: "schematic_net_label", schematic_net_label_id: "schl_gnd", source_net_id: "snet_gnd", text: "GND", center: { x: 0, y: -1 }, anchor_position: { x: 0, y: -1 } },

    { type: "pcb_component", pcb_component_id: "pcbc_r1", source_component_id: "sc_r1", center: { x: -5, y: 0 }, width: 1.6, height: 0.8, layer: "top", rotation: 0 },
    { type: "pcb_component", pcb_component_id: "pcbc_led1", source_component_id: "sc_led1", center: { x: 5, y: 0 }, width: 1.6, height: 0.8, layer: "top", rotation: 0 },

    { type: "pcb_port", pcb_port_id: "pcbp_r1_1", pcb_component_id: "pcbc_r1", source_port_id: "sp_r1_1", x: -4.5, y: 0 },
    { type: "pcb_port", pcb_port_id: "pcbp_r1_2", pcb_component_id: "pcbc_r1", source_port_id: "sp_r1_2", x: -5.5, y: 0 },
    { type: "pcb_port", pcb_port_id: "pcbp_led_1", pcb_component_id: "pcbc_led1", source_port_id: "sp_led_1", x: 4.5, y: 0 },
    { type: "pcb_port", pcb_port_id: "pcbp_led_2", pcb_component_id: "pcbc_led1", source_port_id: "sp_led_2", x: 5.5, y: 0 },

    { type: "pcb_smtpad", pcb_smtpad_id: "pad_r1_1", pcb_component_id: "pcbc_r1", pcb_port_id: "pcbp_r1_1", layer: "top", shape: "rect", width: 0.5, height: 0.6, x: -4.5, y: 0 },
    { type: "pcb_smtpad", pcb_smtpad_id: "pad_r1_2", pcb_component_id: "pcbc_r1", pcb_port_id: "pcbp_r1_2", layer: "top", shape: "rect", width: 0.5, height: 0.6, x: -5.5, y: 0 },
    { type: "pcb_smtpad", pcb_smtpad_id: "pad_led_1", pcb_component_id: "pcbc_led1", pcb_port_id: "pcbp_led_1", layer: "top", shape: "rect", width: 0.5, height: 0.6, x: 4.5, y: 0 },
    { type: "pcb_smtpad", pcb_smtpad_id: "pad_led_2", pcb_component_id: "pcbc_led1", pcb_port_id: "pcbp_led_2", layer: "top", shape: "rect", width: 0.5, height: 0.6, x: 5.5, y: 0 },

    { type: "pcb_silkscreen_text", pcb_silkscreen_text_id: "silk_r1", pcb_component_id: "pcbc_r1", layer: "top", text: "R1", font_size: 0.4, anchor_alignment: "center", anchor_position: { x: -5, y: 0.9 } },

    // A signal trace that drops from top to bottom halfway across.
    {
      type: "pcb_trace",
      pcb_trace_id: "trace_sig",
      source_trace_id: "st_sig",
      connectsTo: ["pcbp_r1_1", "pcbp_led_1"],
      route: [
        { route_type: "wire", x: -4.5, y: 0, width: 0.2, layer: "top" },
        { route_type: "wire", x: 0, y: 0, width: 0.2, layer: "top" },
        { route_type: "wire", x: 0, y: 0, width: 0.2, layer: "bottom" },
        { route_type: "wire", x: 4.5, y: 0, width: 0.2, layer: "bottom" },
      ],
    },
    { type: "pcb_via", pcb_via_id: "via_0", x: 0, y: 0, hole_diameter: 0.3, outer_diameter: 0.6, layers: ["top", "bottom"], subcircuit_connectivity_map_key: NET_SIG },

    { type: "pcb_footprint_overlap_error", pcb_footprint_overlap_error_id: "err_0", message: "pads overlap", pcb_smtpad_ids: ["pad_r1_1", "pad_led_1"] },
  ];
}

test("elementId reads the conventional <type>_id field", () => {
  assert.equal(elementId({ type: "pcb_via", pcb_via_id: "via_0" }), "via_0");
  assert.equal(elementId({ type: "pcb_via" }), "");
  assert.equal(elementId(null), "");
});

test("buildBoardIndex accepts the bare array and the {elements} wrapper", () => {
  const fromArray = buildBoardIndex(fixture());
  const fromObject = buildBoardIndex({ elements: fixture() });
  assert.equal(fromArray.stats.elements, fromObject.stats.elements);
  assert.equal(emptyBoardIndex().stats.elements, 0);
  assert.equal(buildBoardIndex("nonsense").stats.elements, 0);
});

test("components join the schematic and pcb sides of the same part", () => {
  const index = buildBoardIndex(fixture());
  assert.equal(index.components.length, 2);
  const r1 = index.componentByRefdes.get("R1");
  assert.ok(r1);
  assert.equal(r1.schematicId, "schc_r1");
  assert.equal(r1.pcbId, "pcbc_r1");
  assert.equal(r1.lcsc, "C25905");
  assert.equal(r1.value, "1kΩ");
  assert.equal(r1.pads, 2);
  // The box grows past the body to take in the pads and the silkscreen.
  assert.ok(r1.pcbBox.maxY >= 0.9, "silkscreen text is inside the component box");
  assert.ok(r1.pcbBox.minX <= -5.75, "pads are inside the component box");
  assert.ok(boxIsReal(r1.schematicBox));
});

test("nets resolve through one hop for the elements that carry no connectivity key", () => {
  const index = buildBoardIndex(fixture());
  // pcb_trace has no key of its own — it must resolve via source_trace.
  assert.equal(index.netKeyByElementId.get("trace_sig"), NET_SIG);
  // pcb_smtpad resolves pcb_port → source_port.
  assert.equal(index.netKeyByElementId.get("pad_r1_1"), NET_SIG);
  assert.equal(index.netKeyByElementId.get("pad_r1_2"), NET_GND);
  // schematic_net_label resolves via source_net.
  assert.equal(index.netKeyByElementId.get("schl_gnd"), NET_GND);

  const gnd = index.netByName.get("GND");
  assert.ok(gnd, "the named source_net wins the net name");
  assert.equal(gnd.isGround, true);
  assert.equal(gnd.pinCount, 2);

  const signal = index.netByName.get("TR_R1_LED");
  assert.ok(signal, "an unnamed net borrows the source_trace display name");
  assert.equal(signal.pinCount, 2);
  assert.ok(signal.lengthMm > 8.9 && signal.lengthMm < 9.1, `routed length ${signal.lengthMm}`);
  assert.deepEqual([...signal.componentKeys].sort(), ["sc_led1", "sc_r1"]);
});

test("an unnamed net still gets a stable handle", () => {
  const elements = fixture().filter((element) => element.type !== "source_net" && element.type !== "source_trace");
  const index = buildBoardIndex(elements);
  const names = index.nets.map((net) => net.name);
  assert.ok(names.every(Boolean), `every net is named: ${names.join(",")}`);
  assert.ok(names.some((name) => name.startsWith("N$")));
});

test("resolveSelection lights up both domains for a component", () => {
  const index = buildBoardIndex(fixture());
  const r1 = index.componentByRefdes.get("R1");
  const selection = resolveSelection(index, { kind: "component", key: r1.key });
  assert.ok(selection.pcbIds.has("pad_r1_1"));
  assert.ok(selection.pcbIds.has("silk_r1"));
  assert.ok(selection.pcbIds.has("pcbc_r1"));
  assert.ok(selection.schematicIds.has("schc_r1"));
  assert.deepEqual([...selection.refdes], ["R1"]);
  assert.ok(boxIsReal(selection.pcbBox));
});

test("resolveSelection lights up both domains for a net, and names its parts", () => {
  const index = buildBoardIndex(fixture());
  const signal = index.netByName.get("TR_R1_LED");
  const selection = resolveSelection(index, { kind: "net", key: signal.key });
  assert.ok(selection.pcbIds.has("trace_sig"));
  assert.ok(selection.pcbIds.has("via_0"));
  assert.ok(selection.pcbIds.has("pad_r1_1"));
  assert.ok(!selection.pcbIds.has("pad_r1_2"), "the GND pad is NOT on the signal net");
  assert.ok(selection.schematicIds.has("scht_sig"));
  assert.deepEqual([...selection.refdes].sort(), ["LED1", "R1"]);
});

test("resolveSelection accepts a refdes or a net name as the key", () => {
  const index = buildBoardIndex(fixture());
  assert.ok(resolveSelection(index, { kind: "component", key: "R1" }).pcbIds.size > 0);
  assert.ok(resolveSelection(index, { kind: "net", key: "GND" }).pcbIds.size > 0);
  assert.equal(resolveSelection(index, null).pcbIds.size, 0);
  assert.equal(resolveSelection(index, { kind: "component", key: "NOPE" }).pcbIds.size, 0);
});

test("hit testing prefers pads over traces and uses real stroke geometry", () => {
  const index = buildBoardIndex(fixture());
  // Dead centre of R1's first pad, with the trace also passing through it.
  const onPad = hitTestPcb(index, -4.5, 0);
  assert.equal(onPad.element.type, "pcb_smtpad");
  assert.equal(onPad.componentKey, "sc_r1");
  assert.equal(onPad.netKey, NET_SIG);

  // On the trace between the parts, away from any pad.
  const onTrace = hitTestPcb(index, -2, 0);
  assert.equal(onTrace.element.type, "pcb_trace");
  assert.equal(onTrace.netKey, NET_SIG);

  // 1mm off the trace centreline: inside its bounding box, outside the copper.
  assert.equal(hitTestPcb(index, -2, 1), null, "a bounding-box hit test would wrongly report the trace here");
});

test("hit testing respects layer visibility, per trace segment", () => {
  const index = buildBoardIndex(fixture());
  // x=3 is on the bottom half of the trace. Hide bottom and it stops answering.
  assert.equal(hitTestPcb(index, 3, 0).element.type, "pcb_trace");
  assert.equal(hitTestPcb(index, 3, 0).layer, "bottom");
  assert.equal(hitTestPcb(index, 3, 0, { visibleLayers: new Set(["top"]) }), null);
  // The top half still answers with the top layer.
  assert.equal(hitTestPcb(index, -2, 0, { visibleLayers: new Set(["top"]) }).layer, "top");
  // A top-layer pad is invisible when only bottom is shown.
  assert.equal(hitTestPcb(index, -4.5, 0, { visibleLayers: new Set(["bottom"]) }), null);
});

test("geometry helpers", () => {
  assert.equal(distance({ x: 0, y: 0 }, { x: 3, y: 4 }), 5);
  assert.equal(traceLength({ route: [{ x: 0, y: 0 }, { x: 3, y: 4 }] }), 5);
  assert.equal(traceLength(null), 0);

  const box = pcbElementBox({ type: "pcb_via", pcb_via_id: "v", x: 1, y: 2, outer_diameter: 2 });
  assert.deepEqual(box, { minX: 0, minY: 1, maxX: 2, maxY: 3 });
  assert.deepEqual(inflateBox(box, 1), { minX: -1, minY: 0, maxX: 3, maxY: 4 });
  assert.equal(boxIsReal(pcbElementBox({ type: "source_component" })), false);

  const merged = unionBox(box, { minX: -5, minY: -5, maxX: -4, maxY: -4 });
  assert.deepEqual(merged, { minX: -5, minY: -5, maxX: 2, maxY: 3 });

  assert.ok(boxIsReal(schematicElementBox({ type: "schematic_component", center: { x: 0, y: 0 }, size: { width: 1, height: 1 } })));
});

test("pcbElementContains handles polygons, circles and stroke widths", () => {
  const polygon = {
    type: "pcb_smtpad",
    shape: "polygon",
    points: [{ x: 0, y: 0 }, { x: 2, y: 0 }, { x: 2, y: 2 }, { x: 0, y: 2 }],
  };
  assert.equal(pcbElementContains(polygon, 1, 1), true);
  assert.equal(pcbElementContains(polygon, 3, 1), false);

  const circle = { type: "pcb_smtpad", shape: "circle", x: 0, y: 0, radius: 1 };
  assert.equal(pcbElementContains(circle, 0.5, 0.5), true);
  assert.equal(pcbElementContains(circle, 1.5, 0), false);

  const silk = { type: "pcb_silkscreen_path", stroke_width: 0.2, route: [{ x: 0, y: 0 }, { x: 5, y: 0 }] };
  assert.equal(pcbElementContains(silk, 2.5, 0.05), true);
  assert.equal(pcbElementContains(silk, 2.5, 0.5), false);
});
