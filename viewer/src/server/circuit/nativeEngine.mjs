// Experimental prompt-to-KiCad mode is persisted per project, not inferred
// from the server default after a project has already been created.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
const exec = promisify(execFile);
export const NATIVE_ENGINE = 'kicad-native';
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../..');

export function nativeProject(workspace) {
  try { return JSON.parse(fs.readFileSync(path.join(workspace, 'project.json'), 'utf8')).engine === NATIVE_ENGINE; }
  catch { return false; }
}

export function nativeToolEnv(env = process.env) {
  return { ...env, PYTHONPATH: [path.join(repo, 'packages/kicadpy/src'), path.join(repo, 'packages/circuitpy/src'), env.PYTHONPATH].filter(Boolean).join(path.delimiter) };
}

export function nativePrompt(phase, workspace, env = process.env) {
  const python = env.CIRCUIT_PYTHON || 'python3.12';
  const command = `PYTHONPATH=${JSON.stringify(nativeToolEnv(env).PYTHONPATH)} ${JSON.stringify(python)} -m kicadpy.publish --manufacturing design/main.kicad_pro`;
  const common = [
    'You are the experimental KiCad-native v2 circuit designer. Speak the user’s language.',
    `The only project workspace is ${workspace}. Author sources inside design/ and scripts inside tools/.`,
    'KiCad .kicad_sch/.kicad_pcb plus .kicad_pro, project-local symbols/footprints and rules are the design source. product.json records requirements; parts.json records exact sourced parts.',
    'Use native KiCad Python (pcbnew) for PCB authoring and valid native schematic S-expressions. Read the installed API and inspect your outputs. No TSX authoring or circuitcode generator for this engine.',
    `Read ${path.join(repo, 'packages/kicadpy/README.md')} and ${path.join(repo, 'packages/kicadpy/PROMPT-WORKFLOW.md')} for the tool protocol and working commands.`,
    'Retain engineering knowledge from existing block documentation and parts tools, but verify pin maps and native footprints. Do not copy an unrelated reference board as the requested design.',
    'Safety requirements still apply: no mains, low-voltage DC <=24 V, batteries only via a sealed validated charge/protect module, radio only certified modules. Refuse requests outside this envelope.',
    'State uncertainties honestly. Native CAD checks do not establish engineering correctness, PCBA completeness or tested hardware. Prototype readiness requires the independently verified manufacturing packet and evidence-backed engineering review.',
    'The user does not read circuits. Engineering decisions are yours: part choices, values, copper, thermal, protection, process and order settings. Never ask the user a technical question — decide, write the reason and the evidence under engineering/, and say what only a bench test or the fab can settle. The user answers only what they can experience: which device it connects to, how big, which battery, what it must do.',
  ];
  if (phase === 'plan') return [...common,
    'This phase is read-only and capped at 40 minutes: no web research here, it belongs to the build turn. Resolve the brief, outline/size, power budget, components and net/pin allocation, placement, stackup and verification approach. Ask only about things the user can experience (device, size, battery, what it does), never about parts, voltages, rules or processes.',
    'For questions use a circuit-questions fenced JSON block with {"questions":[{"question":"...","header":"...","multiSelect":false,"options":[{"label":"Let Circuit choose","description":"Recommended default"},{"label":"...","description":"..."}]}]}. End the turn after questions.',
    'When ready, emit the COMPLETE plan in one circuit-plan fenced Markdown block. The app uses this fence as its approve button. Do not write files in this phase.',
  ].join('\n');
  if (phase === 'review') return [...common,
    `Read ${path.join(repo, 'packages/kicadpy/MANUFACTURING.md')}. You are the native post-build engineering and manufacturing reviewer.`,
    'Inspect the real native project, parts, requirements, manufacturer datasheets, and the latest boards/*_review/*/manufacturing/manufacturing-report.json. Do not trust earlier agent claims or counts.',
    'Resolve actionable CAD, sourcing and engineering findings. Preserve the requested function. Snapshot before editing; do not remove checks or relax clearances to erase findings. Read product.json remaining_engineering and resolve each with measured or calculated evidence, or retain it as a specific blocker.',
    'Write manufacturing.json and engineering/ evidence using the exact contract. Pass an area only with sufficient design evidence; distinguish bench tests that require physical prototypes from design prerequisites. Never invent measurements, availability, reviewed rotations, approval or report signatures.',
    `After revisions, update the exact designInputs and evidence digests, then run ${command}. Read the resulting findings.`,
    'At the end write .circuit/native-review-attestation.json with {status:"pass"|"blocked", reviewer:"your identity/model", summary:"your independent review conclusion and remaining limits", sourceFingerprints:[the exact source.fingerprint values from the final native board sidecars]}. A pass attests to your own review of these exact sources, not to earlier agent claims. Do not write pass if engineering or manufacturing blockers remain.',
    'Do not edit derived reports/sidecars. Do not order, upload, pay, or claim production hardware validation. Fix every blocker a board, parts or evidence edit can fix; for each one that remains, say who closes it — you in a later revision, the fab at quote time, or a bench test — never the user.',
  ].join('\n');
  return [...common,
    'The user approved implementation. Build the requested design now. Start with product.json, then native schematic with local symbols, PCB/footprints/netlist parity, placement and native routing.',
    'Use absolute executable paths after discovering KiCad. The host Python and KiCad bundled Python are different runtimes. Do not import pcbnew into the wrong interpreter.',
    'Keep existing correct copper. Use kicadpy snapshot/apply/route/check/commit/undo for supported changes; for other design changes first snapshot and work in a candidate copy. Keep the resulting source under design/main.*.',
    `After each complete design revision run: ${command}`,
    'The publisher writes native check reports and SVG previews under boards/main_review and a derived boards/main.board.json for the app. Read its findings, repair and republish. Do not author or modify generated sidecars to claim a pass.',
    `Read ${path.join(repo, 'packages/kicadpy/MANUFACTURING.md')} for engineering evidence and assembly requirements. Work through every finding you can close yourself before the turn ends. Finish with the native project path, what changed, what remains and who closes it — never a question to the user. The app runs a separate native review/repair loop and independent packet checks after your turn.`,
  ].join('\n');
}

export async function publishNativeWorkspace(workspace, env = process.env, signal) {
  const design = path.join(workspace, 'design');
  const projects = fs.readdirSync(design).filter(n => n.endsWith('.kicad_pro')).sort();
  if (!projects.length) throw new Error('V2 did not produce design/*.kicad_pro');
  for (const name of projects) {
    await exec(env.CIRCUIT_PYTHON || 'python3.12', ['-m', 'kicadpy.publish', '--manufacturing', path.join(design, name)],
      { cwd: workspace, env: nativeToolEnv(env), signal, timeout: 600000, maxBuffer: 2 * 1024 * 1024 });
  }
}

export function nativeManufacturingState(workspace) {
  const boards = path.join(workspace, 'boards');
  const reports = fs.readdirSync(boards).filter(n => n.endsWith('.board.json')).map(n => JSON.parse(fs.readFileSync(path.join(boards,n),'utf8')))
    .filter(r => r.source?.engine === NATIVE_ENGINE);
  return { ready: reports.length > 0 && reports.every(r => r.native?.publication === 'complete' && r.native?.manufacturing?.prototypeReady === true),
    publications: reports.map(r => ({source: r.source?.fingerprint, checked: r.native?.checkedRevision, files: r.native?.manufacturing?.files || {}})),
    signature: JSON.stringify(reports.map(r => [r.source?.fingerprint, r.native?.publication, r.native?.manufacturing?.prototypeReady, r.validation?.warnings?.length])),
    blockers: reports.flatMap(r => r.validation?.warnings || []).filter(w => w.severity === 'error').length };
}

export async function runNativeReviewLoop({ workspace, review, publish, onProgress, signal }) {
  const history = [];
  fs.mkdirSync(path.join(workspace,'.circuit'), {recursive:true});
  const journal = path.join(workspace,'.circuit/native-review.json');
  fs.writeFileSync(journal,JSON.stringify({state:'running',rounds:[]}));
  for (let round = 1; round <= 2; round++) {
    if (signal?.aborted) break;
    const before = nativeManufacturingState(workspace);
    onProgress(`Native engineering review ${round}/2: ${before.blockers} blocking findings.`);
    const outcome = await review(round);
    if (signal?.aborted || outcome?.failure) {
      history.push({ round, state: 'interrupted', failure: outcome?.failure || 'cancelled' });
      onProgress(`Native review interrupted: ${outcome?.failure || 'cancelled'}. No review pass was recorded.`);
      break;
    }
    await publish();
    const after = nativeManufacturingState(workspace);
    const attestation=outcome?.attestation;
    const attested=attestation?.status==='pass' && typeof attestation.reviewer==='string' && attestation.reviewer.trim() && typeof attestation.summary==='string' && attestation.summary.trim() && after.publications.every(p=>attestation.sourceFingerprints?.includes(p.source));
    history.push({ round, state: after.ready && attested ? 'ready-for-prototype' : 'blocked', blockers: after.blockers,
      attestation:attestation || null });
    if (after.ready || after.signature === before.signature) break;
  }
  const state = nativeManufacturingState(workspace);
  const verified = state.ready && history.some(r=>r.state==='ready-for-prototype') && !signal?.aborted && !history.some(r => r.state === 'interrupted');
  fs.writeFileSync(journal, JSON.stringify({state:verified?'verified':'blocked',rounds:history,publications:state.publications},null,2));
  // The publisher writes `fab.ready` false; the contract's order gate is set
  // here and nowhere else, once the journal says verified. Any later publish
  // resets it, so a board is never ready for a source nobody attested to.
  // Rewriting the sidecar also wakes the catalog without a manual reload.
  for (const name of fs.readdirSync(path.join(workspace,'boards')).filter(n=>n.endsWith('.board.json'))) {
    const file=path.join(workspace,'boards',name);
    let data; try { data=JSON.parse(fs.readFileSync(file,'utf8')); } catch { continue; }
    if (data?.source?.engine !== NATIVE_ENGINE) continue;
    data.fab = { ...(data.fab || {}), ready: verified && data.native?.manufacturing?.prototypeReady === true };
    fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
  }
  onProgress(verified ? 'Native prototype packet verified. Physical hardware remains untested.' : `Native manufacturing review stopped with ${state.blockers} blocking findings; see the manufacturing report.`);
  return history;
}
