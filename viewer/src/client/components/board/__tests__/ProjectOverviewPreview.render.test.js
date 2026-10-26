import assert from "node:assert/strict";
import test from "node:test";
import React from "react";

import { flush } from "../../../test/render.js";
import { useProjectsStore } from "../../../store/projects.ts";
import { safeProjectOverviewUrl } from "../ProjectOverviewPreview.jsx";
import { openWorkspace } from "./boardWorkspace.test-helper.js";

test("only a same-project viewer HTML URL can become the overview preview", () => {
  assert.equal(safeProjectOverviewUrl("/projects/p1/case/overview.html?v=1", "p1"), "/projects/p1/case/overview.html?v=1");
  assert.equal(safeProjectOverviewUrl("https://elsewhere.test/overview.html", "p1"), "");
  assert.equal(safeProjectOverviewUrl("//elsewhere.test/overview.html", "p1"), "");
  assert.equal(safeProjectOverviewUrl("/projects/other/case/overview.html", "p1"), "");
});

test("native project shows unverified overview until a board entry arrives", async () => {
  const previousProjects = useProjectsStore.getState().projects;
  const w = await openWorkspace();
  try {
    const boardEntry = {
      file: w.boardFile,
      kind: "tsx",
      sourceKind: "tsx",
      url: `/projects/p1/${w.boardFile}`,
      artifact: {},
    };
    React.act(() => useProjectsStore.setState({ projects: [{ id: "p1", name: "Crab", engine: "kicad-native" }] }));
    const url = "/projects/p1/case/overview.html?v=100-200";
    w.ui.set({ boardEntries: [], projectOverviewUrl: url });
    await flush();

    const preview = w.find('[data-slot="project-overview-preview"]');
    assert.ok(preview, "an unverified overview should be visible before the board is accepted");
    assert.match(preview.textContent, /Reference layout — unverified; no checked KiCad board yet/);
    assert.equal(preview.querySelector("iframe")?.getAttribute("src"), url);
    assert.equal(preview.querySelector("iframe")?.getAttribute("sandbox"), "allow-scripts");
    assert.equal(preview.querySelector("iframe")?.getAttribute("referrerpolicy"), "no-referrer");
    assert.equal(preview.querySelector("a")?.getAttribute("href"), url);
    assert.equal(w.find('[data-slot="board-empty-state"]'), null);

    w.ui.set({ boardEntries: [boardEntry] });
    await flush();
    await flush();
    assert.equal(w.find('[data-slot="project-overview-preview"]'), null, "board view should take over when a board arrives");
    assert.ok(w.find('[data-slot="board-group"]'));
    assert.deepEqual(w.ui.errors, []);
  } finally {
    w.close();
    React.act(() => useProjectsStore.setState({ projects: previousProjects }));
  }
});
