// One-tap create: send a ready brief straight into the chat turn, creating a
// project first if none is open. Mirrors ChatInput.handleSend's project-ensure
// logic so a starter tap behaves exactly like typing + Enter — the difference
// is the creator never had to type. See docs/onboarding-ux.md.

import { setProject as setChatProject, startTurn, getChatState } from "@/store/chat";
import { useProjectsStore } from "@/store/projects.ts";
import { PLACEHOLDER_PROJECT_NAME } from "../chat/chatInputHelpers";

/**
 * Start a create turn from a ready-made brief. Returns the startTurn response
 * (or null if it couldn't start). Safe to call with no project open.
 */
export async function startFromBrief(prompt) {
  const payload = String(prompt || "").trim();
  if (!payload) return null;
  // Don't fire a second turn on top of an in-flight one.
  if (getChatState().turnInProgress) return null;
  if (!getChatState().currentProjectId) {
    try {
      const summary = await useProjectsStore.getState().create(PLACEHOLDER_PROJECT_NAME);
      setChatProject(summary.id);
    } catch {
      // create() surfaces its own error via the projects store; bail cleanly.
      return null;
    }
  }
  return startTurn(payload);
}
