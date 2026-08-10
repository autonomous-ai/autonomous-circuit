// Claude Code readiness gate for the chat.
//
// Every inference the app runs is a spawned `claude` CLI subprocess, so before
// any turn reaches the backend the CLI must exist on this machine. This store
// enforces that: `ensureClaudeReady` probes once per app run, and when the CLI
// is missing it opens the setup dialog with MANUAL install instructions and
// parks the send — resolving it `true` (send proceeds) once a re-check finds
// the CLI, or `false` (send dropped) if the user dismisses.
//
// v1 is Create-only: there is no in-app installer (the server intentionally
// 404s `app_install_claude_code`). The dialog links claude.ai/install and
// tells the user to run `claude` once in a terminal to sign in.
//
// Same "Zustand-style" external store shape as ./chat.js: a pure reducer,
// module-level state, and a `useSyncExternalStore` hook.

import { useSyncExternalStore } from "react";
import { getTransport } from "../lib/transport.ts";

/**
 * @typedef {Object} ClaudeSetupState
 * @property {boolean} open the setup dialog is visible
 * @property {"instructions"|"checking"|"error"} phase
 * @property {string} errorMessage re-check feedback (phase "error")
 * @property {boolean} cliReady positive detection cached for this app run
 * @property {boolean} hasPendingSend a chat send is parked awaiting the install
 */

/** @type {ClaudeSetupState} */
export const INITIAL_CLAUDE_SETUP_STATE = Object.freeze({
  open: false,
  phase: "instructions",
  errorMessage: "",
  cliReady: false,
  hasPendingSend: false,
});

/** True for the chat driver's CLAUDE_NOT_INSTALLED error messages — both the
 * user-facing "`claude` CLI not found. Install Claude Code (…)" chat event and
 * the raw DriverError display ("claude CLI not found on PATH"). */
export function isClaudeMissingError(message) {
  const text = String(message || "").toLowerCase();
  return text.includes("claude") && text.includes("cli not found");
}

export function claudeSetupReducer(state, action) {
  switch (action.type) {
    case "open":
      return {
        ...state,
        open: true,
        phase: "instructions",
        errorMessage: "",
        hasPendingSend: action.hasPendingSend ?? state.hasPendingSend,
      };
    case "checking":
      return { ...state, phase: "checking", errorMessage: "" };
    case "recheck_failed":
      return {
        ...state,
        phase: "error",
        errorMessage: String(action.message || "Claude Code was not detected"),
      };
    case "ready":
      return {
        ...state,
        open: false,
        phase: "instructions",
        cliReady: true,
        errorMessage: "",
        hasPendingSend: false,
      };
    case "cli_ready":
      return state.cliReady ? state : { ...state, cliReady: true };
    case "pending_send":
      return state.hasPendingSend === action.value
        ? state
        : { ...state, hasPendingSend: action.value };
    case "dismiss":
      return { ...state, open: false, hasPendingSend: false };
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// External store
// ---------------------------------------------------------------------------

/** @type {ClaudeSetupState} */
let currentState = INITIAL_CLAUDE_SETUP_STATE;
const listeners = new Set();
// Resolver of the most recent parked send. Only one send may resume (firing two
// would trip "A turn is already in progress"), so a newer send supersedes an
// older one by resolving it `false`.
let pendingSendResolve = null;

function setState(next) {
  if (next === currentState) return;
  currentState = next;
  for (const listener of listeners) listener();
}

function dispatch(action) {
  setState(claudeSetupReducer(currentState, action));
}

export function getClaudeSetupState() {
  return currentState;
}

export function subscribeClaudeSetup(listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useClaudeSetupStore(selector = (state) => state) {
  return useSyncExternalStore(
    subscribeClaudeSetup,
    () => selector(currentState),
    () => selector(currentState),
  );
}

export function resetClaudeSetupStore() {
  if (pendingSendResolve) {
    pendingSendResolve(false);
    pendingSendResolve = null;
  }
  setState(INITIAL_CLAUDE_SETUP_STATE);
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

function settlePendingSend(ok) {
  const resolve = pendingSendResolve;
  pendingSendResolve = null;
  resolve?.(ok);
}

/**
 * Open the setup dialog with the manual install instructions. Safe to call
 * repeatedly (e.g. from every CLAUDE_NOT_INSTALLED chat error).
 */
export function openClaudeSetup() {
  if (currentState.cliReady) return;
  if (!currentState.open || currentState.phase !== "checking") {
    dispatch({ type: "open" });
  }
}

/**
 * Close the dialog. Any parked send is dropped (resolved `false`) — the user's
 * text stays in the composer, so nothing is lost. A later send just re-probes.
 */
export function dismissClaudeSetup() {
  settlePendingSend(false);
  dispatch({ type: "dismiss" });
}

/**
 * Re-probe after the user installed Claude Code themselves. Passes → the
 * parked send resumes and the dialog closes. Fails → keep the dialog open
 * with guidance.
 */
export async function recheckClaude(transport = getTransport()) {
  dispatch({ type: "checking" });
  let found = false;
  try {
    const check = await transport.app_prereq_check();
    found = Boolean(check?.claudeCli?.found);
  } catch {
    found = false;
  }
  if (found) {
    dispatch({ type: "ready" });
    settlePendingSend(true);
    return true;
  }
  dispatch({
    type: "recheck_failed",
    message:
      "Claude Code still wasn’t detected. If you installed it manually, make sure the install finished, then re-check.",
  });
  return false;
}

/**
 * The pre-inference gate. Resolves `true` when a turn may start:
 *  - the CLI was already detected this app run (cached), or
 *  - the probe finds it now, or
 *  - the probe itself is unavailable/broken (fail open — the driver still
 *    guards and its error event reopens this dialog), or
 *  - it was missing, the user installed it manually, and Re-check passed.
 * Resolves `false` when the user dismissed the dialog (send dropped) or a
 * newer send superseded this one.
 */
export async function ensureClaudeReady(transport = getTransport()) {
  if (currentState.cliReady) return true;
  let found = null;
  if (typeof transport?.app_prereq_check === "function") {
    try {
      const check = await transport.app_prereq_check();
      found = Boolean(check?.claudeCli?.found);
    } catch {
      found = null;
    }
  }
  if (found === true) {
    dispatch({ type: "cli_ready" });
    return true;
  }
  if (found === null) return true;

  // CLI is definitively missing: park this send behind the setup dialog.
  const gate = new Promise((resolve) => {
    settlePendingSend(false); // supersede an older parked send
    pendingSendResolve = resolve;
  });
  dispatch({ type: "pending_send", value: true });
  dispatch({ type: "open" });
  return gate;
}
