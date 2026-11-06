import assert from "node:assert/strict";
import test from "node:test";

import { click, mount } from "../../../test/render.js";
import TeamTab, { TeamStrip, formatSeconds } from "../TeamTab.jsx";

const team = (decision, seconds, issues) => ({ decision, seconds, issues });
const done = {
  run: "r002", status: "candidate_ready", startedAt: 1793900000, seconds: 7177, openIssues: 0, reason: "",
  teams: { board: team("approve", 532, 0), firmware: team("approve", 1575, 0), case: team("approve", 5420, 1) },
  integration: { decision: "approve", issues: 0 },
};
const failed = {
  run: "r001", status: "planning_failed", startedAt: 1793890000, seconds: null, openIssues: 1, reason: "Claude exited 71",
  teams: { board: team(null, 0, 0), firmware: team(null, 0, 0), case: team(null, 0, 0) }, integration: null,
};
const preview = {
  run: "r003", runStatus: "running", runStage: "case", verified: false,
  teams: {
    board: { status: "passed", stage: "board", verified: false, files: [], media: [], findings: [] },
    firmware: { status: "passed", stage: "firmware", verified: false, files: [], media: [], findings: [] },
    case: { status: "draft", stage: "case", verified: false, files: [], media: [], findings: [] },
  },
};

test("a finished run stays: tester approvals, times, issues and the history", () => {
  const ui = mount(TeamTab, { teamRuns: { activeRun: "r002", running: false, runs: [done, failed] } });
  try {
    const last = ui.container.querySelector('[data-slot="team-last-run"]');
    assert.match(last.textContent, /Last run · r002/);
    assert.match(last.textContent, /Ready/);
    assert.match(ui.container.querySelector('[data-slot="team-run-case"]').textContent, /✓ Approved by tester.*1 issue, fixed/);
    assert.doesNotMatch(ui.container.textContent, /verified/i);
    assert.match(ui.container.querySelector('[data-slot="team-integration"]').textContent, /Integration · 0 issues/);
    const rows = [...ui.container.querySelectorAll('[data-slot="team-history-row"]')];
    assert.deepEqual(rows.map(row => row.dataset.run), ["r001"]);
    assert.match(rows[0].textContent, /Plan failed/);
    assert.equal(rows[0].querySelector("td:nth-child(2)").title, "Claude exited 71");
    assert.deepEqual(ui.errors, []);
  } finally {
    ui.unmount();
  }
});

test("a failed last run says where it stopped", () => {
  const ui = mount(TeamTab, { teamRuns: { activeRun: "r001", running: false, runs: [failed] } });
  try {
    assert.match(ui.container.querySelector('[data-slot="team-last-status"]').textContent, /Plan failed/);
    assert.match(ui.container.textContent, /Stopped on: Claude exited 71/);
    assert.equal(ui.container.querySelector('[data-slot="team-history"]'), null);
  } finally {
    ui.unmount();
  }
});

test("while running the live drafts show above the earlier runs", () => {
  const ui = mount(TeamTab, { teamPreview: preview, teamRuns: { activeRun: "r003", running: true, runs: [{ ...done, run: "r003", status: "running" }, done, failed] } });
  try {
    assert.ok(ui.container.querySelector('[data-slot="team-progress"]'));
    assert.equal(ui.container.querySelector('[data-slot="team-last-run"]'), null);
    assert.deepEqual([...ui.container.querySelectorAll('[data-slot="team-history-row"]')].map(row => row.dataset.run), ["r002", "r001"]);
  } finally {
    ui.unmount();
  }
});

test("no run yet explains when the view fills", () => {
  const ui = mount(TeamTab, {});
  try {
    assert.match(ui.container.querySelector('[data-slot="team-empty"]').textContent, /No team run yet/);
  } finally {
    ui.unmount();
  }
});

test("the Brief strip is one line that opens the Team view", () => {
  let opened = 0;
  const ui = mount(TeamStrip, { teamPreview: preview, teamRuns: { activeRun: "r003", running: true, runs: [] }, onOpen: () => { opened += 1; } });
  try {
    const strip = ui.container.querySelector('[data-slot="team-strip"]');
    assert.equal(strip.dataset.running, "true");
    assert.match(strip.textContent, /Team · r003 running/);
    assert.match(strip.textContent, /✓ Board.*✓ Firmware.*○ Case/);
    click(strip);
    assert.equal(opened, 1);
  } finally {
    ui.unmount();
  }
  const quiet = mount(TeamStrip, { teamRuns: null, teamPreview: null });
  try {
    assert.equal(quiet.container.querySelector('[data-slot="team-strip"]'), null);
  } finally {
    quiet.unmount();
  }
});

test("durations read in minutes, then hours", () => {
  assert.equal(formatSeconds(306), "5 min");
  assert.equal(formatSeconds(7177), "2.0 h");
  assert.equal(formatSeconds(null), "—");
});
