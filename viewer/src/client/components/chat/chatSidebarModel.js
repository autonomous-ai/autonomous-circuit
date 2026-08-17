// Pure sidebar state decisions. Kept outside JSX so the Node test suite can
// pin the project-vs-conversation boundary without a browser transform.

export function shouldShowCreateStarters({
  projectId,
  project,
  historyLength = 0,
  isHydratingSession = false,
}) {
  if (isHydratingSession || historyLength > 0) return false;
  if (!projectId) return true;
  // While the project list is still loading, fail closed: never flash product
  // starters over an existing workspace whose status is not known yet.
  if (!project || project.id !== projectId) return false;
  // New servers provide the explicit workspace-content bit. The hasModel
  // fallback keeps older/native transports safe for already-built projects.
  return typeof project.isNew === "boolean" ? project.isNew : !project.hasModel;
}
