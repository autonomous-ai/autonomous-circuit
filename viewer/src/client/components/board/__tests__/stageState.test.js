import assert from "node:assert/strict";
import test from "node:test";
import { isStagePending } from "../stageState.js";

// The defect: a fresh install has no projects, so the catalog is never read,
// so `hydrated` never flips — and the stage spun on a black rectangle forever
// instead of showing the three-step explanation of what the tool does. Nobody
// with a project on disk could reproduce it.
test("a fresh install with no projects is not pending — it shows the pitch", () => {
  assert.equal(
    isStagePending({
      projectsStatus: "ready",
      projectCount: 0,
      currentProjectId: "",
      catalogHydrated: false,
      catalogError: "",
    }),
    false,
  );
});

test("still listing projects is pending — we do not know which screen is right yet", () => {
  assert.equal(isStagePending({ projectsStatus: "loading", projectCount: 0 }), true);
  assert.equal(isStagePending({}), true);
});

test("a project exists but none is open yet: pending, so the pitch does not flash", () => {
  assert.equal(
    isStagePending({ projectsStatus: "ready", projectCount: 2, currentProjectId: "" }),
    true,
  );
});

test("a project is open and its catalog has not landed: pending", () => {
  assert.equal(
    isStagePending({
      projectsStatus: "ready",
      projectCount: 1,
      currentProjectId: "abc",
      catalogHydrated: false,
    }),
    true,
  );
});

test("hydrated, or failed, both stop the spinner", () => {
  const base = { projectsStatus: "ready", projectCount: 1, currentProjectId: "abc" };
  assert.equal(isStagePending({ ...base, catalogHydrated: true }), false);
  // An error is a landed answer: the workspace renders it, which beats a
  // spinner that will never stop.
  assert.equal(isStagePending({ ...base, catalogError: "boom" }), false);
});
