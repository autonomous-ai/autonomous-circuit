// boardViolations — give every finding a place on the board.
//
// The `.board.json` sidecar is the severity authority (contract §1:
// `{part, kind, detail, severity}`, severity closed). It carries no
// coordinates, so a Messages panel built from it alone can list a violation but
// cannot take you to it. Circuit JSON does carry coordinates: its `*_error` /
// `*_warning` elements name `pcb_component_ids`, `pcb_smtpad_ids`,
// `pcb_port_ids`, sometimes an explicit `center`. This module joins the two —
// sidecar row for the words and the severity, circuit-json element (or a
// refdes/net-name match) for the location — and reports honestly when a finding
// simply cannot be placed.
//
// DOM-free; node:test covers it directly.

import { boxIsReal, elementId, pcbElementBox, unionBox } from "./boardIndex.js";

const SEVERITY_RANK = { error: 0, warning: 1, info: 2 };

/** Sort key: errors first, then warnings, then info; stable within a severity. */
export function severityRank(severity) {
  const rank = SEVERITY_RANK[String(severity || "").toLowerCase()];
  return rank === undefined ? 1 : rank;
}

const ID_FIELDS = [
  "pcb_component_ids",
  "pcb_component_id",
  "pcb_smtpad_ids",
  "pcb_smtpad_id",
  "pcb_pad_ids",
  "pcb_port_ids",
  "pcb_port_id",
  "pcb_trace_ids",
  "pcb_trace_id",
  "pcb_via_ids",
  "source_component_id",
  "source_trace_id",
];

function idsOf(element) {
  const out = [];
  for (const field of ID_FIELDS) {
    const value = element?.[field];
    if (typeof value === "string" && value) out.push(value);
    else if (Array.isArray(value)) for (const item of value) if (typeof item === "string" && item) out.push(item);
  }
  return out;
}

/** Every `*_error` / `*_warning` element in the circuit json. */
export function circuitFindings(index) {
  const out = [];
  for (const element of index?.elements || []) {
    const type = String(element?.type || "");
    if (!type.endsWith("_error") && !type.endsWith("_warning")) continue;
    out.push(element);
  }
  return out;
}

/**
 * The PCB box a circuit-json finding points at: its own `center` if it has one,
 * otherwise the union of every element it names.
 */
export function findingBox(index, finding) {
  let box = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
  const center = finding?.center;
  if (Number.isFinite(Number(center?.x)) && Number.isFinite(Number(center?.y))) {
    const x = Number(center.x);
    const y = Number(center.y);
    box = { minX: x - 0.5, minY: y - 0.5, maxX: x + 0.5, maxY: y + 0.5 };
  }
  for (const id of idsOf(finding)) {
    const element = index?.byId?.get(id);
    if (element) box = unionBox(box, pcbElementBox(element));
    const component = index?.componentByPcbId?.get(id) || index?.componentBySourceId?.get(id);
    if (component) box = unionBox(box, component.pcbBox);
  }
  return box;
}

// "R1", "U12", "LED1", "J1", "C3", "H2", "SW1", "TP4" — a refdes is letters
// then digits, nothing else.
const REFDES_RE = /^[A-Z]{1,4}\d{1,4}$/;
const BRACKET_RE = /\[([^\]]+)\]/g;
const QUOTED_RE = /'([^']+)'|"([^"]+)"/g;
const NAME_ATTR_RE = /name="\.?([A-Za-z]{1,4}\d{1,4})"/g;

/** Every token in a string that could be a refdes or a net name. */
function candidateTokens(text) {
  const out = [];
  const push = (value) => {
    const token = String(value || "").trim();
    if (token) out.push(token);
  };
  const source = String(text || "");
  // Bracketed and quoted spans first — they are the strongest signal ("Track
  // [GND] on F.Cu", "Global Label 'TR_J1_cc2r'") — then their inner words, so
  // "pcb_port[.R1 > .pin1]" also yields "R1".
  const inner = [];
  for (const match of source.matchAll(BRACKET_RE)) {
    push(match[1]);
    inner.push(match[1]);
  }
  for (const match of source.matchAll(QUOTED_RE)) {
    push(match[1] ?? match[2]);
    inner.push(match[1] ?? match[2]);
  }
  for (const match of source.matchAll(NAME_ATTR_RE)) push(match[1]);
  const words = (value) => {
    for (const token of String(value || "").split(/[\s,()<>/[\]]+/)) push(token.replace(/^\.+|[.:;]+$/g, ""));
  };
  words(source);
  for (const span of inner) words(span);
  return out;
}

/**
 * Where a sidecar warning lives. Order matters: an explicit refdes beats a net
 * name, because "V5" is both a net and (in KiCad's DRC prose) a power-symbol
 * reference — the part that carries copper is the more useful jump target.
 *
 * @returns {{kind: "component"|"net"|"board"|"none", key: string, label: string}}
 */
export function locateWarning(index, warning) {
  const none = { kind: "none", key: "", label: "" };
  if (!index) return none;
  const part = String(warning?.part || "").trim();
  const detail = String(warning?.detail || "");

  if (part && part.toLowerCase() === "board") return { kind: "board", key: "", label: "board" };

  const tryComponent = (token) => {
    const component = index.componentByRefdes.get(token);
    return component ? { kind: "component", key: component.key, label: component.refdes } : null;
  };
  const tryNet = (token) => {
    const net = index.netByName.get(token);
    return net ? { kind: "net", key: net.key, label: net.name } : null;
  };

  if (part && REFDES_RE.test(part)) {
    const hit = tryComponent(part);
    if (hit) return hit;
  }
  const exactNet = part ? tryNet(part) : null;

  for (const token of candidateTokens(part)) {
    const hit = tryComponent(token);
    if (hit) return hit;
  }
  if (exactNet) return exactNet;
  for (const token of candidateTokens(part)) {
    const hit = tryNet(token);
    if (hit) return hit;
  }
  for (const token of candidateTokens(detail)) {
    const hit = tryComponent(token);
    if (hit) return hit;
  }
  for (const token of candidateTokens(detail)) {
    const hit = tryNet(token);
    if (hit) return hit;
  }
  return none;
}

/**
 * The Messages panel model: one row per sidecar warning, sorted by severity,
 * each carrying a selection to make and a PCB box to zoom to (when one exists).
 *
 * `warnings` is the already-normalized list from boardData.normalizeWarnings —
 * this module never re-reads the sidecar so the severity rules stay in one
 * place.
 *
 * @param {object|null} index buildBoardIndex output (may be null pre-fetch)
 * @param {Array<{part:string,kind:string,detail:string,severity:string}>} warnings
 * @returns {Array<object>} rows with {id, part, kind, detail, severity, target, box, locatable}
 */
export function buildMessages(index, warnings) {
  const list = Array.isArray(warnings) ? warnings : [];
  // A circuit-json finding whose message matches the sidecar detail gives the
  // exact geometry — try that before falling back to name matching.
  const byMessage = new Map();
  for (const finding of circuitFindings(index)) {
    const message = String(finding.message || "").trim();
    if (message && !byMessage.has(message)) byMessage.set(message, finding);
  }

  const rows = list.map((warning, order) => {
    const detail = String(warning.detail || "").trim();
    const finding = byMessage.get(detail) || null;
    let target = locateWarning(index, warning);
    let box = finding ? findingBox(index, finding) : { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };

    if (finding && target.kind === "none") {
      // The ids a finding names are usually primitives (a pad, a via), not the
      // component — walk the element→component map as well as the direct ones.
      let component = null;
      for (const id of idsOf(finding)) {
        const ownerKey = index?.componentKeyByElementId?.get(id);
        component =
          index?.componentByPcbId?.get(id) ||
          index?.componentBySourceId?.get(id) ||
          (ownerKey ? index?.componentBySourceId?.get(ownerKey) : null);
        if (component) break;
      }
      if (component) target = { kind: "component", key: component.key, label: component.refdes };
    }
    if (!boxIsReal(box)) {
      if (target.kind === "component") {
        box = index.componentBySourceId.get(target.key)?.pcbBox || box;
      } else if (target.kind === "net") {
        box = index.netByKey.get(target.key)?.pcbBox || box;
      } else if (target.kind === "board") {
        box = index.boardBox;
      }
    }

    return {
      id: `${warning.severity}:${warning.kind}:${warning.part}:${order}`,
      order,
      part: String(warning.part || ""),
      kind: String(warning.kind || ""),
      detail,
      severity: String(warning.severity || "warning"),
      findingId: finding ? elementId(finding) : "",
      target,
      box: boxIsReal(box) ? box : null,
      locatable: boxIsReal(box),
    };
  });

  rows.sort((a, b) => severityRank(a.severity) - severityRank(b.severity) || a.order - b.order);
  return rows;
}

/** Counts for the panel header: {error, warning, info, total, locatable}. */
export function messageCounts(rows) {
  const counts = { error: 0, warning: 0, info: 0, total: 0, locatable: 0 };
  for (const row of Array.isArray(rows) ? rows : []) {
    counts.total += 1;
    if (row.locatable) counts.locatable += 1;
    const severity = String(row.severity || "warning").toLowerCase();
    if (counts[severity] === undefined) counts.warning += 1;
    else counts[severity] += 1;
  }
  return counts;
}
