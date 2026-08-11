// boardIndex — turn a `<stem>.circuit.json` element array into the three
// indexes the Altium-style workspace needs: components (one row per real part,
// carrying BOTH its schematic and its PCB geometry), nets (one row per
// electrical node, carrying every element on it in BOTH domains), and drawable
// PCB geometry. DOM-free and fetch-free so node:test covers it directly.
//
// Why this file exists: cross-probing is only possible if the two views share
// identity. Circuit JSON already carries that identity — `source_component_id`
// appears on the schematic element AND the pcb element for the same part, and
// `subcircuit_connectivity_map_key` is the one token every connected element
// agrees on. Index those two keys and "click here, light up there" falls out.
//
// Net identity, precisely. The connectivity key is present directly on
// source_net, source_trace, source_port, schematic_trace and pcb_via. It is
// NOT on pcb_trace, pcb_smtpad, pcb_plated_hole, schematic_port or
// schematic_net_label — those resolve through one hop:
//     pcb_trace        → source_trace_id → source_trace
//     pcb_smtpad       → pcb_port_id     → pcb_port → source_port_id → source_port
//     pcb_plated_hole  → pcb_port_id     → …
//     schematic_port   → source_port_id  → source_port
//     schematic_net_label → source_net_id → source_net
// Resolution is a two-pass walk, so element order in the file never matters.
//
// Group identity, and why it is worth indexing. Every golden block a board
// composes becomes one `source_group` in the compiled JSON, and every part the
// block brought carries that group's id. That is the only surviving record of
// "these eleven parts arrived together as the USB-C entry" — the block name
// itself does not survive compilation. `pcb_group` carries the same id plus a
// real anchor and box, so the grouping is drawable as well as listable.
// `boardRegions.js` turns that into labelled areas; here we only index it.

/** The id field for an element is conventionally `<type>_id`. */
export function elementId(element) {
  if (!element || typeof element !== "object") return "";
  const key = `${element.type}_id`;
  const value = element[key];
  return typeof value === "string" && value ? value : "";
}

const NUM = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);

/** Grow a mutable {minX,minY,maxX,maxY} box to include a point. */
function growPoint(box, x, y) {
  if (!Number.isFinite(x) || !Number.isFinite(y)) return box;
  if (box.minX > x) box.minX = x;
  if (box.minY > y) box.minY = y;
  if (box.maxX < x) box.maxX = x;
  if (box.maxY < y) box.maxY = y;
  return box;
}

function emptyBox() {
  return { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
}

/** A grown box is real only if something grew it. */
export function boxIsReal(box) {
  return Boolean(box) && Number.isFinite(box.minX) && Number.isFinite(box.maxX) && box.maxX >= box.minX;
}

function growRect(box, cx, cy, width, height) {
  const halfW = Math.abs(NUM(width)) / 2;
  const halfH = Math.abs(NUM(height)) / 2;
  growPoint(box, cx - halfW, cy - halfH);
  growPoint(box, cx + halfW, cy + halfH);
  return box;
}

/** Pad the box on every side (mm) — selection outlines want breathing room. */
export function inflateBox(box, margin) {
  if (!boxIsReal(box)) return box;
  const m = NUM(margin);
  return { minX: box.minX - m, minY: box.minY - m, maxX: box.maxX + m, maxY: box.maxY + m };
}

/** Union of two boxes; either may be unreal. */
export function unionBox(a, b) {
  if (!boxIsReal(a)) return boxIsReal(b) ? { ...b } : emptyBox();
  if (!boxIsReal(b)) return { ...a };
  return {
    minX: Math.min(a.minX, b.minX),
    minY: Math.min(a.minY, b.minY),
    maxX: Math.max(a.maxX, b.maxX),
    maxY: Math.max(a.maxY, b.maxY),
  };
}

/** Distance between two {x,y} points, in whatever unit they carry. */
export function distance(a, b) {
  const dx = NUM(b?.x) - NUM(a?.x);
  const dy = NUM(b?.y) - NUM(a?.y);
  return Math.sqrt(dx * dx + dy * dy);
}

// --- element → geometry ----------------------------------------------------

/**
 * The bounding box of one PCB element, in board mm (y up). Returns an unreal
 * box for element kinds that carry no geometry.
 */
export function pcbElementBox(element) {
  const box = emptyBox();
  if (!element) return box;
  switch (element.type) {
    case "pcb_board":
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), element.width, element.height);
    case "pcb_component":
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), element.width, element.height);
    case "pcb_courtyard_rect":
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), element.width, element.height);
    case "pcb_courtyard_outline":
      for (const point of element.outline || []) growPoint(box, NUM(point.x), NUM(point.y));
      return box;
    case "pcb_smtpad":
    case "pcb_solder_paste": {
      if (element.shape === "circle") {
        const r = NUM(element.radius, NUM(element.width) / 2);
        return growRect(box, NUM(element.x), NUM(element.y), r * 2, r * 2);
      }
      if (element.shape === "polygon") {
        for (const point of element.points || []) growPoint(box, NUM(point.x), NUM(point.y));
        return box;
      }
      return growRect(box, NUM(element.x), NUM(element.y), element.width, element.height);
    }
    case "pcb_plated_hole": {
      const w = NUM(element.outer_width, NUM(element.outer_diameter));
      const h = NUM(element.outer_height, NUM(element.outer_diameter, w));
      return growRect(box, NUM(element.x), NUM(element.y), w, h);
    }
    case "pcb_hole": {
      const d = NUM(element.hole_diameter, NUM(element.hole_width));
      return growRect(box, NUM(element.x), NUM(element.y), d, d);
    }
    case "pcb_via": {
      const d = NUM(element.outer_diameter, NUM(element.hole_diameter));
      return growRect(box, NUM(element.x), NUM(element.y), d, d);
    }
    case "pcb_port":
      return growRect(box, NUM(element.x), NUM(element.y), 0.2, 0.2);
    case "pcb_trace": {
      for (const point of element.route || []) growPoint(box, NUM(point.x), NUM(point.y));
      return box;
    }
    case "pcb_silkscreen_path":
      for (const point of element.route || []) growPoint(box, NUM(point.x), NUM(point.y));
      return box;
    case "pcb_silkscreen_text": {
      const size = NUM(element.font_size, 0.4);
      const width = Math.max(1, String(element.text || "").length) * size * 0.62;
      return growRect(box, NUM(element.anchor_position?.x), NUM(element.anchor_position?.y), width, size);
    }
    case "pcb_silkscreen_rect":
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), element.width, element.height);
    case "pcb_silkscreen_circle": {
      const r = NUM(element.radius);
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), r * 2, r * 2);
    }
    case "pcb_cutout": {
      if (element.shape === "circle") {
        const r = NUM(element.radius);
        return growRect(box, NUM(element.center?.x), NUM(element.center?.y), r * 2, r * 2);
      }
      if (Array.isArray(element.points)) {
        for (const point of element.points) growPoint(box, NUM(point.x), NUM(point.y));
        return box;
      }
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), element.width, element.height);
    }
    default:
      return box;
  }
}

/** The bounding box of one schematic element, in schematic units (y up). */
export function schematicElementBox(element) {
  const box = emptyBox();
  if (!element) return box;
  switch (element.type) {
    case "schematic_component":
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), element.size?.width, element.size?.height);
    case "schematic_port":
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), 0.1, 0.1);
    case "schematic_net_label":
      return growRect(box, NUM(element.center?.x), NUM(element.center?.y), 0.4, 0.2);
    case "schematic_trace": {
      for (const edge of element.edges || []) {
        growPoint(box, NUM(edge.from?.x), NUM(edge.from?.y));
        growPoint(box, NUM(edge.to?.x), NUM(edge.to?.y));
      }
      return box;
    }
    case "schematic_text":
      return growRect(box, NUM(element.position?.x), NUM(element.position?.y), 0.4, NUM(element.font_size, 0.18));
    default:
      return box;
  }
}

/** Total centreline length of a pcb_trace route, in mm. */
export function traceLength(trace) {
  const route = Array.isArray(trace?.route) ? trace.route : [];
  let total = 0;
  for (let i = 1; i < route.length; i += 1) total += distance(route[i - 1], route[i]);
  return total;
}

// --- the index -------------------------------------------------------------

/** Accepts the bare array (the file's shape) or `{elements: [...]}`. */
export function circuitElements(input) {
  if (Array.isArray(input)) return input.filter((element) => element && typeof element === "object");
  if (Array.isArray(input?.elements)) return input.elements.filter((e) => e && typeof e === "object");
  return [];
}

const PCB_DRAW_TYPES = new Set([
  "pcb_smtpad",
  "pcb_plated_hole",
  "pcb_hole",
  "pcb_via",
  "pcb_trace",
  "pcb_silkscreen_path",
  "pcb_silkscreen_text",
  "pcb_silkscreen_rect",
  "pcb_silkscreen_circle",
  "pcb_courtyard_rect",
  "pcb_courtyard_outline",
  "pcb_solder_paste",
  "pcb_cutout",
]);

/**
 * Build the workspace index.
 *
 * @param {unknown} input parsed circuit.json (array) or `{elements}`
 * @returns {{
 *   elements: object[],
 *   byId: Map<string, object>,
 *   board: object|null,
 *   boardBox: {minX:number,minY:number,maxX:number,maxY:number},
 *   pcbBox: object,
 *   schematicBox: object,
 *   components: object[],
 *   componentByRefdes: Map<string, object>,
 *   componentBySourceId: Map<string, object>,
 *   componentByPcbId: Map<string, object>,
 *   componentBySchematicId: Map<string, object>,
 *   nets: object[],
 *   netByKey: Map<string, object>,
 *   netByName: Map<string, object>,
 *   netKeyByElementId: Map<string, string>,
 *   componentKeyByElementId: Map<string, string>,
 *   groups: object[],
 *   groupById: Map<string, object>,
 *   pcbDrawables: object[],
 *   layers: string[],
 *   stats: {elements:number, components:number, nets:number},
 * }}
 */
export function buildBoardIndex(input) {
  const elements = circuitElements(input);
  const byId = new Map();
  const byType = new Map();
  for (const element of elements) {
    const id = elementId(element);
    if (id) byId.set(id, element);
    const bucket = byType.get(element.type);
    if (bucket) bucket.push(element);
    else byType.set(element.type, [element]);
  }
  const of = (type) => byType.get(type) || [];

  // --- pass 1: connectivity keys that are written on the element itself.
  const netKeyByElementId = new Map();
  const setKey = (id, key) => {
    if (id && key && !netKeyByElementId.has(id)) netKeyByElementId.set(id, key);
  };
  for (const element of elements) {
    const key = element.subcircuit_connectivity_map_key;
    if (typeof key === "string" && key) setKey(elementId(element), key);
  }

  // --- pass 2: one-hop resolution for the element kinds that carry no key.
  const keyOfSourcePort = (sourcePortId) => netKeyByElementId.get(String(sourcePortId || "")) || "";
  const keyOfPcbPort = (pcbPortId) => {
    const port = byId.get(String(pcbPortId || ""));
    return port ? keyOfSourcePort(port.source_port_id) : "";
  };
  for (const element of elements) {
    const id = elementId(element);
    if (!id || netKeyByElementId.has(id)) continue;
    switch (element.type) {
      case "pcb_trace":
        setKey(id, netKeyByElementId.get(String(element.source_trace_id || "")) || "");
        break;
      case "pcb_port":
        setKey(id, keyOfSourcePort(element.source_port_id));
        break;
      case "pcb_smtpad":
      case "pcb_plated_hole":
      case "pcb_solder_paste":
        setKey(id, keyOfPcbPort(element.pcb_port_id));
        break;
      case "schematic_port":
        setKey(id, keyOfSourcePort(element.source_port_id));
        break;
      case "schematic_net_label":
        setKey(id, netKeyByElementId.get(String(element.source_net_id || "")) || "");
        break;
      default:
        break;
    }
  }
  // pcb_smtpad may reach a port only via its solder-paste sibling, and traces
  // that never named a source_trace can still be placed by the ports they end
  // on — one more sweep catches both without changing anything already set.
  for (const element of of("pcb_trace")) {
    const id = elementId(element);
    if (netKeyByElementId.has(id)) continue;
    for (const portId of element.connectsTo || []) {
      const key = keyOfPcbPort(portId);
      if (key) {
        setKey(id, key);
        break;
      }
    }
  }

  // --- components: one row per source_component, both geometries attached.
  const schematicBySource = new Map();
  for (const element of of("schematic_component")) {
    if (element.source_component_id) schematicBySource.set(element.source_component_id, element);
  }
  const pcbBySource = new Map();
  for (const element of of("pcb_component")) {
    if (element.source_component_id) pcbBySource.set(element.source_component_id, element);
  }

  const components = [];
  const componentBySourceId = new Map();
  const componentByRefdes = new Map();
  const componentByPcbId = new Map();
  const componentBySchematicId = new Map();

  for (const source of of("source_component")) {
    const sourceId = String(source.source_component_id || "");
    if (!sourceId) continue;
    const schematic = schematicBySource.get(sourceId) || null;
    const pcb = pcbBySource.get(sourceId) || null;
    const supplier = source.supplier_part_numbers || {};
    const lcsc = String((supplier.jlcpcb || supplier.lcsc || [])[0] || "");
    const component = {
      key: sourceId,
      groupId: String(source.source_group_id || ""),
      refdes: String(source.name || "").trim(),
      ftype: String(source.ftype || ""),
      mpn: String(source.manufacturer_part_number || ""),
      lcsc,
      supplierPartNumbers: supplier,
      value: String(
        source.display_resistance ||
          source.display_capacitance ||
          source.display_inductance ||
          source.display_voltage ||
          schematic?.symbol_display_value ||
          "",
      ),
      source,
      schematic,
      pcb,
      schematicId: schematic ? String(schematic.schematic_component_id || "") : "",
      pcbId: pcb ? String(pcb.pcb_component_id || "") : "",
      layer: String(pcb?.layer || "top"),
      rotation: NUM(pcb?.rotation),
      pcbBox: emptyBox(),
      schematicBox: schematicElementBox(schematic),
      pcbElementIds: [],
      schematicElementIds: [],
      netKeys: new Set(),
      // Pin name → net, the one thing that lets the UI say "GPIO0" instead of
      // "a pin somewhere on U3". Filled from source_port below.
      ports: [],
      portNamesByNetKey: new Map(),
      pads: 0,
    };
    if (pcb) component.pcbBox = pcbElementBox(pcb);
    components.push(component);
    componentBySourceId.set(sourceId, component);
    if (component.refdes && !componentByRefdes.has(component.refdes)) {
      componentByRefdes.set(component.refdes, component);
    }
    if (component.pcbId) componentByPcbId.set(component.pcbId, component);
    if (component.schematicId) componentBySchematicId.set(component.schematicId, component);
  }

  // Attach every child element to its component, growing the boxes as we go.
  const componentKeyByElementId = new Map();
  const attach = (component, element, domain) => {
    const id = elementId(element);
    if (!id) return;
    componentKeyByElementId.set(id, component.key);
    if (domain === "pcb") {
      component.pcbElementIds.push(id);
      component.pcbBox = unionBox(component.pcbBox, pcbElementBox(element));
    } else {
      component.schematicElementIds.push(id);
      component.schematicBox = unionBox(component.schematicBox, schematicElementBox(element));
    }
    const netKey = netKeyByElementId.get(id);
    if (netKey) component.netKeys.add(netKey);
  };

  for (const element of elements) {
    if (typeof element.type !== "string") continue;
    if (element.type.startsWith("pcb_")) {
      const owner = componentByPcbId.get(String(element.pcb_component_id || ""));
      if (owner) {
        attach(owner, element, "pcb");
        if (element.type === "pcb_smtpad" || element.type === "pcb_plated_hole") owner.pads += 1;
      }
    } else if (element.type.startsWith("schematic_")) {
      const owner = componentBySchematicId.get(String(element.schematic_component_id || ""));
      if (owner) attach(owner, element, "schematic");
    }
  }
  // Ports know their component through the source layer, not the pcb layer.
  for (const port of of("pcb_port")) {
    const owner = componentByPcbId.get(String(port.pcb_component_id || ""));
    const key = netKeyByElementId.get(elementId(port));
    if (owner && key) owner.netKeys.add(key);
  }
  for (const port of of("source_port")) {
    const owner = componentBySourceId.get(String(port.source_component_id || ""));
    if (!owner) continue;
    const key = netKeyByElementId.get(elementId(port)) || "";
    const name = String(port.name || "").trim() || `pin${port.pin_number ?? ""}`;
    owner.ports.push({ name, pinNumber: Number(port.pin_number) || 0, netKey: key });
    if (!key) continue;
    owner.netKeys.add(key);
    const names = owner.portNamesByNetKey.get(key);
    if (names) {
      if (!names.includes(name)) names.push(name);
    } else {
      owner.portNamesByNetKey.set(key, [name]);
    }
  }

  // --- nets: one row per connectivity key, named from the source layer.
  const nets = [];
  const netByKey = new Map();
  const ensureNet = (key) => {
    let net = netByKey.get(key);
    if (net) return net;
    net = {
      key,
      name: "",
      isGround: false,
      isPower: false,
      sourceNetId: "",
      elementIds: [],
      pcbElementIds: [],
      schematicElementIds: [],
      componentKeys: new Set(),
      pinCount: 0,
      lengthMm: 0,
      pcbBox: emptyBox(),
      schematicBox: emptyBox(),
    };
    netByKey.set(key, net);
    nets.push(net);
    return net;
  };

  for (const net of of("source_net")) {
    const key = net.subcircuit_connectivity_map_key;
    if (!key) continue;
    const row = ensureNet(key);
    row.name = String(net.name || row.name || "");
    row.isGround = Boolean(net.is_ground) || row.isGround;
    row.isPower = Boolean(net.is_power) || row.isPower;
    row.sourceNetId = String(net.source_net_id || "");
  }

  for (const [id, key] of netKeyByElementId) {
    const element = byId.get(id);
    if (!element) continue;
    const net = ensureNet(key);
    net.elementIds.push(id);
    if (element.type.startsWith("pcb_")) {
      net.pcbElementIds.push(id);
      if (PCB_DRAW_TYPES.has(element.type)) net.pcbBox = unionBox(net.pcbBox, pcbElementBox(element));
      if (element.type === "pcb_smtpad" || element.type === "pcb_plated_hole") net.pinCount += 1;
      if (element.type === "pcb_trace") net.lengthMm += traceLength(element);
    } else if (element.type.startsWith("schematic_")) {
      net.schematicElementIds.push(id);
      net.schematicBox = unionBox(net.schematicBox, schematicElementBox(element));
    }
    const owner = componentKeyByElementId.get(id);
    if (owner) net.componentKeys.add(owner);
    if (element.type === "source_port") {
      const ownerKey = String(element.source_component_id || "");
      if (ownerKey) net.componentKeys.add(ownerKey);
    }
  }

  // A net with no name of its own borrows the trace display name, then falls
  // back to a stable synthetic — never blank, an unnamed net still needs a
  // handle in the UI.
  const traceByKey = new Map();
  for (const trace of of("source_trace")) {
    const key = trace.subcircuit_connectivity_map_key;
    if (key && !traceByKey.has(key)) traceByKey.set(key, trace);
  }
  let unnamed = 0;
  for (const net of nets) {
    if (net.name) continue;
    const trace = traceByKey.get(net.key);
    net.name = String(trace?.name || trace?.display_name || "").trim();
    if (!net.name) {
      unnamed += 1;
      net.name = `N$${unnamed}`;
      net.unnamed = true;
    }
  }
  nets.sort((a, b) => b.pinCount - a.pinCount || a.name.localeCompare(b.name));

  const netByName = new Map();
  for (const net of nets) if (!netByName.has(net.name)) netByName.set(net.name, net);

  // --- groups: one row per source_group that actually owns parts.
  //
  // A block instance compiles to one source_group; the block's own name is
  // gone by then, but the membership is not. Groups that own no components of
  // their own (a bare `<group>` wrapper used only to rotate a child) are
  // indexed too, so a caller can walk the tree, but they carry no parts.
  const pcbGroupBySourceId = new Map();
  for (const element of of("pcb_group")) {
    const sourceGroupId = String(element.source_group_id || "");
    if (sourceGroupId && !pcbGroupBySourceId.has(sourceGroupId)) {
      pcbGroupBySourceId.set(sourceGroupId, element);
    }
  }
  const groups = [];
  const groupById = new Map();
  const ensureGroup = (id) => {
    let group = groupById.get(id);
    if (group) return group;
    group = {
      id,
      parentId: "",
      isRoot: false,
      pcbGroup: null,
      anchor: null,
      componentKeys: [],
      pcbBox: emptyBox(),
      childIds: [],
    };
    groupById.set(id, group);
    groups.push(group);
    return group;
  };
  for (const element of of("source_group")) {
    const id = String(element.source_group_id || "");
    if (!id) continue;
    const group = ensureGroup(id);
    group.parentId = String(element.parent_source_group_id || "");
    // The board's own group is the one nothing else contains. Board-level
    // glue — the passives a composer wrote directly in the board file rather
    // than getting from a block — lands there, which is exactly what makes it
    // worth distinguishing from a block's group.
    group.isRoot = !group.parentId;
  }
  for (const group of groups) {
    if (group.parentId && groupById.has(group.parentId)) {
      groupById.get(group.parentId).childIds.push(group.id);
    }
  }
  for (const component of components) {
    if (!component.groupId) continue;
    const group = ensureGroup(component.groupId);
    group.componentKeys.push(component.key);
    group.pcbBox = unionBox(group.pcbBox, component.pcbBox);
  }
  for (const group of groups) {
    const pcbGroup = pcbGroupBySourceId.get(group.id) || null;
    group.pcbGroup = pcbGroup;
    if (pcbGroup?.anchor_position) {
      group.anchor = { x: NUM(pcbGroup.anchor_position.x), y: NUM(pcbGroup.anchor_position.y) };
    }
  }

  // --- drawable PCB geometry, in file order (painter order comes later).
  const pcbDrawables = elements.filter((element) => PCB_DRAW_TYPES.has(element.type));

  const board = of("pcb_board")[0] || null;
  const boardBox = board ? pcbElementBox(board) : emptyBox();
  let pcbBox = boardBox;
  for (const element of pcbDrawables) pcbBox = unionBox(pcbBox, pcbElementBox(element));
  let schematicBox = emptyBox();
  for (const element of elements) {
    if (typeof element.type === "string" && element.type.startsWith("schematic_")) {
      schematicBox = unionBox(schematicBox, schematicElementBox(element));
    }
  }

  const layerSet = new Set();
  for (const element of pcbDrawables) {
    if (typeof element.layer === "string" && element.layer) layerSet.add(element.layer);
    for (const layer of element.layers || []) if (typeof layer === "string") layerSet.add(layer);
    for (const point of element.route || []) if (point?.layer) layerSet.add(String(point.layer));
  }
  if (!layerSet.size) layerSet.add("top");

  return {
    elements,
    byId,
    byType,
    board,
    boardBox,
    pcbBox: boxIsReal(pcbBox) ? pcbBox : { minX: -10, minY: -10, maxX: 10, maxY: 10 },
    schematicBox,
    components,
    componentByRefdes,
    componentBySourceId,
    componentByPcbId,
    componentBySchematicId,
    componentKeyByElementId,
    groups,
    groupById,
    nets,
    netByKey,
    netByName,
    netKeyByElementId,
    pcbDrawables,
    layers: [...layerSet],
    stats: { elements: elements.length, components: components.length, nets: nets.length },
  };
}

/** The empty index — lets consumers render before the fetch lands. */
export function emptyBoardIndex() {
  return buildBoardIndex([]);
}

// --- selection resolution --------------------------------------------------

/**
 * Everything that should light up for the current selection, in both domains.
 * Selection is one of `{kind: "component", key}` / `{kind: "net", key}` /
 * null. The returned sets are element-id sets the canvases test against.
 *
 * @returns {{
 *   pcbIds: Set<string>, schematicIds: Set<string>,
 *   componentKeys: Set<string>, netKeys: Set<string>, refdes: Set<string>,
 *   pcbBox: object, schematicBox: object,
 * }}
 */
export function resolveSelection(index, selection) {
  const out = {
    pcbIds: new Set(),
    schematicIds: new Set(),
    componentKeys: new Set(),
    netKeys: new Set(),
    refdes: new Set(),
    pcbBox: emptyBox(),
    schematicBox: emptyBox(),
  };
  if (!index || !selection || !selection.kind || !selection.key) return out;

  if (selection.kind === "component") {
    const component = index.componentBySourceId.get(selection.key) || index.componentByRefdes.get(selection.key);
    if (!component) return out;
    out.componentKeys.add(component.key);
    if (component.refdes) out.refdes.add(component.refdes);
    for (const id of component.pcbElementIds) out.pcbIds.add(id);
    for (const id of component.schematicElementIds) out.schematicIds.add(id);
    if (component.pcbId) out.pcbIds.add(component.pcbId);
    if (component.schematicId) out.schematicIds.add(component.schematicId);
    out.pcbBox = component.pcbBox;
    out.schematicBox = component.schematicBox;
    return out;
  }

  if (selection.kind === "net") {
    const net = index.netByKey.get(selection.key) || index.netByName.get(selection.key);
    if (!net) return out;
    out.netKeys.add(net.key);
    for (const id of net.pcbElementIds) out.pcbIds.add(id);
    for (const id of net.schematicElementIds) out.schematicIds.add(id);
    for (const key of net.componentKeys) {
      out.componentKeys.add(key);
      const component = index.componentBySourceId.get(key);
      if (component?.refdes) out.refdes.add(component.refdes);
    }
    out.pcbBox = net.pcbBox;
    out.schematicBox = net.schematicBox;
    return out;
  }

  return out;
}

/** Shortest distance from point p to segment ab. */
function pointSegmentDistance(px, py, ax, ay, bx, by) {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / lengthSq;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Ray-cast point-in-polygon over a [{x,y}] ring. */
function pointInPolygon(px, py, points) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
    const xi = NUM(points[i]?.x);
    const yi = NUM(points[i]?.y);
    const xj = NUM(points[j]?.x);
    const yj = NUM(points[j]?.y);
    if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi || 1e-12) + xi) inside = !inside;
  }
  return inside;
}

/**
 * True when (x,y) is really inside the element, not just inside its box. Traces
 * and silkscreen strokes get a proper stroke-width test — a diagonal trace has
 * a huge bounding box, and treating that box as the target makes the whole
 * canvas feel mushy.
 */
export function pcbElementContains(element, x, y, tolerance = 0, visibleLayers = null) {
  if (!element) return false;
  switch (element.type) {
    case "pcb_trace": {
      // A route can change layer mid-way, so the layer filter has to be applied
      // per segment — a trace whose bottom half is hidden must not answer a hit
      // over that half.
      const route = element.route || [];
      for (let i = 1; i < route.length; i += 1) {
        const a = route[i - 1];
        const b = route[i];
        const layer = String(b.layer || a.layer || element.layer || "top");
        if (visibleLayers && !visibleLayers.has(layer)) continue;
        const half = NUM(b.width, NUM(a.width, 0.15)) / 2;
        if (pointSegmentDistance(x, y, NUM(a.x), NUM(a.y), NUM(b.x), NUM(b.y)) <= half + tolerance) return true;
      }
      return false;
    }
    case "pcb_silkscreen_path": {
      const route = element.route || [];
      const half = NUM(element.stroke_width, 0.1) / 2;
      for (let i = 1; i < route.length; i += 1) {
        const a = route[i - 1];
        const b = route[i];
        if (pointSegmentDistance(x, y, NUM(a.x), NUM(a.y), NUM(b.x), NUM(b.y)) <= half + tolerance) return true;
      }
      return false;
    }
    case "pcb_via":
    case "pcb_hole": {
      const r = NUM(element.outer_diameter, NUM(element.hole_diameter, 0.3)) / 2;
      return Math.hypot(x - NUM(element.x), y - NUM(element.y)) <= r + tolerance;
    }
    case "pcb_smtpad":
    case "pcb_solder_paste": {
      if (element.shape === "circle") {
        const r = NUM(element.radius, NUM(element.width) / 2);
        return Math.hypot(x - NUM(element.x), y - NUM(element.y)) <= r + tolerance;
      }
      if (element.shape === "polygon") return pointInPolygon(x, y, element.points || []);
      break;
    }
    default:
      break;
  }
  const box = pcbElementBox(element);
  if (!boxIsReal(box)) return false;
  return x >= box.minX - tolerance && x <= box.maxX + tolerance && y >= box.minY - tolerance && y <= box.maxY + tolerance;
}

// Hit-test priority: pads and vias beat traces beat silkscreen, so a pointer
// resting on a pad that a trace runs under still reports the pad.
const HIT_RANK = {
  pcb_smtpad: 40,
  pcb_plated_hole: 40,
  pcb_via: 35,
  pcb_hole: 30,
  pcb_trace: 20,
  pcb_silkscreen_text: 12,
  pcb_silkscreen_path: 10,
  pcb_silkscreen_rect: 10,
  pcb_silkscreen_circle: 10,
  pcb_cutout: 8,
};

/**
 * What the pointer is over, given board-mm coordinates. Pads win over traces
 * win over silkscreen; within a rank the last-drawn element wins.
 * `visibleLayers` is a Set of layer names; null means all.
 */
export function hitTestPcb(index, x, y, { visibleLayers = null, tolerance = 0 } = {}) {
  if (!index) return null;
  const drawables = index.pcbDrawables;
  let best = null;
  let bestRank = -1;
  for (let i = 0; i < drawables.length; i += 1) {
    const element = drawables[i];
    const rank = HIT_RANK[element.type];
    if (rank === undefined) continue;
    if (rank < bestRank) continue;
    if (visibleLayers && element.layer && !visibleLayers.has(element.layer)) continue;
    if (!pcbElementContains(element, x, y, tolerance, visibleLayers)) continue;
    bestRank = rank;
    best = element;
  }
  if (!best) {
    // Nothing drawn is under the cursor — but the cursor may still be inside a
    // component's body, which on a connector or a large IC is mostly empty
    // space between the pads. Altium selects the part there, so we do too.
    // Last resort, so it can never steal a click from real copper.
    for (const component of index.components) {
      const pcb = component.pcb;
      if (!pcb) continue;
      if (visibleLayers && pcb.layer && !visibleLayers.has(pcb.layer)) continue;
      const box = pcbElementBox(pcb);
      if (!boxIsReal(box)) continue;
      if (x < box.minX || x > box.maxX || y < box.minY || y > box.maxY) continue;
      return {
        elementId: component.pcbId,
        element: pcb,
        netKey: "",
        componentKey: component.key,
        layer: String(pcb.layer || "top"),
      };
    }
    return null;
  }
  const id = elementId(best);
  // A trace reports the layer of the segment actually under the cursor.
  let layer = String(best.layer || (best.layers || [])[0] || "");
  if (best.type === "pcb_trace") {
    const route = best.route || [];
    let closest = Infinity;
    for (let i = 1; i < route.length; i += 1) {
      const a = route[i - 1];
      const b = route[i];
      const d = pointSegmentDistance(x, y, NUM(a.x), NUM(a.y), NUM(b.x), NUM(b.y));
      if (d < closest) {
        closest = d;
        layer = String(b.layer || a.layer || layer || "top");
      }
    }
  }
  return {
    elementId: id,
    element: best,
    netKey: index.netKeyByElementId.get(id) || "",
    componentKey: index.componentKeyByElementId.get(id) || "",
    layer,
  };
}
