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
