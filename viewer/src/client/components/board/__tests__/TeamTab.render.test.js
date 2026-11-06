import assert from "node:assert/strict";
import test from "node:test";

import { click, mount } from "../../../test/render.js";
import TeamTab, { TeamStrip, formatSeconds, modelLabel, teamSteps } from "../TeamTab.jsx";

const team = (decision, seconds, issues, extra = {}) => ({ decision, seconds, issues, builds: decision ? 1 : 0, tests: decision ? 1 : 0, repairs: 0, ...extra });
const done = {
  run: "r002", status: "candidate_ready", startedAt: 1793900000, seconds: 7177, openIssues: 0, reason: "",
  teams: { board: team("approve", 532, 0), firmware: team("approve", 1575, 0), case: team("approve", 5420, 1, { builds: 2, tests: 2, repairs: 1, builderModel: "claude-opus-5-5", testerModel: "gpt-6.1-sol" }) },
  integration: { decision: "approve", issues: 0 },
  activity: [
    { stage: "integration-test", at: 1793907000, seconds: 110, decision: "approve", issues: 0, failed: false, model: "claude-opus-5-5" },
    { stage: "case-test", at: 1793906000, seconds: 406, decision: "revise", issues: 1, failed: false, model: "gpt-6.1-sol" },
    { stage: "case", at: 1793905000, seconds: 3600, decision: null, issues: 0, failed: true, model: "claude-opus-5-5" },
  ],
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
    const tab = ui.container.querySelector('[data-slot="team-tab"]');
    assert.match(tab.textContent, /Last run · r002/);
    assert.match(ui.container.querySelector('[data-slot="team-last-status"]').textContent, /Ready/);
    assert.match(ui.container.querySelector('[data-slot="team-run-case"]').textContent, /✓ Approved by tester.*1 issue, fixed/);
    assert.doesNotMatch(ui.container.textContent.replace(/unverified/gi, ""), /verified/i);
    const caseCard = ui.container.querySelector('[data-slot="team-run-case"]');
    assert.deepEqual([...caseCard.querySelectorAll("[data-step]")].map(step => step.dataset.state), ["done", "done", "done", "done"]);
    assert.deepEqual([...caseCard.querySelectorAll('[data-slot="team-model"]')].map(chip => chip.textContent), ["build · Opus", "test · Sol"]);
    const activity = [...ui.container.querySelectorAll('[data-slot="team-activity-item"]')].map(item => item.textContent);
    assert.match(activity[0], /Integration test · approved/);
    assert.match(activity[1], /Case test · 1 issue/);
    assert.match(activity[2], /Case build · stopped/);
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
  const ui = mount(TeamTab, { teamPreview: preview, teamRuns: { activeRun: "r003", running: true, runs: [{ ...done, run: "r003", status: "running", teams: { ...done.teams, case: team(null, 0, 0) } }, done, failed] } });
  try {
    assert.equal(ui.container.querySelector('[data-slot="team-tab"]').dataset.running, "true");
    assert.match(ui.container.textContent, /Run · r003/);
    const caseCard = ui.container.querySelector('[data-slot="team-run-case"]');
    assert.equal(caseCard.querySelector('[data-step="Build"]').dataset.state, "current");
    assert.match(ui.container.querySelector('[data-slot="team-run-board"]').textContent, /✓ Approved by tester/);
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

test("a print-plan-only run never hides the run that built the parts", () => {
  const printOnly = { ...failed, run: "r003", status: "print_only", reason: "", openIssues: 0 };
  const ui = mount(TeamTab, { teamRuns: { activeRun: "r003", running: false, runs: [printOnly, done] } });
  try {
    assert.match(ui.container.textContent, /Last run · r002/);
    const rows = [...ui.container.querySelectorAll('[data-slot="team-history-row"]')];
    assert.deepEqual(rows.map(row => row.dataset.run), ["r003"]);
    assert.match(rows[0].textContent, /Print plan/);
  } finally {
    ui.unmount();
  }
});

test("steps follow the live status: test, then fix after a revise, then approve", () => {
  assert.equal(teamSteps({ builds: 1, tests: 0 }, "checking").current, 1);
  assert.equal(teamSteps({ builds: 1, tests: 1, decision: "revise" }, "draft").current, 2);
  assert.deepEqual(teamSteps({ builds: 1, tests: 1, decision: "approve" }, "").done, [true, true, true, true]);
  assert.equal(teamSteps({}, "queued").current, -1);
  assert.equal(modelLabel("claude-sonnet-5-5"), "Sonnet");
  assert.equal(modelLabel("gpt-6.1-sol"), "Sol");
  assert.equal(modelLabel("mystery-1"), "mystery-1");
});
