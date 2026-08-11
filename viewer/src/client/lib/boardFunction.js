// boardFunction — "does this board do what I asked?"
//
// `boardVerdict` answers *can I get this made?*. For someone who cannot read a
// schematic those are completely different questions, and only the first one
// has ever been answered here. Someone types "a coaster that reminds me to
// drink water", gets a green orderable board, and has no way to check it. If
// the sense electrode was never wired to a pin they find out in two weeks and
// eighty-five dollars.
//
// **Derived, never narrated.** The tempting version of this feature is a model
// looking at a board and saying "this looks like it does what you wanted".
// That is the one failure mode that makes the feature worse than nothing,
// because it manufactures confidence exactly where the user cannot check. So
// every claim in here is a fact about the netlist:
//
//   · a part is on a net, or it is not
//   · that net reaches the microcontroller on a named pin, or it does not
//   · that net is a power rail, or it is not
//
// Where the chain does not close, the row says so. "We cannot confirm this
// from the design" is a real answer and a useful one; a confident sentence
// about a wire that does not exist is neither.
//
// What this cannot tell you, and says so on screen: whether the firmware
// works, whether a resistor value suits your use, or whether the thing you
// meant by "water sensor" is what got built. It can tell you what is wired to
// what, which is the question nobody could answer before.

import { plural } from "./plainLanguage.js";
import { partPlainName, partRole } from "./plainLanguage.js";

/** Net names that are plumbing on every board and never a feature. */
const HOUSEKEEPING = /^(GND|AGND|DGND|VSS|VDD|VCC|V\d|V\d_\d|VBUS|VBAT|3V3|5V)$/i;

/**
 * The microcontroller, if the board has one. Chosen by role (the same MPN
 * table the parts list uses), and among candidates by pad count — on a board
 * with an MCU and a memory chip the MCU is the one with fifty-six pads.
 */
export function findBrain(index) {
  const components = Array.isArray(index?.components) ? index.components : [];
  let best = null;
  for (const component of components) {
    if (partRole(component).role !== "brain") continue;
    if (!best || component.pads > best.pads) best = component;
  }
  return best;
}

/** Every net a component sits on, as net rows rather than keys. */
function netsOf(index, component) {
  const out = [];
  for (const key of component?.netKeys || []) {
    const net = index?.netByKey?.get(key);
    if (net) out.push(net);
  }
  return out;
}

/** True for a rail or a ground — power is not a feature, it is a precondition. */
export function isRailNet(net) {
  if (!net) return false;
  if (net.isGround || net.isPower) return true;
  return HOUSEKEEPING.test(String(net.name || ""));
}

/**
 * The signals the microcontroller owns: one row per non-rail net that touches
 * it, with the pin names it uses and everything else sitting on that net.
 *
 * This is the most direct honest answer to "is my sensor connected?". A user
 * who asked for a button and finds no row whose parts include a button has
 * their answer, and it came from the netlist rather than from a paraphrase.
 */
export function brainSignals(index, brain) {
  if (!index || !brain) return [];
  const ownGroup = String(brain.groupId || "");
  const rows = [];
  for (const net of netsOf(index, brain)) {
    if (isRailNet(net)) continue;
    const pins = (brain.portNamesByNetKey?.get(net.key) || []).slice().sort();
    const others = [];
    for (const key of net.componentKeys || []) {
      if (key === brain.key) continue;
      const component = index.componentBySourceId.get(key);
      if (component) others.push(component);
    }
    others.sort((a, b) =>
      String(a.refdes).localeCompare(String(b.refdes), undefined, { numeric: true, sensitivity: "base" }),
    );
    // A signal whose whole world is the chip's own block is the flash, the
    // crystal, the reset button — housekeeping the block already guarantees.
    // Mixing those into the list buries the four signals the user asked for
    // under twelve they did not.
    const internal =
      others.length > 0 && ownGroup !== "" && others.every((component) => component.groupId === ownGroup);
    rows.push({
      netKey: net.key,
      net: net.name,
      unnamed: Boolean(net.unnamed),
      pins,
      internal,
      others: others.map((component) => ({
        key: component.key,
        refdes: component.refdes,
        name: partPlainName(component),
        role: partRole(component).role,
      })),
      // A net with nothing on it but the MCU pin is a pin driving nothing. It
      // is not automatically a fault — plenty of designs leave a pin named for
      // later — but it is exactly the shape of "the sensor was never wired".
      goesNowhere: others.length === 0,
    });
  }
  return rows.sort((a, b) => a.net.localeCompare(b.net, undefined, { numeric: true }));
}

/** The three buckets the signal list reads as: what left the chip, what stayed
 *  inside its own block, and what is attached to nothing at all. */
export function splitSignals(signals) {
  const list = Array.isArray(signals) ? signals : [];
  return {
    external: list.filter((row) => !row.internal && !row.goesNowhere),
    internal: list.filter((row) => row.internal),
    empty: list.filter((row) => row.goesNowhere),
  };
}

/** The rails, with who is on them. Answers "does the chip have power". */
export function railRows(index, brain) {
  const nets = Array.isArray(index?.nets) ? index.nets : [];
  const rows = [];
  for (const net of nets) {
    if (!isRailNet(net)) continue;
    const parts = net.componentKeys ? net.componentKeys.size : 0;
    rows.push({
      netKey: net.key,
      net: net.name,
      isGround: Boolean(net.isGround),
      parts,
      // Whether the brain is on this rail is the single fact that matters, and
      // it is a set membership test, not an opinion.
      feedsBrain: Boolean(brain && net.componentKeys?.has(brain.key)),
    });
  }
  return rows.sort((a, b) => Number(a.isGround) - Number(b.isGround) || b.parts - a.parts);
}

const STATUS = Object.freeze({
  BRAIN: "brain",
  SIGNAL: "signal",
  POWER: "power",
  // Joined to other parts, but nothing on those nets reaches the
  // microcontroller. Distinct from ISOLATED, which is joined to nothing at all
  // — calling a sensor wired to a connector "isolated" would be false.
  LINKED: "linked",
  ISOLATED: "isolated",
});

/**
 * One row per area of the board: what is there, and whether the program can
 * actually reach it.
 *
 * @param {object|null} index
 * @param {Array<object>} regions boardRegions() output
 * @returns {Array<object>}
 */
export function functionRows(index, regions) {
  const brain = findBrain(index);
  const list = Array.isArray(regions) ? regions : [];
  const rows = [];

  for (const region of list) {
    const components = region.componentKeys
      .map((key) => index?.componentBySourceId?.get(key))
      .filter(Boolean);
    const holdsBrain = Boolean(brain && region.componentKeys.includes(brain.key));

    const inRegion = new Set(region.componentKeys);
    const signals = new Map();
    const rails = new Map();
    const neighbours = new Set();
    for (const component of components) {
      for (const net of netsOf(index, component)) {
        if (isRailNet(net)) {
          if (!rails.has(net.key)) rails.set(net.key, net);
          continue;
        }
        for (const key of net.componentKeys || []) {
          if (!inRegion.has(key) && key !== brain?.key) neighbours.add(key);
        }
        // A signal counts for this region only when it leaves it — a net that
        // never touches the microcontroller cannot be read or driven by the
        // program, and saying otherwise is the exact overclaim to avoid.
        if (brain && net.componentKeys?.has(brain.key) && !holdsBrain) {
          const pins = (brain.portNamesByNetKey?.get(net.key) || []).slice().sort();
          signals.set(net.key, { netKey: net.key, net: net.name, pins });
        }
      }
    }

    const signalList = [...signals.values()].sort((a, b) => a.net.localeCompare(b.net, undefined, { numeric: true }));
    const railList = [...rails.values()]
      .map((net) => ({
        netKey: net.key,
        net: net.name,
        isGround: Boolean(net.isGround),
        // Sharing a rail with the microcontroller is how a part gets power on
        // the same supply the chip runs from. Set membership, not a judgement.
        sharedWithBrain: Boolean(brain && net.componentKeys?.has(brain.key)),
      }))
      .sort((a, b) => Number(a.isGround) - Number(b.isGround) || a.net.localeCompare(b.net));

    let status = STATUS.ISOLATED;
    if (holdsBrain) status = STATUS.BRAIN;
    else if (signalList.length) status = STATUS.SIGNAL;
    else if (railList.length) status = STATUS.POWER;
    else if (neighbours.size) status = STATUS.LINKED;

    rows.push({
      id: region.id,
      region,
      label: region.label,
      detail: region.detail,
      refdes: region.refdes,
      status,
      signals: signalList,
      rails: railList,
      sentence: sentenceFor({
        status,
        region,
        signals: signalList,
        rails: railList,
        brain,
        index,
        role: region.role,
        neighbours: [...neighbours]
          .map((key) => index?.componentBySourceId?.get(key)?.refdes)
          .filter(Boolean)
          .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" })),
      }),
      // The one bit the honesty banner counts: did the chain close?
      confirmed: status === STATUS.BRAIN || status === STATUS.SIGNAL,
    });
  }

  return rows;
}

function joinNets(signals) {
  const bits = signals.map((signal) =>
    signal.pins.length ? `${signal.net} → ${signal.pins.join("/")}` : signal.net,
  );
  if (bits.length <= 3) return bits.join(", ");
  return `${bits.slice(0, 3).join(", ")} and ${bits.length - 3} more`;
}

/** Roles for which power-and-ground is the whole job — a regulator has no
 *  signal to send, and saying "the program cannot reach it" about one would
 *  read as a fault where there is none. */
const PLUMBING_ROLES = new Set(["power_reg", "power_in", "protection", "passive", "mechanical", "clock"]);

function sentenceFor({ status, region, signals, rails, brain, index, role, neighbours = [] }) {
  if (status === STATUS.BRAIN) {
    const split = splitSignals(brainSignals(index, brain));
    return split.external.length
      ? `Runs your program. ${plural(split.external.length, "signal")} of its own leave it for the rest of the board.`
      : "Runs your program. Nothing outside its own block is wired to any of its pins, so it has nothing to control.";
  }
  if (status === STATUS.SIGNAL) {
    const brainName = brain?.refdes ? ` (${brain.refdes})` : "";
    return `Wired to the brain${brainName} on ${joinNets(signals)}.`;
  }
  if (status === STATUS.POWER) {
    const shared = rails.filter((rail) => rail.sharedWithBrain && !rail.isGround).map((rail) => rail.net);
    const own = rails.filter((rail) => !rail.sharedWithBrain && !rail.isGround).map((rail) => rail.net);
    const where = shared.length
      ? `Shares ${shared.join(" and ")} with the brain.`
      : own.length
        ? `On ${own.join(" and ")}, which the brain is not on.`
        : "On ground only.";
    if (role === "indicator") {
      return `${where} It is wired to a rail rather than a pin, so it is lit whenever the board has power and the program cannot change it.`;
    }
    if (PLUMBING_ROLES.has(role) || !brain) return `${where} Power and ground is all this part needs.`;
    return `${where} Nothing carries a signal from here to the brain, so the program can neither read it nor drive it.`;
  }
  if (status === STATUS.LINKED) {
    const to = neighbours.slice(0, 6).join(", ");
    const more = neighbours.length > 6 ? ` and ${neighbours.length - 6} more` : "";
    return `Joined to ${to}${more}, but nothing carries a signal from here to the brain. We cannot confirm the program can use it.`;
  }
  const count = region?.componentKeys?.length || 0;
  return count
    ? "Nothing else on the board connects to it. We cannot confirm this does anything."
    : "Nothing is in this area.";
}

/**
 * The loose ends: parts wired to nothing, and nets with only one thing on
 * them. Both are cheap to compute and both are the shape of the failure this
 * whole tab exists for — a thing that is on the board but not in the circuit.
 *
 * Parts with no pins at all (a mounting hole, a fiducial) are not loose ends;
 * they were never meant to carry a signal.
 */
export function looseEnds(index) {
  const components = Array.isArray(index?.components) ? index.components : [];
  const unconnected = [];
  for (const component of components) {
    if (!component.ports?.length) continue;
    if (component.netKeys && component.netKeys.size > 0) continue;
    unconnected.push({
      key: component.key,
      refdes: component.refdes,
      name: partPlainName(component),
    });
  }

  const dangling = [];
  for (const net of Array.isArray(index?.nets) ? index.nets : []) {
    const parts = net.componentKeys ? net.componentKeys.size : 0;
    if (parts > 1 || !net.name || net.unnamed) continue;
    const only = [...(net.componentKeys || [])]
      .map((key) => index.componentBySourceId.get(key))
      .filter(Boolean)
      .map((component) => component.refdes);
    dangling.push({ netKey: net.key, net: net.name, parts, only });
  }

  return { unconnected, dangling };
}

/**
 * The one line at the top: how much of the board we could tie to a pin.
 *
 * Deliberately a count and not a grade. "8 of 9 areas confirmed" is a fact;
 * "this board looks right" is a claim nobody here is entitled to make.
 */
export function functionSummary(rows, { brain = null } = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const total = list.length;
  const confirmed = list.filter((row) => row.confirmed).length;
  // Joined-to-nothing and joined-to-something-that-is-not-the-brain are both
  // "we could not confirm the program can use this", which is the thing the
  // headline is for. They stay separable in the rows.
  const isolated = list.filter(
    (row) => row.status === STATUS.ISOLATED || row.status === STATUS.LINKED,
  ).length;
  const powerOnly = list.filter((row) => row.status === STATUS.POWER).length;

  if (!total) {
    return {
      tone: "unknown",
      headline: "Nothing to trace yet",
      line: "This reads the netlist of a built board. Build one and every area shows up here with what it is wired to.",
      total,
      confirmed,
      isolated,
      powerOnly,
    };
  }
  if (!brain) {
    return {
      tone: "unknown",
      headline: "No microcontroller on this board",
      line: "Nothing here runs a program, so there are no pins to trace signals back to. The areas below are still listed with the rails they sit on.",
      total,
      confirmed,
      isolated,
      powerOnly,
    };
  }
  if (isolated) {
    return {
      tone: "gap",
      headline: `${plural(isolated, "area")} we could not tie back to the brain`,
      line: `${confirmed} of ${total} areas reach the brain on a named pin${
        powerOnly
          ? `, ${powerOnly} ${powerOnly === 1 ? "carries" : "carry"} power and ground only`
          : ""
      }. The rest are below, with what we could and could not confirm.`,
      total,
      confirmed,
      isolated,
      powerOnly,
    };
  }
  // Not a score. Every area is joined to the circuit; the split between "the
  // program can reach it" and "power only" is a fact about the design, not a
  // mark out of nine, and writing it as a fraction reads as one.
  return {
    tone: "traced",
    headline: "Every area is joined to the rest of the board",
    line: `${confirmed} of ${total} reach the brain on a named pin${
      powerOnly
        ? powerOnly === 1
          ? "; the other one carries power and ground, which is all it needs"
          : `; the other ${powerOnly} carry power and ground, which is all they need`
        : ""
    }.`,
    total,
    confirmed,
    isolated,
    powerOnly,
  };
}

export { STATUS as FUNCTION_STATUS };
