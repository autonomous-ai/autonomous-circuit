// A small board that is structurally faithful to what the pipeline emits, used
// by boardRegions.test.js and boardFunction.test.js.
//
// It is deliberately shaped like a real composition rather than a minimal
// graph, because everything both modules do is about *shape*: parts arriving in
// groups, three identical siblings that should read as one repeated area, a
// light on a GPIO next to a light hard-wired to a rail, a part nobody wired,
// and a pin named with nothing on the other end. Each of those is a case the UI
// has to get right, and none of them shows up in a two-component fixture.
//
//   g_usb    J1 + R3        → V5, USB_DP (reaches U3)
//   g_ldo    U2 + C2        → V5 in, V3_3 out          (power only, shared rail)
//   g_mcu    U3 + U4        → the brain; QSPI is internal to this group
//   g_led_a  LED1 + R20     → V3_3 only                (lit whenever powered)
//   g_led_b  LED2 + R21     → SIG_LED to U3.GPIO0
//   g_btn_1/2/3  SW1..SW3   → three identical siblings, merged
//   g_sensor U5            → on no net at all          (isolated)
//   g_root   R30            → SIG_CAP to U3.GPIO2, written on the board
//
// Plus ORPHAN: a net named on a U3 pin with nothing else attached.

const KEY = (name) => `sub_connectivity_${name}`;

function sourceComponent(id, name, ftype, extra = {}) {
  return { type: "source_component", source_component_id: id, name, ftype, ...extra };
}

function pcbComponent(id, sourceId, x, y, w = 2, h = 1) {
  return {
    type: "pcb_component",
    pcb_component_id: id,
    source_component_id: sourceId,
    center: { x, y },
    width: w,
    height: h,
    layer: "top",
    rotation: 0,
  };
}

function port(id, componentId, name, pinNumber, netName) {
  return {
    type: "source_port",
    source_port_id: id,
    source_component_id: componentId,
    name,
    pin_number: pinNumber,
    ...(netName ? { subcircuit_connectivity_map_key: KEY(netName) } : {}),
  };
}

function net(id, name, { power = false, ground = false } = {}) {
  return {
    type: "source_net",
    source_net_id: id,
    name,
    is_power: power,
    is_ground: ground,
    subcircuit_connectivity_map_key: KEY(name),
  };
}

function group(id, parentId, { x, y } = {}) {
  const out = [{ type: "source_group", source_group_id: id, ...(parentId ? { parent_source_group_id: parentId } : {}) }];
  if (Number.isFinite(x)) {
    out.push({
      type: "pcb_group",
      pcb_group_id: `pcb_${id}`,
      source_group_id: id,
      name: `unnamed_${id}`,
      anchor_position: { x, y },
      center: { x, y },
    });
  }
  return out;
}

/** The fixture, as the bare element array circuit.json actually is. */
export function fixtureBoard() {
  return [
    { type: "pcb_board", pcb_board_id: "pcb_board_0", center: { x: 0, y: 0 }, width: 40, height: 30 },

    ...group("g_root", ""),
    ...group("g_usb", "g_root", { x: -14, y: -10 }),
    ...group("g_ldo", "g_root", { x: -4, y: -10 }),
    ...group("g_mcu", "g_root", { x: 0, y: 0 }),
    ...group("g_led_a", "g_root", { x: 10, y: -10 }),
    ...group("g_led_b", "g_root", { x: 13, y: -10 }),
    ...group("g_btn_1", "g_root", { x: -14, y: 8 }),
    ...group("g_btn_2", "g_root", { x: -8, y: 8 }),
    ...group("g_btn_3", "g_root", { x: -2, y: 8 }),
    ...group("g_sensor", "g_root", { x: 14, y: 8 }),

    net("n_v5", "V5", { power: true }),
    net("n_v33", "V3_3", { power: true }),
    net("n_gnd", "GND", { ground: true }),
    net("n_dp", "USB_DP"),
    net("n_led", "SIG_LED"),
    net("n_cap", "SIG_CAP"),
    net("n_qspi", "QSPI_SCLK"),
    net("n_orphan", "SWCLK"),
    net("n_btn", "BTN_ROW"),

    // --- USB entry
    sourceComponent("sc_j1", "J1", "simple_connector", {
      manufacturer_part_number: "TYPE-C-31-M-12",
      supplier_part_numbers: { jlcpcb: ["C165948"] },
      source_group_id: "g_usb",
    }),
    sourceComponent("sc_r3", "R3", "simple_resistor", {
      display_resistance: "27Ω",
      source_group_id: "g_usb",
    }),
    pcbComponent("pc_j1", "sc_j1", -14, -10, 9, 7),
    pcbComponent("pc_r3", "sc_r3", -11, -10),
    port("sp_j1_vbus", "sc_j1", "VBUS", 1, "V5"),
    port("sp_j1_gnd", "sc_j1", "GND", 2, "GND"),
    port("sp_j1_dp", "sc_j1", "DP1", 3, "USB_DP"),
    port("sp_r3_1", "sc_r3", "pin1", 1, "USB_DP"),

    // --- 3V3 regulator: power only, and the rail it shares with the brain
    sourceComponent("sc_u2", "U2", "simple_chip", {
      manufacturer_part_number: "AMS1117-3.3",
      source_group_id: "g_ldo",
    }),
    sourceComponent("sc_c2", "C2", "simple_capacitor", {
      display_capacitance: "10uF",
      source_group_id: "g_ldo",
    }),
    pcbComponent("pc_u2", "sc_u2", -4, -10, 5, 4),
    pcbComponent("pc_c2", "sc_c2", -1, -10),
    port("sp_u2_in", "sc_u2", "VIN", 1, "V5"),
    port("sp_u2_out", "sc_u2", "VOUT", 2, "V3_3"),
    port("sp_u2_gnd", "sc_u2", "GND", 3, "GND"),
    port("sp_c2_1", "sc_c2", "pin1", 1, "V3_3"),

    // --- the brain, plus its own flash (an internal signal)
    sourceComponent("sc_u3", "U3", "simple_chip", {
      manufacturer_part_number: "RP2040",
      source_group_id: "g_mcu",
    }),
    sourceComponent("sc_u4", "U4", "simple_chip", {
      manufacturer_part_number: "W25Q128JVSIQ",
      source_group_id: "g_mcu",
    }),
    pcbComponent("pc_u3", "sc_u3", 0, 0, 7, 7),
    pcbComponent("pc_u4", "sc_u4", 4, 0, 3, 3),
    port("sp_u3_v33", "sc_u3", "IOVDD", 1, "V3_3"),
    port("sp_u3_gnd", "sc_u3", "GND", 2, "GND"),
    port("sp_u3_dp", "sc_u3", "USB_DP", 3, "USB_DP"),
    port("sp_u3_g0", "sc_u3", "GPIO0", 4, "SIG_LED"),
    port("sp_u3_g2", "sc_u3", "GPIO2", 5, "SIG_CAP"),
    port("sp_u3_g5", "sc_u3", "GPIO5", 6, "BTN_ROW"),
    port("sp_u3_qspi", "sc_u3", "QSPI_SCLK", 7, "QSPI_SCLK"),
    port("sp_u3_swclk", "sc_u3", "SWCLK", 8, "SWCLK"),
    port("sp_u4_clk", "sc_u4", "CLK", 1, "QSPI_SCLK"),

    // --- a light hard-wired to the rail
    sourceComponent("sc_led1", "LED1", "simple_led", { source_group_id: "g_led_a" }),
    sourceComponent("sc_r20", "R20", "simple_resistor", {
      display_resistance: "1kΩ",
      source_group_id: "g_led_a",
    }),
    pcbComponent("pc_led1", "sc_led1", 10, -10),
    pcbComponent("pc_r20", "sc_r20", 11, -10),
    port("sp_led1_a", "sc_led1", "anode", 1, "V3_3"),
    port("sp_led1_k", "sc_led1", "cathode", 2, "GND"),
    port("sp_r20_1", "sc_r20", "pin1", 1, "V3_3"),

    // --- a light on a pin
    sourceComponent("sc_led2", "LED2", "simple_led", { source_group_id: "g_led_b" }),
    sourceComponent("sc_r21", "R21", "simple_resistor", {
      display_resistance: "1kΩ",
      source_group_id: "g_led_b",
    }),
    pcbComponent("pc_led2", "sc_led2", 13, -10),
    pcbComponent("pc_r21", "sc_r21", 14, -10),
    port("sp_led2_a", "sc_led2", "anode", 1, "SIG_LED"),
    port("sp_led2_k", "sc_led2", "cathode", 2, "GND"),
    port("sp_r21_1", "sc_r21", "pin1", 1, "SIG_LED"),

    // --- three identical buttons
    ...[1, 2, 3].flatMap((i) => [
      sourceComponent(`sc_sw${i}`, `SW${i}`, "simple_push_button", {
        supplier_part_numbers: { jlcpcb: ["C318884"] },
        source_group_id: `g_btn_${i}`,
      }),
      pcbComponent(`pc_sw${i}`, `sc_sw${i}`, -14 + (i - 1) * 6, 8, 4, 3),
      port(`sp_sw${i}_1`, `sc_sw${i}`, "pin1", 1, "BTN_ROW"),
      port(`sp_sw${i}_2`, `sc_sw${i}`, "pin2", 2, "GND"),
    ]),

    // --- a sensor nobody wired
    sourceComponent("sc_u5", "U5", "simple_chip", {
      manufacturer_part_number: "BME280",
      source_group_id: "g_sensor",
    }),
    pcbComponent("pc_u5", "sc_u5", 14, 8, 3, 3),
    port("sp_u5_sda", "sc_u5", "SDA", 1, ""),
    port("sp_u5_scl", "sc_u5", "SCL", 2, ""),

    // --- glue written straight into the board file
    sourceComponent("sc_r30", "R30", "simple_resistor", {
      display_resistance: "1MΩ",
      source_group_id: "g_root",
    }),
    pcbComponent("pc_r30", "sc_r30", 6, 6),
    port("sp_r30_1", "sc_r30", "pin1", 1, "SIG_CAP"),
  ];
}
