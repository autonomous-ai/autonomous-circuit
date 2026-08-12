// Fail-closed authorization for viewer-visible manufacturing artifacts.
//
// Stage 5 deliberately leaves diagnostic exports on disk even when a board is
// not orderable. File existence therefore says nothing about publication. The
// board sidecar is the authority: a manufacturing artifact is public only when
// `fab.ready` is the literal boolean true and the canonical artifact path is
// declared under its exact manifest key.

import fs from "node:fs";
import path from "node:path";

export const FAB_ARTIFACTS = Object.freeze([
  Object.freeze({ manifestKey: "gerbers", urlKey: "gerbersUrl", fileName: "gerbers.zip" }),
  Object.freeze({ manifestKey: "bom", urlKey: "bomUrl", fileName: "bom.csv" }),
  Object.freeze({ manifestKey: "cpl", urlKey: "cplUrl", fileName: "cpl.csv" }),
  Object.freeze({ manifestKey: "order", urlKey: "orderUrl", fileName: "ORDER.md" }),
  Object.freeze({ manifestKey: "glb", urlKey: "glbUrl", fileName: "board.glb" }),
  Object.freeze({ manifestKey: "enclosure", urlKey: "enclosureUrl", fileName: "enclosure.json" }),
  Object.freeze({
    manifestKey: "kicadProject",
    urlKey: "kicadProjectUrl",
    fileName: "kicad-project.zip",
  }),
]);

const FAB_ARTIFACT_BY_FILE = new Map(
  FAB_ARTIFACTS.map((artifact) => [artifact.fileName, artifact]),
);

function inside(root, candidate) {
  return candidate === root || candidate.startsWith(`${root}${path.sep}`);
}

function isSymlinkFreePath(root, candidate) {
  const relative = path.relative(root, candidate);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return false;
  let cursor = root;
  for (const segment of relative.split(path.sep).filter(Boolean)) {
    cursor = path.join(cursor, segment);
    try {
      if (fs.lstatSync(cursor).isSymbolicLink()) return false;
    } catch {
      return false;
    }
  }
  return true;
}

function readRegularJson(filePath, root) {
  try {
    if (!isRegularArtifactFile(filePath, root)) return null;
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

/** True only for an ordinary file reached without any symlink path segment. */
export function isRegularArtifactFile(filePath, projectDir) {
  try {
    const root = path.resolve(projectDir);
    const candidate = path.resolve(filePath);
    if (!inside(root, candidate) || !isSymlinkFreePath(root, candidate)) return false;
    const stat = fs.lstatSync(filePath);
    return stat.isFile() && !stat.isSymbolicLink();
  } catch {
    return false;
  }
}

/**
 * Classify and authorize one absolute path below a project workspace.
 *
 * `managed` is true for every path at or below a `<stem>_fab/` directory,
 * including unknown/nested names. Such paths fail closed unless they are one
 * direct canonical member declared by the sibling `<stem>.board.json`.
 */
export function authorizeFabArtifact({ projectDir, artifactPath }) {
  const root = path.resolve(projectDir);
  const candidate = path.resolve(artifactPath);
  if (!inside(root, candidate)) {
    return { managed: false, authorized: false, reason: "outside project" };
  }

  const relative = path.relative(root, candidate);
  const segments = relative ? relative.split(path.sep) : [];
  const fabIndex = segments.findIndex((segment) => segment.endsWith("_fab"));
  if (fabIndex < 0) {
    return { managed: false, authorized: false, reason: "not a fab artifact" };
  }

  const fabDirName = segments[fabIndex];
  const stem = fabDirName.slice(0, -"_fab".length);
  if (!stem || segments.length !== fabIndex + 2) {
    return { managed: true, authorized: false, reason: "non-canonical fab path" };
  }

  const definition = FAB_ARTIFACT_BY_FILE.get(segments[fabIndex + 1]);
  if (!definition) {
    return { managed: true, authorized: false, reason: "unknown fab artifact" };
  }

  const boardDir = path.join(root, ...segments.slice(0, fabIndex));
  const sidecarPath = path.join(boardDir, `${stem}.board.json`);
  const sidecar = readRegularJson(sidecarPath, root);
  if (!sidecar) {
    return { managed: true, authorized: false, reason: "missing or malformed sidecar" };
  }
  if (!sidecar.fab || sidecar.fab.ready !== true) {
    return { managed: true, authorized: false, reason: "fab.ready is not literal true" };
  }
  if (!sidecar.artifacts || typeof sidecar.artifacts !== "object" || Array.isArray(sidecar.artifacts)) {
    return { managed: true, authorized: false, reason: "missing artifact manifest" };
  }

  const expected = `${fabDirName}/${definition.fileName}`;
  if (sidecar.artifacts[definition.manifestKey] !== expected) {
    return { managed: true, authorized: false, reason: "artifact manifest mismatch" };
  }

  return {
    managed: true,
    authorized: true,
    definition,
    sidecarPath,
    expected,
  };
}
