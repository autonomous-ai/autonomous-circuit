// boardRegions — "what am I looking at" for the PCB canvas.
//
// The layout is the most intimidating surface in the app. Two hundred pads and
// four hundred track segments, and nothing on screen says which corner is the
// power supply and which is the radio. Every professional tool answers this the
// same way: Altium calls the areas **rooms** and creates one per schematic
// sheet or channel; KiCad's equivalent is the design block / hierarchical
// sheet. The area is drawn, named, and stays out of the way.
//
// We can do the same without asking anyone to draw anything, because the
// grouping is already in the file. A golden block compiles to one
// `source_group`, every part it brought carries that group's id, and
// `pcb_group` carries the same id plus a real anchor. `boardIndex` indexes
// that; this module turns it into named areas.
//
// The naming rule, and the reason it cannot lie: **a region is named after
// what is inside it, never after what we think it was meant to be.** The block
// name does not survive compilation, so inventing one back would be a guess.
// A group holding an RP2040 is "The brain" because there is an RP2040 in it —
// a claim the user can check by clicking the region and reading the parts.
//
// Repeats. A fifty-key matrix compiles to fifty identical sibling groups.
// Fifty boxes each labelled "Key" is noise, so three or more siblings with the
// same part signature merge into one room with a count, exactly the way Altium
// treats a repeated channel. Two of a thing stay two things — a board with two
// regulators has two rails, and saying "Power supply ×2" over one box spanning
// both would hide that.

import { boxIsReal, inflateBox, unionBox } from "./boardIndex.js";
import { boxToScreenRect } from "./boardRender.js";
import { partPlainName, partRole } from "./plainLanguage.js";

/** Which role wins when a group holds several kinds of part. Lower is louder. */
const ROLE_RANK = [
  "brain",
  "radio",
  "sensor",
  "driver",
  "power_in",
  "power_reg",
  "memory",
  "control",
  "indicator",
  "clock",
  "protection",
  "mechanical",
  "passive",
  "other",
];

/** Plain names for an area of board. Singular and plural, because "Buttons"
 *  over one button is the kind of small wrongness that costs trust. */
const REGION_LABELS = Object.freeze({
  brain: ["The brain", "The brains"],
  power_in: ["Power in", "Power in"],
  power_reg: ["Power supply", "Power supplies"],
  memory: ["Memory", "Memory"],
  sensor: ["Sensor", "Sensors"],
  radio: ["Radio", "Radios"],
  driver: ["Driver", "Drivers"],
  indicator: ["Light", "Lights"],
  control: ["Button", "Buttons"],
  clock: ["Clock", "Clocks"],
  protection: ["Protection", "Protection"],
  passive: ["Supporting parts", "Supporting parts"],
  mechanical: ["Mounting", "Mounting"],
  other: ["Parts", "Parts"],
});

/** Siblings this many deep or more collapse into one counted room. */
export const MERGE_THRESHOLD = 3;

const rankOf = (role) => {
  const at = ROLE_RANK.indexOf(role);
  return at < 0 ? ROLE_RANK.length : at;
};

/**
 * The part that gives a group its name: the loudest role, and within a role the
 * one with a real manufacturer part number (an RP2040 names a group; the 100nF
 * beside it does not).
 */
export function leadComponent(components) {
  let best = null;
  let bestRank = Infinity;
  let bestHasMpn = false;
  for (const component of Array.isArray(components) ? components : []) {
    const role = partRole(component).role;
    const rank = rankOf(role);
    const hasMpn = Boolean(String(component?.mpn || "").trim());
    if (rank < bestRank || (rank === bestRank && hasMpn && !bestHasMpn)) {
      best = component;
      bestRank = rank;
      bestHasMpn = hasMpn;
    }
  }
  return best;
}

/**
 * What a group is, as a signature two sibling groups can be compared on. Two
 * groups are "the same block again" when they hold the same kinds of part in
 * the same numbers — which is what a repeated channel is.
 */
export function groupSignature(components) {
  const parts = (Array.isArray(components) ? components : [])
    .map((component) => `${component?.ftype || ""}|${String(component?.mpn || "").toUpperCase()}`)
    .sort();
  return parts.join(",");
}

function labelFor(role, count) {
  const pair = REGION_LABELS[role] || REGION_LABELS.other;
  return count > 1 ? pair[1] : pair[0];
}

/**
 * Named areas of the board, largest first.
 *
 * @param {object|null} index buildBoardIndex output
 * @returns {Array<{
 *   id: string, groupIds: string[], label: string, detail: string, role: string,
 *   instances: number, componentKeys: string[], refdes: string[],
 *   box: object, fromBoardFile: boolean, lead: object|null,
 * }>}
 */
export function boardRegions(index) {
  const groups = Array.isArray(index?.groups) ? index.groups : [];
  const byKey = index?.componentBySourceId;
  if (!groups.length || !byKey) return [];

  // Only groups that own parts are areas. A bare `<group>` used to rotate a
  // child owns nothing and would draw an empty box over its child's box.
  const owning = groups.filter((group) => group.componentKeys.length > 0);

  // Merge identical siblings, but only past the threshold — see the header.
  const buckets = new Map();
  for (const group of owning) {
    const components = group.componentKeys.map((key) => byKey.get(key)).filter(Boolean);
    const signature = `${group.parentId}::${groupSignature(components)}`;
    const bucket = buckets.get(signature);
    if (bucket) bucket.push({ group, components });
    else buckets.set(signature, [{ group, components }]);
  }

  const regions = [];
  for (const members of buckets.values()) {
    const merged = members.length >= MERGE_THRESHOLD ? [members] : members.map((member) => [member]);
    for (const set of merged) {
      const componentKeys = [];
      const components = [];
      const groupIds = [];
      let box = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
      let fromBoardFile = false;
      for (const member of set) {
        groupIds.push(member.group.id);
        if (member.group.isRoot) fromBoardFile = true;
        for (const key of member.group.componentKeys) componentKeys.push(key);
        for (const component of member.components) components.push(component);
        box = unionBox(box, member.group.pcbBox);
      }
      const lead = leadComponent(components);
      const role = lead ? partRole(lead).role : "other";
      const instances = set.length;
      const refdes = components
        .map((component) => String(component.refdes || ""))
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }));
      regions.push({
        id: groupIds.join("+"),
        groupIds,
        role,
        label: labelFor(role, instances),
        detail: describe({ lead, components, instances, fromBoardFile }),
        instances,
        componentKeys,
        refdes,
        box,
        fromBoardFile,
        lead: lead || null,
      });
    }
  }

  // Loudest role first, then by how much board it covers — the reading order a
  // person uses on a layout they have never seen.
  return regions.sort(
    (a, b) => rankOf(a.role) - rankOf(b.role) || boxArea(b.box) - boxArea(a.box),
  );
}

function describe({ lead, components, instances }) {
  const bits = [];
  if (lead) {
    const name = partPlainName(lead);
    const mpn = String(lead.mpn || "").trim();
    bits.push(mpn && mpn.toLowerCase() !== name.toLowerCase() ? `${name} (${mpn})` : name);
  }
  if (instances > 1) bits.push(`${instances}×`);
  const count = components.length;
  if (count > 1) bits.push(`${count} parts`);
  return bits.join(" · ");
}

/** Area of a box in mm², 0 when the box never grew. */
export function boxArea(box) {
  if (!boxIsReal(box)) return 0;
  return Math.max(0, box.maxX - box.minX) * Math.max(0, box.maxY - box.minY);
}

/** Regions that have somewhere to be drawn. */
export function drawableRegions(regions) {
  return (Array.isArray(regions) ? regions : []).filter((region) => boxIsReal(region.box));
}

/** A room whose box covers this much of the board is a frame around everything. */
export const ROOM_MAX_BOARD_FRACTION = 0.92;
/** Below this many screen pixels the label would sit over its own room's copper. */
export const ROOM_MIN_LABEL_PX = { width: 44, height: 14 };

/**
 * The rooms to paint, in screen coordinates, largest first.
 *
 * Screen space rather than board space so the outline stays one pixel and the
 * label stays legible at any zoom — the same trick the selection and flash
 * outlines already use. Three rules stop the overlay becoming clutter rather
 * than a map, and each is a case a real board hits:
 *
 *   · a room covering essentially the whole board (harness-puck's LED ring is
 *     60mm across on a 70mm board) is a frame around everything and is dropped
 *   · a room too small to hold its own label keeps the outline and loses the
 *     text rather than printing it over the pads it is naming
 *   · largest first, so a small room's label is never painted underneath a
 *     bigger room's outline
 *
 * @param {Array<object>} regions boardRegions() output
 * @param {{view: object, boardBox: object|null, margin?: number}} options
 */
export function roomOverlay(regions, { view, boardBox = null, margin = 0.6 } = {}) {
  const list = Array.isArray(regions) ? regions : [];
  if (!list.length || !view) return [];
  const boardSize = boxIsReal(boardBox)
    ? (boardBox.maxX - boardBox.minX) * (boardBox.maxY - boardBox.minY)
    : 0;
  const out = [];
  for (const region of list) {
    if (!boxIsReal(region.box)) continue;
    const area = boxArea(region.box);
    if (boardSize > 0 && area > boardSize * ROOM_MAX_BOARD_FRACTION) continue;
    const rect = boxToScreenRect(view, inflateBox(region.box, margin));
    out.push({
      id: region.id,
      label: region.instances > 1 ? `${region.label} ×${region.instances}` : region.label,
      rect,
      area,
      showLabel: rect.width >= ROOM_MIN_LABEL_PX.width && rect.height >= ROOM_MIN_LABEL_PX.height,
    });
  }
  return out.sort((a, b) => b.area - a.area);
}
