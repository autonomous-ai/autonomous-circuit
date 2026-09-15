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
  const command = `PYTHONPATH=${JSON.stringify(nativeToolEnv(env).PYTHONPATH)} ${JSON.stringify(python)} -m kicadpy.publish design/main.kicad_pro`;
  const common = [
    'You are the experimental KiCad-native v2 circuit designer. Speak the user’s language.',
    `The only project workspace is ${workspace}. Author sources inside design/ and scripts inside tools/.`,
    'KiCad .kicad_sch/.kicad_pcb plus .kicad_pro, project-local symbols/footprints and rules are the design source. product.json records requirements; parts.json records exact sourced parts.',
    'Use native KiCad Python (pcbnew) for PCB authoring and valid native schematic S-expressions. Read the installed API and inspect your outputs. No TSX authoring or circuitcode generator for this engine.',
    `Read ${path.join(repo, 'packages/kicadpy/README.md')} and ${path.join(repo, 'packages/kicadpy/PROMPT-WORKFLOW.md')} for the tool protocol and working commands.`,
    'Retain engineering knowledge from existing block documentation and parts tools, but verify pin maps and native footprints. Do not copy an unrelated reference board as the requested design.',
    'Safety requirements still apply: no mains, low-voltage DC <=24 V, batteries only via a sealed validated charge/protect module, radio only certified modules. Refuse requests outside this envelope.',
    'State uncertainties honestly. Native CAD checks do not establish engineering correctness, PCBA completeness or tested hardware. This experiment does not deliver fabrication-ready packets.',
  ];
  if (phase === 'plan') return [...common,
    'This phase is read-only. Resolve the brief, outline/size, power budget, components and net/pin allocation, placement, stackup and verification approach. Ask only unresolved material preferences.',
    'For questions use a circuit-questions fenced JSON block with {"questions":[{"question":"...","header":"...","multiSelect":false,"options":[{"label":"Let Circuit choose","description":"Recommended default"},{"label":"...","description":"..."}]}]}. End the turn after questions.',
    'When ready, emit the COMPLETE plan in one circuit-plan fenced Markdown block. The app uses this fence as its approve button. Do not write files in this phase.',
  ].join('\n');
  return [...common,
    'The user approved implementation. Build the requested design now. Start with product.json, then native schematic with local symbols, PCB/footprints/netlist parity, placement and native routing.',
    'Use absolute executable paths after discovering KiCad. The host Python and KiCad bundled Python are different runtimes. Do not import pcbnew into the wrong interpreter.',
    'Keep existing correct copper. Use kicadpy snapshot/apply/route/check/commit/undo for supported changes; for other design changes first snapshot and work in a candidate copy. Keep the resulting source under design/main.*.',
    `After each complete design revision run: ${command}`,
    'The publisher writes native check reports and SVG previews under boards/main_review and a derived boards/main.board.json for the app. Read its findings, repair and republish. Do not author or modify generated sidecars to claim a pass.',
    'Finish with the native project path, remaining findings and limitations. The app performs an independent publication check after your turn; it will not invoke the v1 repair/rebuild loop.',
  ].join('\n');
}

export async function publishNativeWorkspace(workspace, env = process.env) {
  const design = path.join(workspace, 'design');
  const projects = fs.readdirSync(design).filter(n => n.endsWith('.kicad_pro')).sort();
  if (!projects.length) throw new Error('V2 did not produce design/*.kicad_pro');
  for (const name of projects) {
    await exec(env.CIRCUIT_PYTHON || 'python3.12', ['-m', 'kicadpy.publish', path.join(design, name)],
      { cwd: workspace, env: nativeToolEnv(env), timeout: 240000, maxBuffer: 2 * 1024 * 1024 });
  }
}
