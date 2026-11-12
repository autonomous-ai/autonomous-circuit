// The Case tab's arithmetic: what a status means, which parts show, where an
// exploded part goes, and what a print rotation asks of the slicer.

import assert from "node:assert/strict";
import test from "node:test";

import {
  COUPON_COLOR, cssColor, defaultVisibility, describeStatus, downloadName, explodeOffset, orderParts, PART_PALETTE,
  partColors, plateTitle, printNote, REFERENCE_COLOR, roleLabel, summaryChips, viewDirection,
} from "../caseView.js";

test("only a passing check says the case fits; stale and failed say not to print yet", () => {
  assert.equal(describeStatus("passed").title, "Fits the board");
  assert.equal(describeStatus("failed", "2 case errors").tone, "bad");
  assert.match(describeStatus("failed", "2 case errors").detail, /^2 case errors/);
  assert.match(describeStatus("stale", "the board changed since the case was checked").detail, /Do not print/);
  assert.equal(describeStatus("unverified", "part of the case could not be measured").tone, "warn");
  assert.match(describeStatus("unverified").detail, /Do not count it as a pass/);
  assert.equal(describeStatus(undefined).title, "No case yet");
});

test("the coupon is listed first and left out of the assembly", () => {
  const parts = [
    { name: "plunger_SW1", role: "plunger" },
    { name: "front", role: "shell" },
    { name: "coupon", role: "coupon" },
    { name: "back", role: "shell" },
  ];
  assert.deepEqual(orderParts(parts).map((p) => p.name), ["coupon", "back", "front", "plunger_SW1"]);
  assert.deepEqual(defaultVisibility(parts), { plunger_SW1: true, front: true, coupon: false, back: true });
  assert.equal(roleLabel("coupon"), "Test-fit coupon");
  assert.equal(roleLabel("whatever"), "Part");
});

test("exploding pulls what is above the board up and what is below down; the coupon goes beside", () => {
  const above = { min: [0, 0, -1.6], max: [0, 0, 12] };
  const below = { min: [0, 0, -9], max: [0, 0, -1.6] };
  assert.deepEqual(explodeOffset({ role: "shell" }, above, 32, 20), [0, 0, 20]);
  assert.deepEqual(explodeOffset({ role: "shell" }, below, 32, 20), [0, 0, -20]);
  // the coupon's left edge lands half a gap right of the case's right edge
  assert.deepEqual(explodeOffset({ role: "coupon" }, { min: [-20, 0, 0], max: [10, 0, 5] }, 32, 20), [62, 0, 0]);
});

test("a print rotation is said in words", () => {
  assert.equal(printNote({ printRotateDeg: [180, 0, 0], supports: false }), "flip it face down on the bed, no supports");
  assert.equal(printNote({ printRotateDeg: [0, 0, 0] }), "print it as modelled");
  assert.equal(printNote({}), "print it as modelled");
  assert.match(printNote({ printRotateDeg: [90, 0, 0] }), /rotate it 90° \/ 0° \/ 0°/);
});

test("download names are safe and say which board", () => {
  assert.equal(downloadName("main", "plunger_SW1", "stl"), "main-plunger_SW1.stl");
  assert.equal(downloadName("my board", "../front", "step"), "my-board-front.step");
});

test("every printed part gets its own colour, stable for the same set; coupons and references stay muted", () => {
  const parts = ["frame_lower", "frame_upper", "thigh_FL", "thigh_FR", "thigh_RL", "thigh_RR", "foot_FL", "foot_FR", "foot_RL", "foot_RR"]
    .map((name) => ({ name, role: "other" }));
  parts.push({ name: "coupon", role: "coupon" }, { name: "ref_DO_NOT_PRINT_servos", role: "other", printable: false });
  const colors = partColors(parts);
  const printed = parts.slice(0, 10).map((p) => colors[p.name]);
  assert.equal(new Set(printed).size, 10, "ten parts, ten colours");
  assert.ok(printed.every((c) => PART_PALETTE.includes(c)));
  assert.equal(colors.coupon, COUPON_COLOR);
  assert.equal(colors.ref_DO_NOT_PRINT_servos, REFERENCE_COLOR);
  assert.deepEqual(partColors([...parts].reverse()), colors, "order of the list does not change colours");
  assert.equal(cssColor(0x4f9de8), "#4f9de8");
  assert.equal(cssColor(0x0000ff), "#0000ff");
});

test("more parts than colours: every part still gets a palette colour", () => {
  const parts = Array.from({ length: PART_PALETTE.length + 3 }, (_, i) => ({ name: `p${i}`, role: "other" }));
  const colors = partColors(parts);
  assert.equal(Object.keys(colors).length, parts.length);
  assert.equal(new Set(Object.values(colors)).size, PART_PALETTE.length);
});

test("summary chips say only what the data has", () => {
  const parts = [
    { name: "front", role: "shell", supports: true },
    { name: "back", role: "shell", supports: false },
    { name: "leg", role: "other" },
    { name: "coupon", role: "coupon", supports: true },
    { name: "ref", role: "other", printable: false },
  ];
  const chips = summaryChips({ parts, plates: [{ index: 1 }, { index: 2 }], state: "passed", sizeMm: [120.4, 80.6, 45] });
  assert.deepEqual(chips.map((c) => [c.id, c.label]), [
    ["parts", "3 parts"],
    ["size", "120 × 81 × 45 mm"],
    ["supports", "1 need support"],
    ["plates", "2 plates"],
    ["status", "Pass"],
  ]);
  assert.match(chips.find((c) => c.id === "supports").title, /1 part not stated/);
  assert.equal(chips.find((c) => c.id === "status").tone, "good");

  // Nothing known about supports, no plates, no size yet, no check: no chips for them.
  const bare = summaryChips({ parts: [{ name: "a", role: "shell" }], plates: [], state: "none", sizeMm: null });
  assert.deepEqual(bare.map((c) => c.label), ["1 part"]);
  assert.equal(summaryChips({ parts: [{ name: "a", role: "shell", supports: false }] })[1].label, "No supports");
  assert.equal(summaryChips({ sizeMm: [NaN, 1, 1] }).length, 0);
});

test("the four check results become four chips", () => {
  const status = (state) => summaryChips({ state }).find((c) => c.id === "status");
  assert.deepEqual([status("passed").label, status("stale").label, status("unverified").label, status("failed").label],
    ["Pass", "Review", "Unverified", "Fail"]);
  assert.equal(status("failed").tone, "bad");
  assert.equal(status("unverified").tone, "warn");
  assert.equal(status(undefined), undefined);
});

test("a plate title keeps the description, not the status words, material or revision", () => {
  const name = "MỚI/SỬA — CẦN IN — Khung xương MỚI A1V4-r5 (khung dưới, khung trên) — CHỌN IN PLATE NÀY — PETG — A1V4-r5";
  assert.deepEqual(plateTitle(name), { title: "Khung xương MỚI A1V4-r5 (khung dưới, khung trên)", tag: "MỚI/SỬA", full: name });
  assert.equal(plateTitle("ĐÃ IN — GIỮ LẠI — Chân trước — PLA — r3").title, "Chân trước");
  assert.equal(plateTitle("ĐÃ IN — GIỮ LẠI — Chân trước — PLA — r3").tag, "ĐÃ IN");
  // A plain name, or one made only of status words, is shown whole.
  assert.deepEqual(plateTitle("Base and front links"), { title: "Base and front links", tag: "", full: "Base and front links" });
  assert.equal(plateTitle("MỚI/SỬA — CẦN IN").title, "MỚI/SỬA — CẦN IN");
  assert.equal(plateTitle("MỚI/SỬA — CẦN IN").tag, "");
  assert.equal(plateTitle("Legs — small parts").title, "small parts");
  assert.equal(plateTitle(undefined).title, "");
});

test("camera presets are unit directions; top is not parallel to the up axis", () => {
  for (const view of ["iso", "top", "front", "nonsense"]) assert.ok(Math.abs(Math.hypot(...viewDirection(view)) - 1) < 1e-9);
  assert.deepEqual(viewDirection("front"), [0, -1, 0]);
  const top = viewDirection("top");
  assert.ok(top[2] > 0.999 && top[1] < 0, "top looks down, leaning a hair toward -y");
  assert.deepEqual(viewDirection("nonsense"), viewDirection("iso"));
});

test("an accepted case wins; otherwise the case team's draft parts show as an unprintable draft", async () => {
  const { resolveCase, drawingMessage, DRAFT_PRINT_LABEL } = await import("../caseView.js");
  const preview = { run: "r001", teams: { case: { status: "draft", stage: "case", models: [
    { name: "shell", role: "shell", stlUrl: "/p/.circuit/team-previews/current/case/case/out/shell.stl?v=1", bytes: 10, printable: true },
    { name: "ref_DO_NOT_PRINT_servo", role: "other", stlUrl: "/p/servo.stl?v=1", bytes: 5, printable: false }] } } };
  const accepted = { state: "passed", parts: [{ name: "front", stlUrl: "/p/case/out/front.stl" }] };
  assert.equal(resolveCase(accepted, preview).enclosure, accepted);
  assert.equal(resolveCase(accepted, preview).draft, null);
  const wip = resolveCase({ state: "none", parts: [], contractUrl: "/c.json" }, preview);
  assert.equal(wip.enclosure.state, "draft");
  assert.equal(wip.enclosure.contractUrl, "/c.json");
  assert.equal(wip.draft.badge, "Đang làm · chưa duyệt (run r001)");
  assert.deepEqual(wip.enclosure.parts.map((p) => [p.name, p.printable]), [["shell", true], ["ref_DO_NOT_PRINT_servo", false]]);
  assert.equal(wip.enclosure.printStatus.state, "draft");
  assert.equal(describeStatus("draft").tone, "warn");
  assert.deepEqual(resolveCase(null, { run: "r002", teams: { case: { models: [] } } }),
    { enclosure: null, draft: null, drawing: true, run: "r002" });
  assert.equal(resolveCase(null, null, { running: true, activeRun: "r003" }).drawing, true);
  assert.equal(resolveCase(null, null, { running: false }).drawing, false);
  assert.match(drawingMessage("r002"), /still drawing \(run r002\)/);
  assert.equal(DRAFT_PRINT_LABEL, "Đang vẽ… chưa in được");
});

test("waterfall status groups parts as Khung, Khay dán, Chân, Khớp, Nắp with a chip each", async () => {
  const { stageGroups, partStatus, PART_STATUS } = await import("../caseView.js");
  const parts = [{ name: "frame" }, { name: "battery_tray" }, { name: "leg_FL" }, { name: "pin" }, { name: "coupon" }];
  const stages = { stages: [{ name: "simple", status: "valid" }, { name: "moving", status: "note" }, { name: "joints", status: "working" }],
    parts: { frame: { stage: "simple", group: "frame", status: "valid" }, battery_tray: { stage: "simple", group: "glue-on", status: "valid" },
      leg_FL: { stage: "moving", group: "moving", status: "note" }, pin: { stage: "joints", group: "joints", status: "working" } } };
  const grouped = stageGroups(parts, stages);
  assert.deepEqual(grouped.groups.map((g) => [g.label, g.status, g.parts.map((p) => p.name)]),
    [["Khung", "valid", ["frame"]], ["Khay dán", "valid", ["battery_tray"]], ["Chân", "note", ["leg_FL"]], ["Khớp", "working", ["pin"]]]);
  assert.deepEqual(grouped.other.map((p) => p.name), ["coupon"]);
  assert.equal(stageGroups(parts, null), null);
  assert.equal(partStatus(stages, "leg_FL"), "note");
  assert.equal(partStatus(stages, "coupon"), null);
  assert.deepEqual(Object.values(PART_STATUS).map((s) => s.label), ["Chờ", "Đang vẽ", "Đang kiểm", "Đạt", "Ghi chú", "Xong"]);
});
