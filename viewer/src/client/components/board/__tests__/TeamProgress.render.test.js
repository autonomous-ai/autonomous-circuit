import assert from "node:assert/strict";
import test from "node:test";

import { mount } from "../../../test/render.js";
import TeamProgress from "../TeamProgress.jsx";

const preview = {
  run: "r001", runStatus: "running", runStage: "building", verified: false,
  teams: {
    board: { status: "draft", stage: "board", verified: false, files: ["design/main.kicad_pcb"],
      media: [{ file: "boards/main_review/_pcb.svg", url: "/projects/p/.circuit/team-previews/current/board/_pcb.svg" }], findings: [] },
    firmware: { status: "checking", stage: "firmware", verified: false, files: ["firmware/main.c"], media: [], findings: [] },
    case: { status: "queued", stage: "", verified: false, files: [], media: [], findings: [] },
  },
};

test("team progress shows three unverified teams and draft image separately", () => {
  const ui = mount(TeamProgress, { preview });
  try {
    assert.match(ui.container.textContent, /unverified drafts/);
    assert.match(ui.container.textContent, /Board/);
    assert.match(ui.container.textContent, /Firmware/);
    assert.match(ui.container.textContent, /Case/);
    assert.match(ui.container.textContent, /firmware\/main.c/);
    assert.equal(ui.container.querySelectorAll("img").length, 1);
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("accepted or malformed preview is not rendered", () => {
  const ui = mount(TeamProgress, { preview: { ...preview, verified: true } });
  try {
    assert.equal(ui.container.querySelector('[data-slot="team-progress"]'), null);
  } finally {
    ui.unmount();
  }
});
