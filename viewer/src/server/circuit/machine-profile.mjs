// Machine-readable contract carried inside an approved circuit plan.
//
// This module is deliberately pure and is not wired into chat turns yet. The
// public project generator is still being cold-qualified, so calling it from
// the viewer would turn a provisional source profile into a production path.
// Keeping the parser isolated lets the plan transport fail closed now without
// pretending that generator adoption is complete.

export const MACHINE_PROFILE_SCHEMA_VERSION = 1;
export const SUPPORTED_DESIGN_PROFILE = "protected-usb-indicator-v1";
export const SUPPORTED_PROFILE_CAPABILITIES = Object.freeze([
  "power-usb",
  "indicator",
  "usb-data",
]);
export const MAX_PROFILE_DESCRIPTION_CHARS = 500;

const PROFILE_FENCE_OPEN = /^~~~circuit-profile[ \t]*$/u;
const PROFILE_FENCE_CLOSE = /^~~~[ \t]*$/u;
const EXPECTED_KEYS = Object.freeze([
  "capabilities",
  "description",
  "designProfile",
  "schemaVersion",
]);

export class MachineProfilePlanError extends Error {
  constructor(message) {
    super(message);
    this.name = "MachineProfilePlanError";
    this.code = "UNSUPPORTED_DESIGN_PLAN";
    this.statusCode = 422;
  }
}

function refuse(message) {
  throw new MachineProfilePlanError(message);
}

function profileFenceBodies(planText) {
  if (typeof planText !== "string" || !planText.trim()) {
    refuse("approved plan is empty");
  }

  const lines = planText.split(/\r?\n/u);
  const bodies = [];
  for (let index = 0; index < lines.length; index += 1) {
    if (!PROFILE_FENCE_OPEN.test(lines[index])) continue;

    const body = [];
    let closed = false;
    for (index += 1; index < lines.length; index += 1) {
      if (PROFILE_FENCE_OPEN.test(lines[index])) {
        refuse("circuit-profile blocks cannot be nested");
      }
      if (PROFILE_FENCE_CLOSE.test(lines[index])) {
        closed = true;
        break;
      }
      body.push(lines[index]);
    }
    if (!closed) {
      refuse("circuit-profile block is not closed");
    }
    bodies.push(body.join("\n"));
  }

  if (bodies.length !== 1) {
    refuse(
      bodies.length === 0
        ? "approved plan must contain exactly one circuit-profile block"
        : "approved plan contains more than one circuit-profile block",
    );
  }
  return bodies;
}

function exactCapabilities(value) {
  return (
    Array.isArray(value) &&
    value.length === SUPPORTED_PROFILE_CAPABILITIES.length &&
    value.every(
      (capability, index) => capability === SUPPORTED_PROFILE_CAPABILITIES[index],
    )
  );
}

/**
 * Parse the sole supported new-product profile from approved plan markdown.
 *
 * The outer `circuit-plan` fence is removed by the chat transport before this
 * function runs. A tilde fence is used here so the machine block can live
 * inside that outer backtick fence without accidentally terminating it.
 * Nothing is inferred from prose: absent, dynamic, partial, reordered, or
 * unknown capability sets are unsupported and must never reach IMPLEMENT.
 */
export function parseApprovedMachineProfile(planText) {
  const [body] = profileFenceBodies(planText);
  let raw;
  try {
    raw = JSON.parse(body);
  } catch (error) {
    refuse(`circuit-profile is not strict JSON: ${error?.message || error}`);
  }

  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    refuse("circuit-profile must be a JSON object");
  }
  const keys = Object.keys(raw).sort();
  if (
    keys.length !== EXPECTED_KEYS.length ||
    keys.some((key, index) => key !== EXPECTED_KEYS[index])
  ) {
    refuse(`circuit-profile must contain exactly: ${EXPECTED_KEYS.join(", ")}`);
  }
  if (raw.schemaVersion !== MACHINE_PROFILE_SCHEMA_VERSION) {
    refuse(`unsupported circuit-profile schemaVersion: ${raw.schemaVersion}`);
  }
  if (raw.designProfile !== SUPPORTED_DESIGN_PROFILE) {
    refuse(`unsupported designProfile: ${String(raw.designProfile)}`);
  }
  if (!exactCapabilities(raw.capabilities)) {
    refuse(
      "unsupported capabilities: expected exact ordered closure " +
        SUPPORTED_PROFILE_CAPABILITIES.join(", "),
    );
  }
  if (
    typeof raw.description !== "string" ||
    !raw.description ||
    raw.description !== raw.description.trim() ||
    /[\u0000\r\n]/u.test(raw.description) ||
    raw.description.length > MAX_PROFILE_DESCRIPTION_CHARS
  ) {
    refuse(
      `description must be a trimmed single line of 1..${MAX_PROFILE_DESCRIPTION_CHARS} characters`,
    );
  }

  return Object.freeze({
    schemaVersion: MACHINE_PROFILE_SCHEMA_VERSION,
    designProfile: SUPPORTED_DESIGN_PROFILE,
    capabilities: SUPPORTED_PROFILE_CAPABILITIES,
    description: raw.description,
  });
}

