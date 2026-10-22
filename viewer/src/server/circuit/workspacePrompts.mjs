// Prompt text is project-owned data. A working directory can host unrelated agent sessions,
// so reading Codex/Claude/Grok logs here would disclose private conversations in the viewer.
// Only explicit prompt files inside this project may be returned.

import fs from "node:fs";
import path from "node:path";

const MAX_PROMPT_BYTES = 1024 * 1024;
const DATED_ORIGINAL = /^\d{4}-\d{2}-\d{2}-original\.md$/;

function readPromptFile(root, member) {
  try {
    const file = fs.realpathSync(path.join(root, member));
    if (!file.startsWith(root + path.sep)) return null;
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > MAX_PROMPT_BYTES) return null;
    const text = fs.readFileSync(file, "utf8").trim();
    return text ? { text, at: new Date(stat.mtimeMs).toISOString(), source: "file", file: member } : null;
  } catch {
    return null;
  }
}

function singleDatedOriginal(root) {
  try {
    const history = fs.realpathSync(path.join(root, "prompts", "history"));
    if (!history.startsWith(root + path.sep)) return null;
    const names = fs.readdirSync(history)
      .filter((name) => DATED_ORIGINAL.test(name));
    return names.length === 1 ? readPromptFile(root, path.join("prompts", "history", names[0])) : null;
  } catch {
    return null;
  }
}

/** Return only files deliberately saved in this project; never inspect agent session logs. */
export function readWorkspacePrompts({ dir } = {}) {
  if (!dir) return { original: null, current: null, sessions: [] };
  let root;
  try {
    root = fs.realpathSync(dir);
  } catch {
    return { original: null, current: null, sessions: [] };
  }
  const original = readPromptFile(root, "prompts/original.md")
    || readPromptFile(root, "PROMPT.md")
    || singleDatedOriginal(root);
  const current = readPromptFile(root, "prompts/current.md")
    || readPromptFile(root, "PROMPT.md");
  // Keep the response shape for older clients, but never return a conversation transcript.
  return { original, current, sessions: [] };
}
