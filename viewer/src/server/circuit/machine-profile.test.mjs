import test from "node:test";
import assert from "node:assert/strict";

import {
  MAX_PROFILE_DESCRIPTION_CHARS,
  MachineProfilePlanError,
  SUPPORTED_DESIGN_PROFILE,
  SUPPORTED_PROFILE_CAPABILITIES,
  parseApprovedMachineProfile,
} from "./machine-profile.mjs";

function machineBlock(overrides = {}) {
  return [
    "# Engineering plan",
    "",
    "~~~circuit-profile",
    JSON.stringify({
      schemaVersion: 1,
      designProfile: SUPPORTED_DESIGN_PROFILE,
      capabilities: [...SUPPORTED_PROFILE_CAPABILITIES],
      description: "protected USB data board with a 3.3V status indicator",
      ...overrides,
    }),
    "~~~",
    "",
    "## Power budget",
    "Machine-derived values follow.",
  ].join("\n");
}

function assertRefused(plan, pattern) {
  assert.throws(
    () => parseApprovedMachineProfile(plan),
    (error) => {
      assert.ok(error instanceof MachineProfilePlanError);
      assert.equal(error.code, "UNSUPPORTED_DESIGN_PLAN");
      assert.equal(error.statusCode, 422);
      assert.match(error.message, pattern);
      return true;
    },
  );
}

test("approved machine profile is explicit, exact, immutable, and CRLF-safe", () => {
  const parsed = parseApprovedMachineProfile(machineBlock().replaceAll("\n", "\r\n"));
  assert.deepEqual(parsed, {
    schemaVersion: 1,
    designProfile: "protected-usb-indicator-v1",
    capabilities: ["power-usb", "indicator", "usb-data"],
    description: "protected USB data board with a 3.3V status indicator",
  });
  assert.equal(Object.isFrozen(parsed), true);
  assert.equal(Object.isFrozen(parsed.capabilities), true);
});

test("profile is never inferred from prose or a different fence transport", () => {
  assertRefused(
    "Build protected USB, USB data, and an indicator with protected-usb-indicator-v1.",
    /exactly one circuit-profile/,
  );
  assertRefused(
    machineBlock().replaceAll("~~~", "```"),
    /exactly one circuit-profile/,
  );
  assertRefused(
    `<!-- ~~~circuit-profile -->\n${JSON.stringify({ designProfile: SUPPORTED_DESIGN_PROFILE })}\n~~~`,
    /exactly one circuit-profile/,
  );
});

test("missing, duplicate, nested, and unclosed profile blocks fail closed", () => {
  assertRefused("# ordinary plan", /exactly one circuit-profile/);
  assertRefused(`${machineBlock()}\n${machineBlock()}`, /more than one/);
  assertRefused(
    `~~~circuit-profile\n{}\n~~~circuit-profile\n{}\n~~~`,
    /cannot be nested/,
  );
  assertRefused(`~~~circuit-profile\n{}`, /not closed/);
});

test("profile payload is strict JSON with no unknown or missing members", () => {
  assertRefused(
    `~~~circuit-profile\n{/* comment */}\n~~~`,
    /not strict JSON/,
  );
  assertRefused(machineBlock({ extra: true }), /must contain exactly/);
  const missingDescription = JSON.parse(
    machineBlock().match(/~~~circuit-profile\n([^\n]+)\n~~~/u)[1],
  );
  delete missingDescription.description;
  assertRefused(
    `~~~circuit-profile\n${JSON.stringify(missingDescription)}\n~~~`,
    /must contain exactly/,
  );
});

test("only the literal supported schema, profile, and capability closure pass", () => {
  assertRefused(machineBlock({ schemaVersion: 2 }), /schemaVersion/);
  assertRefused(machineBlock({ schemaVersion: "1" }), /schemaVersion/);
  assertRefused(machineBlock({ designProfile: "legacy-freeform-v1" }), /designProfile/);
  assertRefused(machineBlock({ capabilities: "power-usb" }), /capabilities/);
  assertRefused(
    machineBlock({ capabilities: ["power-usb", "usb-data", "indicator"] }),
    /capabilities/,
  );
  assertRefused(
    machineBlock({ capabilities: ["power-usb", "indicator"] }),
    /capabilities/,
  );
  assertRefused(
    machineBlock({
      capabilities: ["power-usb", "indicator", "usb-data", "radio"],
    }),
    /capabilities/,
  );
});

test("profile description is bounded, single-line, and already canonical", () => {
  assertRefused(machineBlock({ description: "" }), /description/);
  assertRefused(machineBlock({ description: " padded " }), /description/);
  assertRefused(machineBlock({ description: "two\nlines" }), /description/);
  assertRefused(
    machineBlock({ description: "x".repeat(MAX_PROFILE_DESCRIPTION_CHARS + 1) }),
    /description/,
  );
  assert.equal(
    parseApprovedMachineProfile(
      machineBlock({ description: "x".repeat(MAX_PROFILE_DESCRIPTION_CHARS) }),
    ).description.length,
    MAX_PROFILE_DESCRIPTION_CHARS,
  );
});

