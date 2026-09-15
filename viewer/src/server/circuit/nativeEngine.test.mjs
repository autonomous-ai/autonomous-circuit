import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { nativeProject, nativePrompt } from './nativeEngine.mjs';
import { createProjectsStore } from './projects.mjs';
import { buildCommandArgs, spawnTurn, sessionIdForProject } from './driver.mjs';
import { scanProjectCatalog } from './catalog.mjs';
import { isBoardEntry } from '../../client/lib/boardModel.js';
const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '../../../..');

test('native default is persisted for new projects and never switches old projects', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'native-store-'));
  const old = createProjectsStore({ rootDir: root, env: {} }).create('old');
  const store = createProjectsStore({ rootDir: root, env: { CIRCUIT_DEFAULT_ENGINE: 'kicad-native' } });
  const fresh = store.create('new');
  assert.equal(nativeProject(path.join(root, old.id)), false);
  assert.equal(nativeProject(path.join(root, fresh.id)), true);
  const args = buildCommandArgs({ workspace: path.join(root, fresh.id), phase: 'plan', sessionId: 'test', env: {} });
  const prompt = args[args.indexOf('--append-system-prompt') + 1];
  assert.match(prompt, /KiCad-native v2/);
  assert.match(prompt, /circuit-plan/);
  assert.doesNotMatch(prompt, /<Rp2040Core/);
  assert.match(nativePrompt('implement', '/tmp/project'), /kicadpy.publish/);
});

test('native sources are board entries, snapshots are not', () => {
  assert.ok(isBoardEntry({ file: 'design/main.kicad_pcb' }));
  assert.ok(!isBoardEntry({ file: '.kicadpy/candidate/design/main.kicad_pcb' }));
});

test('implementation turn publishes native previews and never invokes the v1 review loop', { timeout: 60000 }, async (t) => {
  if (!fs.existsSync('/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli')) return t.skip('KiCad integration requires local toolchain');
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'native-turn-'));
  const workspace = path.join(root, 'project');
  fs.mkdirSync(workspace);
  fs.writeFileSync(path.join(workspace, 'project.json'), JSON.stringify({ engine: 'kicad-native' }));
  const fixture = path.join(repo, 'packages/kicadpy/tests/fixtures/tiny');
  const writeFiles = [];
  function collect(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      if (e.isDirectory()) collect(full);
      else writeFiles.push({ path: `design/${path.relative(fixture, full)}`, content: fs.readFileSync(full, 'utf8') });
    }
  }
  collect(fixture);
  const scenario = path.join(root, 'scenario.json');
  const log = path.join(root, 'calls.jsonl');
  fs.writeFileSync(scenario, JSON.stringify({ implement: { writeFiles, lines: [{ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: 'Native design authored.' }] } }] } }));
  const events = [];
  await spawnTurn({ workspace, sessionId: sessionIdForProject('native-test'), message: 'Make this native board', phase: 'implement', turnId: 'native-turn',
    onEvent: e => events.push(e), env: { ...process.env, CIRCUIT_PYTHON: 'python3.12', CIRCUIT_CLAUDE_BIN: path.join(here, 'fixtures/fake-claude.mjs'), CIRCUIT_FAKE_SCENARIO: scenario, CIRCUIT_FAKE_LOG: log } });
  assert.ok(!events.some(e => e.kind === 'error'), JSON.stringify(events));
  const meta = JSON.parse(fs.readFileSync(path.join(workspace, 'boards/tiny.board.json')));
  assert.equal(meta.source.engine, 'kicad-native');
  assert.equal(meta.fab.ready, false);
  assert.equal(meta.native.publication, 'complete');
  const calls = fs.readFileSync(log, 'utf8').trim().split('\n').map(JSON.parse);
  assert.deepEqual(calls.map(c => c.phase), ['implement']);
  const catalog = scanProjectCatalog({ projectDir: workspace, projectId: 'test' });
  const entries = Array.isArray(catalog) ? catalog : catalog.entries;
  const board = entries.find(e => e.file === 'design/tiny.kicad_pcb');
  assert.match(board.artifact.pcbUrl, /_pcb.svg/);
  assert.ok(!entries.some(e => e.file.includes('_review/')));
  fs.appendFileSync(path.join(workspace, 'design/tiny.kicad_pcb'), '\n');
  const next = scanProjectCatalog({ projectDir: workspace, projectId: 'test' });
  const stale = (Array.isArray(next) ? next : next.entries).find(e => e.file === board.file);
  assert.equal(stale.nativeStale, true);
  assert.equal(stale.artifact.pcbUrl, undefined);
});
