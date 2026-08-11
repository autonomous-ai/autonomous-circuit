// Is anything actually on its way to the board stage?
//
// The bug this answers: on a genuinely fresh install — no projects at all —
// the stage rendered a spinner on a black rectangle *forever*. The catalog is
// scoped to the open project, so with no project there is nothing to read;
// `catalogHydrated` stayed false, and the branch that gates the "here is what
// this tool does" pitch on hydration never fired. The very first thing a new
// user saw was an app that looked like it had hung, and nobody who already had
// a project on disk could ever reproduce it.
//
// A spinner is a promise that something is coming. It is only honest when
// something is: we are still finding out what projects exist, a project exists
// and is about to be opened, or a project is open and its catalog has not
// arrived. Anything else — including "there is nothing here yet" — is a state
// the stage should explain in words instead.

/**
 * @param {{projectsStatus?: string, projectCount?: number, currentProjectId?: string|null,
 *          catalogHydrated?: boolean, catalogError?: string}} state
 * @returns {boolean} true while a spinner is the truthful thing to show
 */
export function isStagePending({
  projectsStatus = "",
  projectCount = 0,
  currentProjectId = "",
  catalogHydrated = false,
  catalogError = "",
} = {}) {
  // Still listing what is on disk — we do not yet know if this is a fresh
  // install or a returning user, and guessing wrong flashes the wrong screen.
  if (projectsStatus !== "ready") return true;
  // A project exists but none is open: the auto-open effect is about to run.
  if (Number(projectCount) > 0 && !currentProjectId) return true;
  // A project is open and its catalog has not landed. An error counts as
  // landed — the workspace shows the error, which beats a spinner that will
  // never stop.
  if (currentProjectId && !catalogHydrated && !catalogError) return true;
  return false;
}
