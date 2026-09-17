import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { nativeProject, nativePrompt, runNativeReviewLoop } from './nativeEngine.mjs';
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
  // The owner does not read circuits: no phase may end on a technical question.
  for (const phase of ['plan', 'implement', 'review']) assert.match(nativePrompt(phase, '/tmp/project'), /Never ask the user a technical question/);
  assert.match(nativePrompt('plan', '/tmp/project'), /capped at 40 minutes/);
  assert.match(nativePrompt('review', '/tmp/project'), /never the user/);
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
  assert.deepEqual(calls.map(c => c.phase), ['implement', 'review']);
  assert.match(calls[1].argv.join(' '), /native post-build/);
  const catalog = scanProjectCatalog({ projectDir: workspace, projectId: 'test' });
  const entries = Array.isArray(catalog) ? catalog : catalog.entries;
  const board = entries.find(e => e.file === 'design/tiny.kicad_pcb');
  assert.match(board.artifact.pcbUrl, /_pcb.svg/);
  assert.match(board.artifact.glbUrl, /board.glb/);
  assert.match(board.artifact.manufacturingReportUrl, /manufacturing-report.json/);
  assert.equal(board.nativeManufacturingVerified, false);
  const bom=path.join(workspace,meta.native.previewDir,'manufacturing/bom.csv');
  const originalBom=fs.readFileSync(bom);
  fs.appendFileSync(bom,'corrupted');
  const corruptCatalog=scanProjectCatalog({projectDir:workspace,projectId:'test'});
  const corrupt=(Array.isArray(corruptCatalog)?corruptCatalog:corruptCatalog.entries).find(e=>e.file===board.file);
  assert.equal(corrupt.nativeManufacturingVerified,false);
  assert.equal(corrupt.artifact.gerbersUrl,undefined);
  fs.writeFileSync(bom,originalBom);
  assert.ok(!entries.some(e => e.file.includes('_review/')));
  fs.appendFileSync(path.join(workspace, 'design/tiny.kicad_pcb'), '\n');
  const next = scanProjectCatalog({ projectDir: workspace, projectId: 'test' });
  const stale = (Array.isArray(next) ? next : next.entries).find(e => e.file === board.file);
  assert.equal(stale.nativeStale, true);
  assert.equal(stale.artifact.pcbUrl, undefined);
  assert.equal(stale.artifact.glbUrl, undefined);
});

test('a native packet with zero errors is ready; the attestation only decides the journal state', async () => {
  const workspace=fs.mkdtempSync(path.join(os.tmpdir(),'native-attestation-'));
  fs.mkdirSync(path.join(workspace,'boards'));
  fs.writeFileSync(path.join(workspace,'boards/main.board.json'),JSON.stringify({source:{engine:'kicad-native',fingerprint:'revision'},native:{publication:'complete',checkedRevision:'checked',manufacturing:{prototypeReady:true,files:{}}},validation:{warnings:[]}}));
  const args={workspace,publish:async()=>{},onProgress:()=>{}};
  await runNativeReviewLoop({...args,review:async()=>({})});
  const journal=()=>JSON.parse(fs.readFileSync(path.join(workspace,'.circuit/native-review.json')));
  // Zero errors and an intact packet: ready even before any attestation.
  assert.equal(journal().state,'ready-unattested');
  const attestation={status:'pass',reviewer:'test',summary:'Reviewed fixture',sourceFingerprints:['stale']};
  await runNativeReviewLoop({...args,review:async()=>({attestation})});
  assert.equal(journal().state,'ready-unattested');
  attestation.sourceFingerprints=['revision'];
  await runNativeReviewLoop({...args,review:async()=>({attestation})});
  assert.equal(journal().state,'verified');
});

test('native review stops when unchanged and reports provider failure without claiming pass', async () => {
  const workspace=fs.mkdtempSync(path.join(os.tmpdir(),'native-review-loop-'));
  fs.mkdirSync(path.join(workspace,'boards'));
  fs.writeFileSync(path.join(workspace,'boards/main.board.json'),JSON.stringify({source:{engine:'kicad-native',fingerprint:'revision'},native:{publication:'complete',manufacturing:{prototypeReady:false}},validation:{warnings:[{severity:'error'}]}}));
  let reviews=0, publications=0;const messages=[];
  const args={workspace,review:async()=>{reviews++;return {};},publish:async()=>{publications++;},onProgress:m=>messages.push(m)};
  const unchanged=await runNativeReviewLoop(args);
  assert.equal(reviews,1);assert.equal(publications,1);assert.equal(unchanged[0].state,'blocked');
  const failed=await runNativeReviewLoop({...args,review:async()=>({failure:'usage limit'})});
  assert.equal(failed[0].state,'interrupted');
  assert.ok(messages.some(m=>m.includes('usage limit')));
  // An interrupted round still republishes once, so the sidecar matches the disk.
  assert.equal(publications,2);
});
