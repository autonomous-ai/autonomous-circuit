import assert from "node:assert/strict";
import test from "node:test";

import { flush, mount } from "../../../test/render.js";
import CaseOverviewTab, { themedOverviewUrl } from "../CaseOverviewTab.jsx";

test("the overview URL carries the app theme in its hash", () => {
  assert.equal(themedOverviewUrl("/p/case/overview.html?v=1", true), "/p/case/overview.html?v=1#theme=dark");
  assert.equal(themedOverviewUrl("/p/case/overview.html#old", false), "/p/case/overview.html#theme=light");
  assert.equal(themedOverviewUrl("", true), "");
});

test("the iframe switches its theme hash when the app goes dark", async () => {
  document.documentElement.classList.remove("dark");
  const ui = mount(CaseOverviewTab, { artifact: { case: { overviewUrl: "/p/case/overview.html?v=1" } } });
  try {
    const frame = () => ui.container.querySelector("iframe");
    assert.match(frame().getAttribute("src"), /#theme=light$/);
    document.documentElement.classList.add("dark");
    for (let i = 0; i < 3; i++) await flush();
    assert.match(frame().getAttribute("src"), /#theme=dark$/);
  } finally {
    ui.unmount();
    document.documentElement.classList.remove("dark");
  }
});

test("engineering drawings open from a row of links above the overview", () => {
  const ui = mount(CaseOverviewTab, { artifact: { case: { overviewUrl: "/p/case/overview.html", drawings: [
    { name: "assembly", title: "Crab — assembly", kind: "assembly", url: "/p/case/out/drawings/assembly.svg?v=1" },
    { name: "frame_lower", title: "frame_lower", kind: "part", url: "/p/case/out/drawings/frame_lower.svg?v=1" }] } } });
  try {
    const links = [...ui.container.querySelectorAll('[data-slot="case-drawings"] a')];
    assert.deepEqual(links.map(a => [a.textContent, a.getAttribute("href")]),
      [["Assembly", "/p/case/out/drawings/assembly.svg?v=1"], ["frame_lower", "/p/case/out/drawings/frame_lower.svg?v=1"]]);
  } finally { ui.unmount(); }
  const empty = mount(CaseOverviewTab, { artifact: { case: { overviewUrl: "/p/case/overview.html" } } });
  try { assert.equal(empty.container.querySelector('[data-slot="case-drawings"]'), null); } finally { empty.unmount(); }
});

const overviewScene = [];
function StubScene(props) { overviewScene.push(props); return null; }
const draftPreview = { run: "r007", teams: { case: { status: "draft", models: [
  { name: "shell", role: "shell", stlUrl: "/p/.circuit/team-previews/current/case/case/out/shell.stl?v=1", bytes: 9, printable: true }] } } };

test("with no accepted case the overview shows the team's draft model under a badge", () => {
  overviewScene.length = 0;
  const ui = mount(CaseOverviewTab, { artifact: {}, teamPreview: draftPreview, Scene: StubScene });
  try {
    assert.equal(ui.container.querySelector("iframe"), null);
    assert.ok(ui.container.querySelector('[data-slot="case-overview-draft"]'));
    assert.equal(ui.container.querySelector('[data-slot="case-draft-badge"]').textContent, "Đang làm · chưa duyệt (run r007)");
    assert.deepEqual(overviewScene.at(-1).parts.map(p => p.name), ["shell"]);
    assert.doesNotMatch(ui.container.textContent, /No case overview yet/);
  } finally { ui.unmount(); }
});

test("an accepted overview wins over a draft; an active run with nothing yet says the team is drawing", () => {
  const accepted = mount(CaseOverviewTab, { artifact: { case: { overviewUrl: "/p/case/overview.html", parts: [{ name: "a", stlUrl: "/a.stl" }] } },
    teamPreview: draftPreview, Scene: StubScene });
  try {
    assert.ok(accepted.container.querySelector("iframe"));
    assert.equal(accepted.container.querySelector('[data-slot="case-draft-badge"]'), null);
  } finally { accepted.unmount(); }
  const drawing = mount(CaseOverviewTab, { artifact: {}, teamRuns: { running: true, activeRun: "r008", runs: [] }, Scene: StubScene });
  try {
    const empty = drawing.container.querySelector('[data-slot="case-overview-empty"]');
    assert.match(empty.textContent, /still drawing \(run r008\)/);
    assert.doesNotMatch(empty.textContent, /No case overview yet/);
  } finally { drawing.unmount(); }
  const idle = mount(CaseOverviewTab, { artifact: {}, Scene: StubScene });
  try { assert.match(idle.container.textContent, /No case overview yet/); } finally { idle.unmount(); }
});
