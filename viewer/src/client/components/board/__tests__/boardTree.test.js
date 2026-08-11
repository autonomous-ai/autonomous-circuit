import assert from "node:assert/strict";
import test from "node:test";

import {
  ancestorIds,
  buildBoardChildren,
  buildBoardTree,
  expansionForNode,
  filterTree,
  findSelectionNodeId,
  groupComponents,
  groupNets,
  netClass,
  refdesGroupLabel,
  refdesPrefix,
  seg,
  visibleRows,
} from "../boardTree.js";

// A stand-in for the shape buildBoardIndex returns — only the fields the tree
// reads. Keeping it hand-written (rather than running the real indexer) is
// deliberate: it documents the exact contract between the two modules.
function fakeIndex() {
  return {
    stats: { elements: 42, components: 5, nets: 4 },
    components: [
      { key: "sc_r10", refdes: "R10", value: "10kΩ" },
      { key: "sc_r2", refdes: "R2", value: "1kΩ" },
      { key: "sc_u1", refdes: "U1", mpn: "ESP32-S3-WROOM-1" },
      { key: "sc_j1", refdes: "J1", ftype: "usb_c" },
      { key: "sc_x", refdes: "", value: "" },
    ],
    nets: [
      { key: "n_gnd", name: "GND", isGround: true, isPower: false, pinCount: 9 },
      { key: "n_3v3", name: "3V3", isGround: false, isPower: true, pinCount: 6 },
      { key: "n_sda", name: "SDA", isGround: false, isPower: false, pinCount: 3 },
      { key: "n_x", name: "N$1", isGround: false, isPower: false, pinCount: 2, unnamed: true },
    ],
  };
}

test("refdesPrefix takes the leading letters, uppercased; junk lands in ?", () => {
  assert.equal(refdesPrefix("U12"), "U");
  assert.equal(refdesPrefix("tp3"), "TP");
  assert.equal(refdesPrefix("LED4"), "LED");
  assert.equal(refdesPrefix(""), "?");
  assert.equal(refdesPrefix("12"), "?");
  assert.equal(refdesPrefix(null), "?");
});

test("refdesGroupLabel names known prefixes and passes unknown ones through", () => {
  assert.equal(refdesGroupLabel("U"), "Integrated circuits");
  assert.equal(refdesGroupLabel("SW"), "Switches");
  assert.equal(refdesGroupLabel("ZZ"), "ZZ");
  assert.equal(refdesGroupLabel("?"), "Unlabelled");
});

test("groupComponents buckets by prefix, orders buckets by reading order, sorts members numerically", () => {
  const groups = groupComponents(fakeIndex().components);
  assert.deepEqual(
    groups.map((g) => g.prefix),
    ["U", "R", "J", "?"],
  );
  // R2 before R10 — the whole reason for the numeric collator.
  assert.deepEqual(
    groups.find((g) => g.prefix === "R").components.map((c) => c.refdes),
    ["R2", "R10"],
  );
  assert.equal(groups.find((g) => g.prefix === "?").components.length, 1);
});

test("groupComponents tolerates a missing/empty list", () => {
  assert.deepEqual(groupComponents(undefined), []);
  assert.deepEqual(groupComponents([]), []);
});

test("netClass prefers the source flags, then falls back to the name shape", () => {
  assert.equal(netClass({ isGround: true, name: "whatever" }), "ground");
  assert.equal(netClass({ isPower: true, name: "whatever" }), "power");
  assert.equal(netClass({ name: "GND" }), "ground");
  assert.equal(netClass({ name: "AGND" }), "ground");
  assert.equal(netClass({ name: "VSS" }), "ground");
  assert.equal(netClass({ name: "3V3" }), "power");
  assert.equal(netClass({ name: "+5V" }), "power");
  assert.equal(netClass({ name: "VBUS" }), "power");
  assert.equal(netClass({ name: "SDA" }), "signal");
  assert.equal(netClass({ name: "" }), "signal");
  assert.equal(netClass(null), "signal");
});

test("groupNets always orders Power, Ground, Signal and drops empty buckets", () => {
  const groups = groupNets(fakeIndex().nets);
  assert.deepEqual(
    groups.map((g) => g.id),
    ["power", "ground", "signal"],
  );
  assert.equal(groups.find((g) => g.id === "signal").nets.length, 2);
  assert.deepEqual(groupNets([{ name: "SDA" }]).map((g) => g.id), ["signal"]);
});

test("seg escapes the path separator so ids stay splittable", () => {
  assert.equal(seg("boards/main.tsx"), "boards%2Fmain.tsx");
  assert.equal(seg("100%"), "100%25");
  assert.equal(ancestorIds("a/b/c").length, 3);
  assert.deepEqual(ancestorIds("a/b/c"), ["a", "a/b", "a/b/c"]);
  assert.deepEqual(ancestorIds(""), []);
});

test("buildBoardChildren yields Components and Nets groups with counts", () => {
  const children = buildBoardChildren("p:x/b:main", fakeIndex());
  assert.deepEqual(children.map((c) => c.label), ["Components", "Nets"]);
  assert.equal(children[0].count, 5);
  assert.equal(children[1].count, 4);
  // Every leaf carries a selection descriptor for the shared selection store.
  const ics = children[0].children.find((g) => g.label === "Integrated circuits");
  assert.deepEqual(ics.children[0].select, { kind: "component", key: "sc_u1" });
  const ground = children[1].children.find((g) => g.label === "Ground");
  assert.deepEqual(ground.children[0].select, { kind: "net", key: "n_gnd" });
  assert.equal(ground.children[0].sublabel, "9 pins");
});

test("buildBoardChildren returns nothing when the index has not landed", () => {
  assert.deepEqual(buildBoardChildren("b", null), []);
  assert.deepEqual(buildBoardChildren("b", { stats: { elements: 0 }, components: [], nets: [] }), []);
});

const PROJECTS = [
  { id: "pa", name: "harness-puck" },
  { id: "pb", name: "terminal-keyboard" },
];

function tree(overrides = {}) {
  return buildBoardTree({
    projects: PROJECTS,
    currentProjectId: "pa",
    boardEntries: [{ file: "boards/main.tsx" }],
    selectedFile: "boards/main.tsx",
    index: fakeIndex(),
    boardStatusOf: () => "ready",
    boardLabelOf: () => "main",
    ...overrides,
  });
}

test("buildBoardTree expands only the active project's selected board", () => {
  const roots = tree();
  assert.equal(roots.length, 2);
  const [active, foreign] = roots;
  assert.equal(active.active, true);
  assert.equal(active.children.length, 1);
  assert.equal(active.children[0].selected, true);
  assert.equal(active.children[0].children.length, 2);
  // A foreign project is expandable before its catalog is known — expanding it
  // is what triggers the fetch.
  assert.equal(foreign.expandable, true);
  assert.deepEqual(foreign.children, []);
});

test("buildBoardTree renders a fetched foreign catalog and its loading state", () => {
  const catalogs = new Map([
    ["pb", { status: "ready", boards: [{ file: "boards/main.tsx" }, { file: "boards/rf.tsx" }] }],
  ]);
  const roots = tree({ projectCatalogs: catalogs });
  assert.equal(roots[1].children.length, 2);
  // A foreign board never expands into contents — we only index the open one.
  assert.equal(roots[1].children[0].expandable, false);

  const loading = tree({ projectCatalogs: new Map([["pb", { status: "loading", boards: [] }]]) });
  assert.equal(loading[1].loading, true);
});

test("an unselected board in the active project stays a leaf", () => {
  const roots = tree({
    boardEntries: [{ file: "boards/main.tsx" }, { file: "boards/rf.tsx" }],
    selectedFile: "boards/rf.tsx",
    boardLabelOf: (entry) => entry.file,
  });
  const boards = roots[0].children;
  assert.equal(boards.find((b) => b.boardFile === "boards/main.tsx").expandable, false);
  assert.equal(boards.find((b) => b.boardFile === "boards/rf.tsx").expandable, true);
});

test("filterTree keeps ancestors of a match and the whole subtree of a self-match", () => {
  const roots = tree();
  const hit = filterTree(roots, "gnd");
  // Project → Board → Nets → Ground → GND, and nothing else.
  assert.equal(hit.length, 1);
  assert.equal(hit[0].children[0].children.length, 1);
  const nets = hit[0].children[0].children[0];
  assert.equal(nets.label, "Nets");
  assert.equal(nets.children.length, 1);
  assert.equal(nets.children[0].children[0].label, "GND");

  // A group that matches by its own label keeps everything under it.
  const byGroup = filterTree(roots, "resistors");
  const resistors = byGroup[0].children[0].children[0].children[0];
  assert.equal(resistors.label, "Resistors");
  assert.equal(resistors.children.length, 2);
});

test("filterTree matches the sublabel too, and returns the tree by identity when empty", () => {
  const roots = tree();
  assert.equal(filterTree(roots, "   "), roots);
  const byMpn = filterTree(roots, "esp32");
  assert.equal(byMpn[0].children[0].children[0].children[0].children[0].label, "U1");
});

test("filterTree drops a project with no match at all", () => {
  const roots = tree({
    projectCatalogs: new Map([["pb", { status: "ready", boards: [{ file: "boards/rf.tsx" }] }]]),
    boardLabelOf: (entry) => entry.file,
  });
  assert.equal(filterTree(roots, "harness").length, 1);
});

test("visibleRows descends only into expanded nodes", () => {
  const roots = tree();
  assert.equal(visibleRows(roots, new Set()).length, 2); // two collapsed projects
  const open = new Set(["p:pa", "p:pa/b:boards%2Fmain.tsx"]);
  const rows = visibleRows(roots, open);
  assert.deepEqual(
    rows.map((r) => r.node.label),
    ["harness-puck", "main", "Components", "Nets", "terminal-keyboard"],
  );
  assert.deepEqual(rows.map((r) => r.depth), [0, 1, 2, 2, 0]);
});

test("visibleRows forceExpand opens everything — a hidden search match is not a match", () => {
  const rows = visibleRows(filterTree(tree(), "gnd"), new Set(), { forceExpand: true });
  assert.deepEqual(
    rows.map((r) => r.node.label),
    ["harness-puck", "main", "Nets", "Ground", "GND"],
  );
});

test("findSelectionNodeId locates a leaf, and expansionForNode opens its ancestors only", () => {
  const roots = tree();
  const id = findSelectionNodeId(roots, { kind: "net", key: "n_3v3" });
  assert.equal(id, "p:pa/b:boards%2Fmain.tsx/nets/power/n_3v3");
  assert.equal(findSelectionNodeId(roots, { kind: "net", key: "nope" }), "");
  assert.equal(findSelectionNodeId(roots, null), "");
  const chain = expansionForNode(id);
  assert.deepEqual(chain, [
    "p:pa",
    "p:pa/b:boards%2Fmain.tsx",
    "p:pa/b:boards%2Fmain.tsx/nets",
    "p:pa/b:boards%2Fmain.tsx/nets/power",
  ]);
});
