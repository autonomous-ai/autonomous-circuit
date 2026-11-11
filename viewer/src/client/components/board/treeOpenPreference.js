// Whether the PROJECTS sidebar starts open. It takes a fifth of the screen and
// the board, case and team views need the room more, so a first visit starts
// with it collapsed to its thin rail; after that, the viewer's own choice sticks.

export const TREE_OPEN_STORAGE_KEY = "circuit.boardTree.open";

/** The remembered choice; collapsed when there is none or storage is blocked. */
export function readStoredTreeOpen(storage = globalThis.localStorage) {
  try {
    return storage?.getItem?.(TREE_OPEN_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

/** Remember the choice. Never throws — blocked storage must not eat the click. */
export function writeStoredTreeOpen(open, storage = globalThis.localStorage) {
  try {
    storage?.setItem?.(TREE_OPEN_STORAGE_KEY, open ? "true" : "false");
  } catch {
    /* private mode, disabled storage — the in-memory choice still applies */
  }
  return Boolean(open);
}
