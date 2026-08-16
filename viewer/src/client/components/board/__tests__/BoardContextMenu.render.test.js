// The right-click menu, rendered.
//
// `boardContextMenu.test.js` proves the model: which rows exist, which are
// disabled, what each one says. It cannot prove any of that reaches a screen.
// Between the model and the user sit Radix's controlled `DropdownMenu`, a
// portal, a focus scope and a dismiss layer, and every one of them is a place
// the menu can be correct and invisible. That is the exact shape of the defect
// this repo shipped in 8fb33cd and again in e7f1434: the logic was right, the
// wiring was not, and the suite was green.
//
// So everything below reads the real DOM the app paints — rows out of the
// portal, `data-disabled` off Radix's own item, focus out of
// `document.activeElement` — and nothing reads our source as text.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { key, mount, pointer, settle } from "../../../test/render.js";
import BoardContextMenu from "../BoardContextMenu.jsx";
import { buildBoardIndex, elementId } from "../../../lib/boardIndex.js";
import { bindPlacements, parseBoardSource } from "../boardSource.js";

// The real harness-puck board, the same one boardContextMenu.test.js reads, so
// the model test and the render test cannot be looking at two different
// boards. Loud rather than skipped: a render proof that quietly opts out when
// the board is missing is the false floor this suite has shipped once already.
const REPO = path.resolve(fileURLToPath(new URL("../../../../../..", import.meta.url)));
const BOARDS = path.join(REPO, "examples/harness-puck/boards");
for (const file of ["main.circuit.json", "main.tsx"]) {
  assert.ok(fs.existsSync(path.join(BOARDS, file)), `missing ${path.join(BOARDS, file)}`);
}

const index = buildBoardIndex(JSON.parse(fs.readFileSync(path.join(BOARDS, "main.circuit.json"), "utf8")));
const placements = bindPlacements(
  parseBoardSource(fs.readFileSync(path.join(BOARDS, "main.tsx"), "utf8")).placements,
  index,
);

/** Move mode on, latest build, file open — the state every write row wants. */
const OPEN_TO_EDIT = { canEdit: true, viewing: false, editor: { ready: true, state: "ready", reason: "" } };

/** What the pointer hands over after a right-press-and-release on empty board. */
const EMPTY_BOARD = {
  hit: null,
  hits: [],
  point: { x: 0, y: -29 },
  pointLabel: "0.000, -29.000 mm",
  client: { x: 300, y: 200 },
};

function requestFor(hit, point = { x: 0, y: -29 }, pointLabel = "0.000, -29.000 mm") {
  return { hit, hits: [hit], point, pointLabel, client: { x: 300, y: 200 } };
}

const partHit = (refdes) => {
  const component = index.components.find((one) => one.refdes === refdes);
  assert.ok(component, `${refdes} is not on this board`);
  return { elementId: component.pcbId, element: component.pcb, componentKey: component.key, netKey: "", layer: component.layer };
};

const firstTraceHit = () => {
  const trace = index.pcbDrawables.find((one) => one.type === "pcb_trace");
  assert.ok(trace, "the board has no copper to right-click");
  const id = elementId(trace);
  return {
    elementId: id,
    element: trace,
    componentKey: "",
    netKey: index.netKeyByElementId.get(id) || "",
    layer: String(trace.route?.[0]?.layer || "top"),
  };
};

// The menu is portalled to `document.body`, not into the mount container —
// which is the whole reason a test that only looked at the component's own
// subtree would report an empty menu and pass.
const menuEl = () => document.body.querySelector('[data-slot="board-context-menu"]');
const rowIds = () => [...menuEl().querySelectorAll("[data-id]")].map((node) => node.dataset.id);
const rowEl = (id) => menuEl().querySelector(`[data-id="${id}"]`);
const groupIds = () => [...menuEl().querySelectorAll('[data-slot="board-context-group"]')].map((node) => node.dataset.group);
const headerText = () => menuEl().querySelector('[data-slot="dropdown-menu-label"]').textContent;
const focusedId = () => document.activeElement?.dataset?.id ?? null;

/** `data-disabled` is Radix's own flag, set from our `disabled` prop. */
const isDisabled = (id) => rowEl(id).hasAttribute("data-disabled");
const reasonOf = (id) => rowEl(id).querySelector('[data-slot="board-context-reason"]')?.textContent ?? null;

test("the board's own right-click menu is on screen, and `open` is the only thing that puts it there", async () => {
  const ui = mount(BoardContextMenu, {
    open: false,
    request: EMPTY_BOARD,
    boardName: "harness-puck",
    onOpenChange: () => {},
  });

  // Radix's own ContextMenu cannot be told to open, which is why this is a
  // controlled DropdownMenu. If that ever regresses to an uncontrolled menu,
  // nothing here is on screen until a real `contextmenu` event fires — and the
  // canvas deliberately swallows those.
  assert.equal(menuEl(), null, "a menu nobody asked for is already open");

  ui.set({ open: true });
  await settle();

  assert.ok(menuEl(), "open=true and the menu never reached the DOM");
  assert.deepEqual(groupIds(), ["view", "display", "edit"]);
  assert.deepEqual(rowIds(), ["fit", "grid", "units", "edit-mode", "ask-agent-board"]);
  assert.equal(headerText(), "harness-puck0.000, -29.000 mm");
  // Nothing on the design-space menu writes the board file.
  assert.deepEqual(
    rowIds().filter((id) => rowEl(id).dataset.writes === "true"),
    [],
  );

  ui.set({ open: false });
  await settle();
  assert.equal(menuEl(), null, "the menu outlived open=false");
  assert.deepEqual(ui.errors, []);
  ui.unmount();
});

test("right-clicking a part gets a different menu, headed by the part", async () => {
  const ui = mount(BoardContextMenu, {
    open: true,
    request: requestFor(partHit("J1")),
    index,
    placements,
    ...OPEN_TO_EDIT,
    onOpenChange: () => {},
  });
  await settle();

  assert.deepEqual(groupIds(), ["here", "select", "edit", "view"]);
  assert.deepEqual(rowIds(), ["properties", "show-in-schematic", "lock", "move-exact", "ask-agent", "zoom-here"]);
  // What it is, what it is on, and where the press landed — the header cannot
  // disagree with the HUD because it echoes the label the caller formatted.
  assert.equal(headerText(), "J1USB-C socket · top · 0.000, -29.000 mm");
  // Named after the part, not after "the selection": an EE right-clicks to act
  // on the thing under the cursor.
  assert.match(rowEl("move-exact").textContent, /^Move J1 .*by an exact amount…$/u);

  // And it is genuinely a different menu, not the board one plus extras.
  for (const id of ["fit", "grid", "units", "ask-agent-board"]) {
    assert.equal(rowEl(id), null, `the design-space row "${id}" is on the object menu too`);
  }
  // The two rows that change `boards/main.tsx` are marked as such, because the
  // group that writes has to be visibly different from the group that looks.
  assert.deepEqual(
    rowIds().filter((id) => rowEl(id).dataset.writes === "true"),
    ["lock", "move-exact"],
  );

  assert.deepEqual(ui.errors, []);
  ui.unmount();
});

test("a row for a capability this click does not have is absent, not greyed", async () => {
  // Copper carries a net, so the net row is there and is named.
  const trace = mount(BoardContextMenu, {
    open: true,
    request: requestFor(firstTraceHit()),
    index,
    placements,
    ...OPEN_TO_EDIT,
    onOpenChange: () => {},
  });
  await settle();
  const netName = index.netByKey.get(firstTraceHit().netKey)?.name;
  assert.ok(netName, "the trace under test is on no net");
  assert.equal(rowEl("select-net").textContent, `Select the whole net ${netName}`);
  trace.unmount();

  // A part carries none, so the row is gone rather than disabled: "Select the
  // whole net —" is a worse answer than no row.
  const part = mount(BoardContextMenu, {
    open: true,
    request: requestFor(partHit("J1")),
    index,
    placements,
    ...OPEN_TO_EDIT,
    onOpenChange: () => {},
  });
  await settle();
  assert.equal(rowEl("select-net"), null);
  part.unmount();

  // Same rule on the design-space menu: nothing is highlighted, so there is
  // nothing to clear.
  const bare = mount(BoardContextMenu, { open: true, request: EMPTY_BOARD, boardName: "b", onOpenChange: () => {} });
  await settle();
  assert.equal(rowEl("clear-highlight"), null);
  bare.set({ selection: { kind: "net", key: firstTraceHit().netKey } });
  await settle();
  assert.equal(rowEl("clear-highlight").textContent, "Clear the highlight");
  bare.unmount();
});

test("a write row that cannot run says why, on screen, and leaves a way out", async () => {
  const ui = mount(BoardContextMenu, {
    open: true,
    request: requestFor(partHit("J1")),
    index,
    placements,
    canEdit: false,
    viewing: false,
    onOpenChange: () => {},
  });
  await settle();

  const why = "Move parts is off. Turn it on and this edits the board file.";
  for (const id of ["lock", "move-exact"]) {
    assert.equal(isDisabled(id), true, `"${id}" would write the board file with Move parts off`);
    // The sentence has to be painted, not merely present in the model. Radix
    // fades a disabled row to 50% by default and this menu turns that off
    // precisely so the reason is readable; a reason nobody can read is not a
    // reason.
    assert.equal(reasonOf(id), why);
  }

  // The rule that makes a disabled row payable: a refused intention always has
  // somewhere to go. Both of these are enabled while the writes are not.
  for (const id of ["edit-mode", "ask-agent"]) {
    assert.equal(isDisabled(id), false, `"${id}" is the way out of the gate and it is disabled too`);
    assert.equal(reasonOf(id), null);
  }
  assert.equal(rowEl("edit-mode").textContent, "Turn on Move parts");
  assert.match(rowEl("ask-agent").textContent, /^Ask the agent to change J1…$/u);

  // Turn the gate off and every reason disappears — the reasons are about the
  // app's state, not decoration on the row.
  ui.set(OPEN_TO_EDIT);
  await settle();
  assert.equal(menuEl().querySelector('[data-slot="board-context-reason"]'), null);
  assert.equal(isDisabled("lock"), false);
  assert.equal(isDisabled("move-exact"), false);
  assert.equal(rowEl("edit-mode"), null, "the way out is still offered after the gate opened");

  assert.deepEqual(ui.errors, []);
  ui.unmount();
});

test("Escape and a click on the board behind both close it, and neither reaches the board", async () => {
  const closed = [];
  const ui = mount(BoardContextMenu, {
    open: true,
    request: EMPTY_BOARD,
    boardName: "b",
    onOpenChange: (next) => closed.push(next),
  });
  await settle();

  // The menu is modal, which is what stops the dismissing click also changing
  // the selection on the canvas underneath. Non-modal, closing the menu had a
  // side effect on the board every time.
  assert.equal(document.body.style.pointerEvents, "none");

  key(menuEl(), "Escape");
  await settle();
  assert.deepEqual(closed, [false], "Escape left the menu up");
  ui.set({ open: false });
  await settle();
  assert.equal(document.body.style.pointerEvents, "", "the board stayed inert after the menu closed");
  ui.unmount();

  const away = [];
  const second = mount(BoardContextMenu, {
    open: true,
    request: EMPTY_BOARD,
    boardName: "b",
    onOpenChange: (next) => away.push(next),
  });
  // Radix arms its outside-press listener on a timer, so that the very press
  // that opened the menu cannot close it again. Without settling first, this
  // asserts on a menu that is not yet listening and passes for the wrong
  // reason.
  await settle();
  const elsewhere = document.createElement("div");
  document.body.appendChild(elsewhere);
  pointer(elsewhere, "down", { clientX: 900, clientY: 700 });
  await settle();
  assert.deepEqual(away, [false], "a click away from the menu left it up");
  elsewhere.remove();
  second.unmount();
});

test("the arrow keys walk the rows, skip the ones that cannot run, and Enter chooses", async () => {
  const closed = [];
  const fitted = [];
  const ui = mount(BoardContextMenu, {
    open: true,
    request: EMPTY_BOARD,
    boardName: "b",
    onOpenChange: (next) => closed.push(next),
    onFit: () => fitted.push("fit"),
  });
  await settle();

  const walk = async (from, keyName) => {
    key(from, keyName);
    await settle();
    return focusedId();
  };

  // Nothing is focused until a key asks for it: the board keeps its selection
  // and its highlight while the menu is up.
  assert.equal(focusedId(), null);
  assert.equal(await walk(menuEl(), "ArrowDown"), "fit");
  assert.equal(await walk(document.activeElement, "ArrowDown"), "grid");
  assert.equal(await walk(document.activeElement, "ArrowDown"), "units");
  assert.equal(await walk(document.activeElement, "ArrowUp"), "grid");
  assert.equal(await walk(menuEl(), "End"), "ask-agent-board");
  assert.equal(await walk(menuEl(), "Home"), "fit");

  key(document.activeElement, "Enter");
  await settle();
  assert.deepEqual(fitted, ["fit"], "Enter on a focused row ran nothing");
  assert.deepEqual(closed, [false], "choosing a row left the menu up");
  assert.deepEqual(ui.errors, []);
  ui.unmount();

  // On a gated menu the two disabled writes are stepped over rather than
  // focused, so the keyboard never parks on a row that cannot act.
  const gated = mount(BoardContextMenu, {
    open: true,
    request: requestFor(partHit("J1")),
    index,
    placements,
    canEdit: false,
    onOpenChange: () => {},
  });
  await settle();
  assert.ok(isDisabled("lock") && isDisabled("move-exact"), "this menu was supposed to be gated");

  const visited = [];
  key(menuEl(), "ArrowDown");
  await settle();
  visited.push(focusedId());
  for (let step = 0; step < 4; step += 1) {
    key(document.activeElement, "ArrowDown");
    await settle();
    visited.push(focusedId());
  }
  assert.deepEqual(visited, [
    "properties",
    "show-in-schematic",
    "edit-mode",
    "ask-agent",
    "zoom-here",
  ]);
  gated.unmount();
});
