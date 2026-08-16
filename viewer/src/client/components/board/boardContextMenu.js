// boardContextMenu — what the right-click menu offers, decided in one place.
//
// Altium spends one button on three jobs: menu when idle, cancel when a
// command is running, pan when held and dragged
// ([shortcut-keys/pcb-editors](https://www.altium.com/documentation/altium-designer/shortcut-keys/pcb-editors)).
// The first of those is this file. The other two are gestures and belong to
// the pointer, which is why nothing here reads an event: the canvas decides
// *whether* a menu was asked for and hands over the hit; this module decides
// *what* is in it.
//
// The complete ordered item list of Altium's own PCB context menu is not
// published — **unverified**. Altium documents the commands (`<ObjectType>
// Locked`, Align, Properties, Select Component Nets, Violations) on
// [placement-editing-techniques](https://www.altium.com/documentation/altium-designer/pcb/placement-editing-techniques)
// and [interrogating-resolving-design-violations](https://www.altium.com/documentation/altium-designer/pcb/drc/interrogating-resolving-design-violations),
// not the menu. So the grouping below is ours, ordered by the question the
// user is asking: what is this → select it → change it → what is wrong with it
// → look closer. Cheapest question first, and the group that writes the board
// file sits in the middle where the pointer does not land by accident.
//
// Three states per item, and there is no fourth:
//
//   1. **enabled** — clicking it does the thing, now.
//   2. **disabled with a reason** — one plain sentence naming what would have
//      to change instead. Never a greyed mystery.
//   3. **absent** — the capability does not exist. The reason is written in
//      this file rather than shown as a row the user cannot use.
//
// And the rule that makes state 2 payable: **whenever a write item is
// disabled, `ask-agent` in the same group is enabled.** The chat can make any
// edit our parser cannot, so a refused intention always has somewhere to go.
// That is what "never silently discard a user's action" costs.
//
// Pure: no DOM, no React, no fetch, so `node:test` covers every decision.
// `BoardContextMenu.jsx` renders this model and contains no `if` about
// content.

// Relative, not the `@/` alias — node:test has no bundler to resolve one.
import { boxIsReal, pcbElementBox } from "../../lib/boardIndex.js";
import { formatPoint } from "../../lib/boardRender.js";
import { issueCode, partPlainName, plainIssue } from "../../lib/plainLanguage.js";
import { warningNoteText } from "./boardData.js";
// The refusal wording lives with the drag that first needed it. Importing it
// rather than restating it is deliberate: a menu that explained a locked part
// differently from the canvas would teach the user there are two rules.
import { placementForHit, refusalForHit } from "./placementDrag.js";

/**
 * Every action id the renderer's switch handles. An item that is enabled and
 * carries an action outside this set is an item that does nothing — the one
 * failure this menu may not have (see `WindowMenuBar.jsx`'s Undo, which calls
 * `document.execCommand` into a catch block and teaches the user the app loses
 * their work).
 */
export const BOARD_CONTEXT_ACTIONS = Object.freeze(
  new Set([
    "select",
    "properties",
    "jump",
    "locate",
    "zoom-box",
    "lock",
    "move-exact",
    "prefill",
    "fit",
    "clear-selection",
    "toggle-grid",
    "toggle-units",
    "toggle-edit",
  ]),
);

/** mm per mil, so a typed offset means the same thing as the HUD's readout. */
const MM_PER_MIL = 0.0254;

/** What to call a PCB primitive in a sentence. Altium's own object names. */
const ELEMENT_NAMES = {
  pcb_smtpad: "Pad",
  pcb_plated_hole: "Plated hole",
  pcb_via: "Via",
  pcb_hole: "Hole",
  pcb_trace: "Trace",
  pcb_silkscreen_text: "Silkscreen text",
  pcb_silkscreen_path: "Silkscreen",
  pcb_silkscreen_rect: "Silkscreen",
  pcb_silkscreen_circle: "Silkscreen",
  pcb_cutout: "Cutout",
  pcb_component: "Part",
};

function elementName(type) {
  return ELEMENT_NAMES[String(type || "")] || "Object";
}

/** One menu row, with every field present so the renderer never tests for one. */
function row(spec) {
  return {
    id: "",
    label: "",
    /** Shown under the label when the row is disabled. Empty otherwise. */
    reason: "",
    disabled: false,
    /** True when choosing this row changes `boards/<stem>.tsx`. */
    writes: false,
    action: "",
    payload: null,
    children: null,
    ...spec,
  };
}

/** The selection this hit maps onto, or null when we cannot select it.
 *
 *  `resolveSelection` understands components and nets and nothing else
 *  (`lib/boardIndex.js`), so a bare silkscreen stroke has no selection to make.
 *  Offering "Properties" on one would be a row that lights up and does nothing.
 */
export function targetForHit(hit) {
  if (!hit) return null;
  if (hit.componentKey) return { kind: "component", key: hit.componentKey };
  if (hit.netKey) return { kind: "net", key: hit.netKey };
  return null;
}

function componentFor(index, hit) {
  const key = hit?.componentKey;
  return key ? index?.componentBySourceId?.get(key) || null : null;
}

function netNameFor(index, netKey) {
  if (!netKey) return "";
  return String(index?.netByKey?.get(netKey)?.name || netKey);
}

/** The name of the thing under the cursor, in the user's vocabulary. */
function nameForHit(index, hit, placement) {
  const component = componentFor(index, hit);
  if (component?.refdes) return component.refdes;
  if (placement?.label) return placement.label;
  const net = netNameFor(index, hit?.netKey);
  if (net) return `${elementName(hit?.element?.type)} on ${net}`;
  return elementName(hit?.element?.type);
}

/** The box to zoom to for this hit: the whole part if it is one, else the
 *  primitive. A pad's own box at 200 px/mm is a zoom nobody asked for. */
function boxForHit(index, hit) {
  const component = componentFor(index, hit);
  if (component?.pcbBox && boxIsReal(component.pcbBox)) return component.pcbBox;
  const box = hit?.element ? pcbElementBox(hit.element) : null;
  return box && boxIsReal(box) ? box : null;
}

function pointInBox(point, box) {
  if (!point || !box) return false;
  return point.x >= box.minX && point.x <= box.maxX && point.y >= box.minY && point.y <= box.maxY;
}

/**
 * The findings whose located box covers this point, error first.
 *
 * `buildMessages` already sorts by `severityRank`, so the filter keeps that
 * order rather than re-deriving it — the menu and the Messages panel rank
 * findings the same way or they are two tools.
 */
export function violationsAt(messageRows, point) {
  if (!point) return [];
  return (Array.isArray(messageRows) ? messageRows : []).filter((item) => pointInBox(point, item?.box));
}

/**
 * A typed offset in the document's units, as millimetres.
 *
 * Taken exactly, never snapped: someone who types 0.35 with a 0.5 mm grid
 * meant 0.35. The grid is for the pointer, which cannot be precise; a keyboard
 * already is.
 */
export function offsetToMm(value, units = "mm") {
  // `Number("")` is 0, and an empty field is not a request to move by nothing.
  const text = String(value ?? "").trim();
  if (!text) return null;
  const number = Number(text);
  if (!Number.isFinite(number)) return null;
  return units === "mil" ? number * MM_PER_MIL : number;
}

/** The same value the other way, for filling the dialog's fields. */
export function mmToOffset(mm, units = "mm") {
  const value = Number(mm) || 0;
  return units === "mil" ? value / MM_PER_MIL : value;
}

/**
 * Why the board file cannot be written right now, regardless of what is under
 * the cursor. Checked before the hit is even consulted, because when move mode
 * is off `editor.placements` is empty and every hit would otherwise report the
 * *wrong* reason — "no line in the board file puts this here" — for parts the
 * file plainly places.
 */
function fileGate({ viewing, canEdit, editor }) {
  if (viewing) return "You are looking at an older build — open the latest to change it.";
  if (!canEdit) return "Move parts is off. Turn it on and this edits the board file.";
  if (editor?.state === "loading") return "Still reading the board file.";
  if (editor?.reason) return String(editor.reason);
  if (!editor?.ready) return "The board file is not open for editing.";
  return "";
}

/**
 * The menu for one right-click.
 *
 * @param {{
 *   hit: object|null,            what the pointer resolved to, or null for empty board
 *   hits?: Array<object>,        every candidate under the pointer, ranked, if the caller has them
 *   point?: {x: number, y: number},
 *   pointLabel?: string,         formatted by the caller, echoed verbatim
 *   index?: object|null,
 *   placements?: object,         editor.placements binding
 *   editor?: object,             state only: {ready, state, reason}
 *   messageRows?: Array<object>,
 *   selection?: object|null,
 *   canEdit?: boolean, viewing?: boolean,
 *   showGrid?: boolean, units?: string,
 *   boardName?: string,
 * }} ctx
 * @returns {{header: {label: string, detail: string}, groups: Array<{id: string, items: Array<object>}>}}
 */
export function boardContextMenu(ctx = {}) {
  const {
    hit = null,
    hits = null,
    point = null,
    pointLabel: givenLabel = "",
    index = null,
    placements = null,
    editor = null,
    messageRows = null,
    selection = null,
    canEdit = false,
    viewing = false,
    showGrid = true,
    units = "mm",
    boardName = "",
  } = ctx;

  // Derived here rather than taken from the caller. The parity spec named
  // `formatPoint` so the menu header and the HUD two inches above it cannot
  // print different numbers for the same press; a caller that simply forgot to
  // pass it dropped the coordinate out of the header and shipped an
  // ask-the-agent prefill reading "J1 at : ". A default nobody has to remember
  // cannot be forgotten.
  const pointLabel = givenLabel || (point ? formatPoint(point.x, point.y, units) : "");

  return hit
    ? objectMenu({
        hit,
        hits,
        point,
        pointLabel,
        index,
        placements,
        editor,
        messageRows,
        canEdit,
        viewing,
        units,
      })
    : designSpaceMenu({ pointLabel, selection, canEdit, viewing, showGrid, units, boardName });
}

function objectMenu({
  hit,
  hits,
  point,
  pointLabel,
  index,
  placements,
  editor,
  messageRows,
  canEdit,
  viewing,
  units,
}) {
  const placement = placementForHit(placements, hit);
  const name = nameForHit(index, hit, placement);
  const component = componentFor(index, hit);
  const target = targetForHit(hit);
  const netKey = String(hit.netKey || "");
  const netName = netNameFor(index, netKey);

  const header = {
    label: name,
    detail: [component ? partPlainName(component) : "", String(hit.layer || ""), pointLabel]
      .filter(Boolean)
      .join(" · "),
  };

  const groups = [];

  // --- what is here -------------------------------------------------------
  const here = [];
  const candidates = (Array.isArray(hits) ? hits : [])
    .map((one) => ({ one, target: targetForHit(one) }))
    .filter((entry) => entry.target);
  if (candidates.length > 1) {
    here.push(
      row({
        id: "objects-here",
        label: `Objects under the cursor (${candidates.length})`,
        children: candidates.map((entry, order) =>
          row({
            id: `object-${order}`,
            label: `${elementName(entry.one.element?.type)} · ${nameForHit(index, entry.one, placementForHit(placements, entry.one))}`,
            action: "select",
            payload: { target: entry.target },
          }),
        ),
      }),
    );
  }
  if (target) {
    here.push(
      row({
        id: "properties",
        label: "Properties",
        action: "properties",
        payload: { target },
      }),
    );
  }
  if (here.length) groups.push({ id: "here", items: here });

  // --- select and cross-probe. All of this already works; the menu is only
  // the first place it is written down. The ⌘/Ctrl-click jump in particular is
  // documented nowhere inside the app.
  const select = [];
  if (netKey) {
    select.push(
      row({
        id: "select-net",
        label: `Select the whole net ${netName}`,
        action: "select",
        payload: { target: { kind: "net", key: netKey } },
      }),
    );
  }
  if (target) {
    select.push(
      row({
        id: "show-in-schematic",
        label: "Show it in the schematic",
        action: "jump",
        payload: { target, source: "pcb" },
      }),
    );
  }
  if (select.length) groups.push({ id: "select", items: select });

  // --- change the board file ----------------------------------------------
  //
  // One gate, then one refusal. The gate is about the app's state (older
  // build, move mode off); the refusal is about this particular object (locked,
  // copper, inside a block, placed in code). They are separate questions and
  // running them in the other order gives the wrong answer, because with move
  // mode off there are no bound placements to ask about.
  const gate = fileGate({ viewing, canEdit, editor });
  const refusal = gate ? null : refusalForHit(index, placements, hit);
  const blocked = gate || refusal?.reason || "";
  const locked = Boolean(placement?.locked);
  // A locked placement can always be unlocked — that refusal is the lock doing
  // its job, not a limit of what the file can say.
  const lockBlocked = Boolean(blocked) && !(locked && refusal?.kind === "locked");
  const edit = [];

  if (!canEdit && !viewing) {
    // The one-click way out of the gate above, so a disabled row is never the
    // end of the road.
    edit.push(row({ id: "edit-mode", label: "Turn on Move parts", action: "toggle-edit", payload: { on: true } }));
  }

  edit.push(
    row({
      id: "lock",
      // A lock is worth more to us than to Altium: theirs protects a placement
      // from your own hands, ours lands in the file the next agent reads.
      label: locked ? `Unlock ${placement.label}` : `Lock ${placement ? placement.label : name} in place`,
      reason: lockBlocked ? blocked : "",
      disabled: lockBlocked,
      writes: true,
      action: "lock",
      payload: { placementId: placement?.id || "", locked: !locked },
    }),
  );

  edit.push(
    row({
      id: "move-exact",
      label: `Move ${placement ? placement.label : name} by an exact amount…`,
      reason: blocked,
      disabled: Boolean(blocked),
      writes: true,
      action: "move-exact",
      payload: { placementId: placement?.id || "", units },
    }),
  );

  edit.push(
    row({
      id: "ask-agent",
      label: blocked ? `Ask the agent to change ${name}…` : `Ask the agent about ${name}…`,
      action: "prefill",
      payload: { text: `${name} at ${pointLabel}: ` },
    }),
  );
  groups.push({ id: "edit", items: edit });

  // --- findings. Absent when nothing here is flagged: "Violations (0)" is a
  // worse answer than no row at all.
  const found = violationsAt(messageRows, point);
  if (found.length) {
    groups.push({
      id: "findings",
      items: [
        row({
          id: "violations",
          label: `Violations here (${found.length})`,
          children: [
            ...found.map((item) =>
              row({
                id: `violation-${item.id}`,
                label: plainIssue(issueCode(item)).title,
                reason: item.detail,
                action: "locate",
                payload: { row: item },
              }),
            ),
            row({
              id: "violations-fix",
              label: found.length > 1 ? `Ask the agent to fix these (${found.length})` : "Ask the agent to fix this",
              action: "prefill",
              payload: { text: warningNoteText(found[0]) },
            }),
          ],
        }),
      ],
    });
  }

  // --- view. Fit already has `F`, `Ctrl+PgDn` and a rail button; zoom to
  // *this* has no route at all today.
  const box = boxForHit(index, hit);
  if (box) {
    groups.push({
      id: "view",
      items: [row({ id: "zoom-here", label: "Zoom to this", action: "zoom-box", payload: { box } })],
    });
  }

  return { header, groups };
}

/**
 * Altium's "context menu for the design space" — the shorter menu you get on
 * empty board. Everything on it is a view or a mode, nothing writes, and every
 * callback behind it already exists.
 */
function designSpaceMenu({ pointLabel, selection, canEdit, viewing, showGrid, units, boardName }) {
  const view = [row({ id: "fit", label: "Zoom to fit", action: "fit" })];
  if (selection) {
    view.push(row({ id: "clear-highlight", label: "Clear the highlight", action: "clear-selection" }));
  }

  // Imperative, never a state readout. "Grid: on" as the row that turns the
  // grid OFF is a label promising the opposite of what the click does, and the
  // object menu two functions up already says "Turn on Move parts" — a feature
  // that disagrees with itself teaches the user there are two rules. Our
  // vendored `ui/dropdown-menu` has a `DropdownMenuCheckboxItem`, but a check
  // mark states and an imperative label instructs, and every other row in this
  // menu instructs.
  const display = [
    row({ id: "grid", label: showGrid ? "Hide the grid" : "Show the grid", action: "toggle-grid" }),
    row({
      id: "units",
      label: units === "mil" ? "Show sizes in mm" : "Show sizes in mil",
      action: "toggle-units",
    }),
  ];

  const edit = [
    row({
      id: "edit-mode",
      label: canEdit ? "Turn off Move parts" : "Turn on Move parts",
      reason: viewing ? "You are looking at an older build — open the latest to change it." : "",
      disabled: Boolean(viewing),
      action: "toggle-edit",
      payload: { on: !canEdit },
    }),
    row({
      id: "ask-agent-board",
      label: "Ask the agent about this board…",
      action: "prefill",
      payload: { text: `${boardName || "This board"} at ${pointLabel}: ` },
    }),
  ];

  return {
    header: { label: boardName || "Board", detail: pointLabel },
    groups: [
      { id: "view", items: view },
      { id: "display", items: display },
      { id: "edit", items: edit },
    ],
  };
}

/** Every row in the model, submenu children included. */
export function flattenItems(model) {
  const out = [];
  for (const group of model?.groups || []) {
    for (const item of group.items || []) {
      out.push(item);
      for (const child of item.children || []) out.push(child);
    }
  }
  return out;
}

/** One row by id, or null. Submenu children are searched too. */
export function findItem(model, id) {
  return flattenItems(model).find((item) => item.id === id) || null;
}
