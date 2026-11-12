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
