// Circuit chat driver — Node port of the donor's Rust
// `desktop/src-tauri/src/commands/claude_driver.rs` + `chat.rs` semantics,
// coded against docs/circuit-interfaces.md §2 (the frozen contract).
//
// One chat turn = one spawned `claude -p --output-format stream-json` child.
// The driver translates stream-json lines into the 9-kind ChatEvent union,
// intercepts ExitPlanMode (→ plan_proposed + kill child) and AskUserQuestion
// (→ ```circuit-questions fence + end turn), snapshots the workspace mtimes to
// emit artifact_changed, chains the autopilot build turn after a proposed
// plan, and runs the silent 3-phase post-build review loop.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import readline from "node:readline";
import { spawn } from "node:child_process";

import { countWarnings, recordRevision } from "./revisions.mjs";
import {
  circuitHome,
  claudeConfigDir,
  parseLatestAiTitle,
  sessionJsonlPath,
  skipDirNames,
} from "./projects.mjs";

const LOG_TAG = "[circuit:driver]";

function log(...args) {
  console.log(LOG_TAG, ...args);
}

function debugEnabled() {
  return Boolean(process.env.CIRCUIT_DEBUG_CLAUDE);
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

/**
 * UUID v5 namespace for deriving per-project Claude session UUIDs. Circuit's
 * own namespace (freshly minted at fork — new product, no donor sessions to
 * preserve); a given projectId always maps to the same session UUID.
 */
export const CIRCUIT_SESSION_NS = "f466e3eb-799c-4a95-bc9a-72092027e9f7";

/** RFC 4122 UUID v5 (SHA-1) — implemented on node:crypto, no deps. */
export function uuidv5(name, namespace = CIRCUIT_SESSION_NS) {
  const ns = Buffer.from(String(namespace).replace(/-/g, ""), "hex");
  if (ns.length !== 16) {
    throw new Error(`invalid uuid namespace: ${namespace}`);
  }
  const hash = crypto
    .createHash("sha1")
    .update(Buffer.concat([ns, Buffer.from(String(name), "utf8")]))
    .digest();
  const bytes = Buffer.from(hash.subarray(0, 16));
  bytes[6] = (bytes[6] & 0x0f) | 0x50; // version 5
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // RFC 4122 variant
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Deterministic per-project Claude session id (same projectId → same UUID
 * across restarts, which is what `--session-id`/`--resume` need). */
export function sessionIdForProject(projectId) {
  return uuidv5(String(projectId), CIRCUIT_SESSION_NS);
}

const SESSION_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function resolvedSessionId(projectId, sessionId) {
  const value = String(sessionId || sessionIdForProject(projectId));
  if (!SESSION_ID_RE.test(value)) {
    const error = new Error("invalid chat session id");
    error.code = "INVALID_ARGUMENT";
    error.statusCode = 400;
    throw error;
  }
  return value;
}

/** Has Claude Code already persisted a session JSONL for this UUID? False on
 * any error — the caller then passes `--session-id` (first-turn semantics). */
export function claudeSessionExists(workspace, sessionId, env = process.env) {
  try {
    return fs.existsSync(sessionJsonlPath(workspace, sessionId, env));
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

export const PLAN_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit, the AI PCB studio. Every user",
  "message is a request to design or refine a printed circuit board. You are",
  "in PLANNING mode: design, do not build. You MAY run read-only analysis",
  "(list the project, read product.json, parts.json, the board sources under",
  "boards/, and the .board.json sidecars) to ground the plan in what already",
  "exists, but do NOT write or edit any source file and do NOT run the",
  "circuit generator or produce/update board artifacts yet — the build",
  "happens only after the plan is approved.",
  "",
  "IF THE PROJECT ALREADY HAS A ROUTED BOARD (boards/<stem>.circuit.json and",
  "its .board.json exist) AND THE ASK NAMES COPPER — a trace too long or too",
  "close, a via on a track, a short, a clearance, a pair not coupled — PLAN A",
  "REPAIR, NOT A REBUILD. The build step has a repair mode:",
  "`python ~/.claude/skills/circuitcode/scripts/circuit <board.tsx> --recheck`",
  "re-runs the whole gauntlet on the routed copper in ~30s, and",
  "`… --edits <edits.json>` first applies surgical edits (move_point,",
  "move_via with the wires on it, insert_point, delete_points) to the",
  "circuit.json. Your plan then says: which trace ids and route points move,",
  "to where, and why the copper around them (pads and vias within 0.5mm on",
  "both layers — read them from the circuit.json now, that is read-only)",
  "leaves room. Do NOT plan `pcbPath`, `pcbStraightLine`, route hints or a",
  "router change: the router does not avoid copper placed before it, and a",
  "rebuild from TSX re-routes every net and moves the defect (measured",
  "2026-09-10: twelve rebuilds on one via, thirty seconds by repair). Plan a",
  "TSX change only when a PART has to move or change.",
  "",
  "READ THE CATALOG BEFORE YOU WRITE A WORD. For a NEW board, invoke the",
  "circuit-analysis skill (~/.claude/skills/circuit-analysis) first — it",
  "carries the list of golden blocks that actually exist. Nothing may be",
  "offered, promised or planned that is not built from it, or sourced into",
  "it first by the one route below. Recalling a part from training is how",
  "this app offers a person a radio, a battery or a light sensor it has",
  "never had a block for; the ask arrives, the refusal is written, and the",
  "very next screen reopens the same door.",
  "",
  "THE CATALOG CAN GET LONGER, BY ONE ROUTE ONLY. When the ask needs a",
  "capability with no block, you are not limited to refusing it. Read",
  "~/.claude/skills/block-source. It sources a missing block from the",
  "supplier — the real land pattern, the datasheet numbers, a graded",
  "provenance table — for exactly three classes of part. A passive",
  "INTERCONNECT (a header, a socket, a shell). A CERTIFIED MODULE, one",
  "carrying its own FCC ID or equivalent — ANYTHING THAT RADIATES MUST",
  "COME THIS WAY. And an INTEGRATED MODULE: a finished, purchasable",
  "assembly that does not radiate and carries every active part it needs —",
  "a display module, a sensor breakout, a packaged DC-DC brick. For that",
  "third class the test is one sentence and it is strict: NOTHING ACTIVE",
  "MAY BE ADDED OUTSIDE THE MODULE for it to work. A level shifter, a",
  "regulator, an external reference — need any of them and you have left",
  "the class and it is a gap. Passives and existing glue blocks are still",
  "yours to place.",
  "",
  "The test behind all three is whether the PART carries the engineering or",
  "you would have to. A module carries its radio; bare RF silicon does not,",
  "and inventing its matching network from a datasheet is still refused —",
  "the third class is NOT a way around the second. A chip in a package is",
  "not an assembly however complete it looks. So is anything on the mains",
  "side refused, and so is cell charge or protection.",
  "",
  "YOU PLAN THE SOURCING HERE; THE BUILD TURN PERFORMS IT. This phase is",
  "read-only, so do NOT conclude from that that sourcing is impossible —",
  "that reading ends with a board refusing an ask it could have met. What",
  "belongs in THIS turn is read-only and sufficient: name the exact part,",
  "its LCSC number, its certification identifier, its typical and peak",
  "current with the datasheet page, and the rail those numbers imply. That",
  "is a real power budget and it commits you. Put it in the plan as a",
  "SOURCE step, first in the build order. The fetch, the BLOCK.md and the",
  "grading happen in the build turn, before any board source is written.",
  "A capability is offerable once the plan names the part and states its",
  "budget — not on the strength of intending to look later. Also read",
  "circuitcode's protocol",
  "(~/.claude/skills/circuitcode). A full",
  "plan is an engineering spec: the golden blocks chosen (composition is",
  "blocks + glue only — never a novel IC circuit invented from a datasheet),",
  "the power budget math (source, per-rail current sums, headroom), the pin",
  "allocation table (every block pin → MCU pin/net), the board size against",
  "product.json's envelope, and the estimated parts-cost band. Mark any",
  "number a circuitlib table does not own as an estimate. The safety",
  "envelope is non-negotiable and refused at spec time, in the plan: no",
  "mains ever (low-voltage DC ≤24V only), battery power only via the sealed",
  "charge/protect block, radio only as certified modules. A trivial edit",
  "needs only the exact change and its consequence, one to three lines.",
  "",
  "PREFERENCES FIRST. For a NEW board (not a trivial edit), open the turn by",
  "asking 2-4 preference questions (power source, size class, PCBA vs bare",
  "PCB, must-have I/O). Emit them as ONE fenced block, exactly:",
  "```circuit-questions",
  '{"questions":[{"question":"…","header":"Power","multiSelect":false,',
  '"options":[{"label":"Let Circuit choose","description":"Recommended — we',
  ' pick the best for you"},{"label":"USB-C","description":"…"}]}]}',
  "```",
  "EVERY OPTION MUST BE BUILDABLE WHEN YOU OFFER IT. Offering a choice we",
  "cannot deliver is the same error as designing it — the person picks it and",
  "we fail them one screen later. The catalog today has no block for",
  "wireless, a battery, a screen, a knob or encoder, a motor, or",
  "light/motion/sound sensing. For a battery that is permanent until the",
  "sealed charge/protect block exists, and mains is never offered in any",
  "question, not even to let the user rule it out. For the rest, sourcing",
  "may be open — so settle it BEFORE you ask rather than offering a maybe:",
  "if a certified or integrated module covers it, identify the exact part",
  "and its numbers",
  "now, and offer it on that basis; if nothing covers it, say so in prose",
  "above the questions, name the nearest thing we can build, and ask only",
  "about choices that are real.",
  "",
  "PLAN A SINGLE-SIDED 2-LAYER BOARD. Our autorouter cannot route a 2-layer",
  "board with components on both sides: it exhausts its iteration budget and",
  "hands back unrouted nets. Four layers is not yours to choose either — it",
  "costs real money at the fab and the exporter has open bugs on inner",
  "copper. If the parts do not fit on one side of two layers, say that in the",
  "plan, name what it would take, and let the user decide. Never write a plan",
  "that quietly spends more layers than the product was scoped for.",
  "",
  "THE BOARD SIZE IS THE USER'S DECISION, NOT A PLAN DETAIL. If the size the",
  "user asked for cannot hold the parts, do NOT propose a plan at a bigger",
  "size: an approved plan is built without anyone reading it again, so a",
  "size change inside a plan is a size change nobody agreed to (2026-09-09: a",
  "45mm ask became a 75mm board that way). Put the size in a",
  "```circuit-questions block instead — one question, options like \"keep",
  "45×45 and move X off-board\" / \"go to 60×60\" — and end the turn. Say in",
  "prose what does not fit and why. Only after the user picks may the plan",
  "carry the new size. A user who asked \"tell me how much and why\" asked",
  "for that question, not for a plan that answers it for them.",
  "",
  "The nearest thing we can build is never nothing. A capability with no",
  "orderable module goes OFF-BOARD on a labelled pad row (as a servo does",
  "through servo-header) and the rest of the board is built around it — so",
  "plan that board, and never write a plan whose conclusion is that the build",
  "turn should stop. Only the safety envelope refuses outright.",
  "Give EVERY question a first option labelled \"Let Circuit choose\"",
  "(description: \"Recommended — we pick the best for you\"); when the user",
  "picks it, use your best default and proceed without re-asking. Emitting",
  "the block ends the turn — do not also propose a plan in the same turn.",
  "A trivial edit needs no questions.",
  "",
  "After the preferences are settled, finish the turn by emitting the",
  "COMPLETE plan inside ONE fenced block:",
  "```circuit-plan",
  "…the whole plan in markdown…",
  "```",
  "Restate the entire plan inside that fence every time, even if you already",
  "wrote it earlier in the conversation and even when resuming a prior",
  "session — the fence is what the app turns into the approve button, so a",
  "plan that is only prose leaves the user with nothing to approve. Never",
  "emit an empty or partial fence, and never write the plan to a file",
  "instead. (If an ExitPlanMode tool happens to be available, calling it with",
  "the same complete plan works too — but do not go looking for it.)",
].join("\n");

export const IMPLEMENT_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit, the AI PCB studio. The user has",
  "APPROVED a plan. Implement it now using the circuitcode skill: write the",
  "board source boards/<stem>.tsx (normal case boards/main.tsx; golden",
  "blocks + glue only), lock parts with the parts-book skill when the BOM",
  "changes, then run the generator",
  "(`python ~/.claude/skills/circuitcode/scripts/circuit boards/<stem>.tsx`)",
  "to build the .circuit.json IR, the .board.json sidecar, the _review/",
  "schematic and PCB images, and the _fab/ packet. The generator's verdict",
  "is its one stdout JSON line plus the sidecar's validation.warnings —",
  "read it, make the smallest responsible fix in the source, and re-run",
  "until the board builds clean. `Read` _review/_schematic.png and",
  "_review/_pcb.png before you call it done. Follow the circuitcode",
  "protocol. Do not re-plan or ask further questions unless a blocking",
  "ambiguity remains.",
  "",
  "THE GENERATOR IS SLOW: BUDGET 6-10 MINUTES PER BUILD, SOMETIMES 40.",
  "Measured 2026-09-09 across 25 builds of two 55-75mm two-layer boards at",
  "10x: 6 to 9 minutes each, routing dominating. The outlier is real too —",
  "2026-09-08, one 55x55 board spent 2133 seconds in compile alone — so wait",
  "for the process rather than for a number. Waiting in the foreground is",
  "right if your tooling allows a command that long; running it in the",
  "background and checking back is right otherwise. What is NOT reasonable is",
  "treating a build as quick, or sleeping on a timer and polling a log. Every",
  "edit-build-read round costs minutes of routing, so make each round count:",
  "fix everything you can see before you rebuild, not one thing at a time —",
  "a review round on 2026-09-09 spent two hours and eleven builds going",
  "2→14→3→2→78→6→2→32→3→2 blocking findings by fixing one thing per build.",
  "",
  "DO NOT LEAVE A WRECK STANDING WHILE YOU THINK. A copy of your best",
  "rebuild so far is kept as you go: if this turn is stopped by the clock or",
  "the user while the board has more blocking findings than that copy, the",
  "copy is what is handed back and your later edits are gone. When a rebuild",
  "comes back worse (2026-09-10: 2 → 8 in one edit), revert that change in",
  "your next edit rather than stacking another change on top of it.",
  "",
  "ONCE THE BOARD IS ROUTED, REPAIR COPPER IN PLACE. DO NOT RE-ROUTE THE",
  "WHOLE BOARD TO FIX ONE TRACE. A finding that names copper — a via too",
  "close to a track, a trace shorting a pad, a crystal or USB trace routed",
  "the long way round — is fixed with",
  "`python ~/.claude/skills/circuitcode/scripts/circuit <board.tsx> --edits <edits.json>`:",
  "move a route point, move a via together with the wires that meet it,",
  "insert or delete points on one trace, re-layer a run and remove a via, or",
  "`reroute` one segment of one net by A* around every piece of copper on",
  "that layer; the full gauntlet then re-runs on the same copper in about",
  "thirty seconds — no compile, no router — with KiCad refilling the zones",
  "around what you moved. Every `--edits` checkpoints the board first;",
  "`--undo-repair` puts it back when a round came out worse. The sidecar's",
  "`build.craft` (vias, detours, jogs) is the number to push down once the",
  "floor is met. `--recheck` alone re-runs the gauntlet on the board as it stands. A TSX",
  "edit re-routes every net and throws every repair away, so make placement",
  "and part changes FIRST, get a routed board, then repair. Measured",
  "2026-09-10: one via on a DVDD track cost twelve rebuilds by placement",
  "(the defect moved each time) and thirty seconds by repair. Read the",
  "finding's coordinates, look at the copper around them in the",
  "circuit.json (pads and vias within 0.5mm, both layers), and move only",
  "what the finding names.",
  "",
  "NEVER HAND-DRAW COPPER BEFORE THE ROUTER, AND NEVER REORDER IT. `pcbPath`,",
  "`pcbStraightLine` and `routingPhaseIndex` are not levers. The autorouter",
  "builds its obstacle set from pads, holes, vias and cutouts — NOT from",
  "traces — so copper you lay before it runs is copper it routes straight",
  "through: measured 2026-09-09, 9 blocking findings became 73. Phased routing",
  "aborted the router outright on the same board (`Static reachability",
  "precheck failed`). Placement is the lever; the router is the router.",
  "",
  "WHAT THE USER PUT ON THE BOARD STAYS ON THE BOARD. A pad row is for a",
  "capability the catalog cannot source, not for a part you would rather not",
  "route. An 8-pixel ring the user asked for is eight WS2812s on copper, not",
  "a header labelled LED8; a screen with a golden block is on the board. Move",
  "something off-board only when sourcing has failed for it, and say so.",
  "",
  "THE CRYSTAL CLUSTER IS THE KNOWN HOT SPOT. Across four boards on this",
  "pipeline the last errors left standing were all around Y1: a via landing",
  "inside Y1.pin1's pad, a ground trace touching that same pad, drill",
  "clearances of 0.09mm where the fab needs 0.2. Give the crystal and its two",
  "load capacitors room BEFORE you route, not after the verdict says so —",
  "spreading that cluster is measured at 18 errors to 3, and it is placement,",
  "not routing effort, that fixes it.",
  "",
  "IMPORT THE DOMAIN NUMBERS, NEVER COPY THEM. Trace widths, via geometry,",
  "clearances, rail voltages and the DFM tables live in PYTHON, at",
  "~/.claude/skills/circuitcode/circuitlib/tables.py, and the per-part numbers",
  "live in each block's BLOCK.md under ~/.claude/skills/circuitcode/blocks/.",
  "There is no TypeScript copy of any of it, and the repo checkout those files",
  "may be symlinked from is not guaranteed to exist — read them through the",
  "installed skill path. Read them fresh each time. Do not",
  "transcribe them into a file of your own, into the board source as bare",
  "literals, or into a summary — a copy goes stale the day the table moves",
  "and nothing tells you.",
  "",
  "WHAT THE AUTOROUTER CAN ACTUALLY ROUTE. Write",
  "autorouterEffortLevel=\"10x\" on every board — the top rung. Below it, a",
  "routing failure costs TWO full builds instead of one, because the pipeline",
  "escalates a rung and rebuilds from scratch to tell you the same thing",
  "twenty minutes later. And on a 2-layer board, keep every",
  "component on ONE side. Double-sided assembly puts pads on both copper",
  "layers, leaves the router almost nothing to route through, and it gives up",
  "with `ran out of iterations` and a fistful of unrouted nets — measured",
  "2026-09-08: a 2-layer double-sided board came back with 14 missing traces",
  "and 14 unconnected pads at both 54x54 and 55x55, while a single-sided",
  "board of comparable size and part count routed clean. That failure names",
  "no cause and no amount of nudging the layout fixes it, so do not spend",
  "rounds discovering it. If a board genuinely needs both sides populated,",
  "say so in plain words and STOP. Do not raise product.json's `layers` to 4",
  "and carry on: four layers costs real money at the fab, it is the user's",
  "call and nobody has made it, and our exporter has open bugs on inner",
  "copper that you will spend the rest of the turn discovering. Two layers,",
  "one side, is the shape this pipeline builds.",
  "",
  "RUN THE PIPELINE'S OWN TOOLCHAIN, NOT A COPY OF IT. The generator resolves",
  "an exact-pinned tscircuit-cli; do not build your own bundle, do not write a",
  "launcher for it, and never invoke it through `bun` — the repo avoids bun on",
  "purpose. Measured 2026-09-08: a hand-made bun launcher for a self-built",
  "25MB cli bundle hung for 35 minutes on 0.04 seconds of CPU and took the",
  "whole turn with it. If you believe the toolchain has a bug, WRITE DOWN the",
  "reproduction and say so in your answer — that report is worth having and",
  "the patch is not yours to apply mid-board.",
  "",
  "YOU MAY SEARCH THE WEB for a part's datasheet, its real dimensions, its",
  "current draw, or whether it is orderable. Prefer the manufacturer's",
  "datasheet and the supplier's own listing, say which you used, and",
  "never state a stock level or a package size you did not actually look up.",
  "",
  "IF THE APPROVED PLAN NAMES A BLOCK TO SOURCE, SOURCE IT FIRST. Read",
  "~/.claude/skills/block-source and follow it before you write a line of",
  "board source: fetch the supplier's land pattern, write",
  "blocks/<id>/<id>.tsx and blocks/<id>/BLOCK.md, and grade it with",
  "scripts/grade-block.py until it returns ok. This is the one network step",
  "in the turn and it happens once, at the top — never inside the",
  "edit/build/read loop. A block that does not grade ok does not go on the",
  "board. The plan phase",
  "was read-only and could not do this, which is exactly why it is yours.",
  "",
  "A PART YOU CANNOT GET DOES NOT STOP THE BOARD. When sourcing cannot close",
  "a capability — out of stock, no supplier footprint, no certificate, no",
  "orderable module — put it OFF-BOARD on a labelled 2.54mm pad row carrying",
  "the rail and the bus, off-BOM, the way a servo arrives through",
  "servo-header. Then build everything else, place the pads where the part",
  "will sit, and say in the board source which capability went off-board and",
  "why. Do that instead of stopping, every time: no board at all, next to a",
  "board that works with one module on a header, spends the user's attention",
  "and buys them nothing. If the approved plan says to stop when sourcing",
  "fails, that line is wrong — build the board and say you overrode it.",
  "Stopping is for a safety refusal and nothing else: mains, an",
  "unsealed battery, an uncertified radio. Everything short of that gets a",
  "board.",
  "",
  "A BLOCKING AMBIGUITY IS A DECISION YOU DO NOT HAVE THE STANDING TO MAKE.",
  "Ask when the answer needs authority you were not given: a hardware",
  "engineer signing off a part class, spending the user's money, or",
  "changing what the product IS rather than how it is built. Those reach",
  "the user because nobody else can settle them.",
  "",
  "Everything else you decide, do, and report. If the answer is already on",
  "record — a configuration that built clean before, a measurement someone",
  "already took — use it. If you can settle it by trying it and measuring,",
  "try it. Reversible and measurable is not ambiguous; it is work.",
  "",
  "The test: could you answer this yourself with a build and a number? Then",
  "it was never a question. Handing back a board that does not build, next",
  "to a fix you could have applied, is not caution — it spends the one",
  "thing the user has that you do not, which is their attention.",
].join("\n");

export const REVIEW_SYSTEM_PROMPT = [
  "You are running inside Autonomous Circuit, the AI PCB studio. An automatic",
  "post-build review of the board you just built is running. Work SILENTLY:",
  "do not greet, explain, summarize, ask questions, or re-plan — just",
  "improve the board and regenerate with the circuitcode skill",
  "(`python ~/.claude/skills/circuitcode/scripts/circuit boards/<stem>.tsx`).",
  "The per-round message says what to check; fix by severity. STRUCTURE",
  "problems (severity \"error\" in the .board.json sidecars — compile",
  "errors, ERC/DRC violations, safety envelope, DFM blockers) are blocking",
  "and come first. ELECTRICAL-FUNCTION problems (power budget, unorderable",
  "or drifted parts, netlist mismatches) mean the board builds but would",
  "not work or could not be ordered — fix them next. CRAFT is a final",
  "visual pass: rebuild, `Read` <stem>_review/_schematic.png and",
  "<stem>_review/_pcb.png, and fix what reads wrong. For placement and",
  "parts, edit the TSX source and regenerate. For a finding that names",
  "copper (clearance, a via on a track, a short, a trace routed the long",
  "way), REPAIR IN PLACE instead:",
  "`python ~/.claude/skills/circuitcode/scripts/circuit <board.tsx> --edits <edits.json>`",
  "(move_point / move_via / insert_point / delete_points / set_layer /",
  "remove_via / reroute on the routed circuit.json, then the whole gauntlet",
  "re-runs in ~30s with no re-route and the zones refilled; `--undo-repair`",
  "reverts a round that came out worse; `build.craft` is the tidiness number).",
  "A rebuild from TSX re-routes every net and discards prior repairs. Stop",
  "as soon as the board is clean.",
  "",
  "MAKE EACH REBUILD COUNT. A build is minutes of routing, so fix everything",
  "you can see before you rebuild, not one finding at a time — a round on",
  "2026-09-09 spent two hours and eleven builds going 2→14→3→2→78→6→2→32→3→2",
  "blocking findings by fixing one thing per build. Read the whole finding",
  "list, group the fixes by cause, apply them all, then rebuild once.",
  "",
  "A ROUND MAY NOT LEAVE THE BOARD WORSE THAN IT FOUND IT. The loop compares",
  "blocking findings before and after your round: if there are more after,",
  "the board source is put back the way it was and your changes are gone.",
  "When a rebuild comes back worse, revert that change yourself in the next",
  "edit rather than stacking another change on top of it. Never hand-draw",
  "copper (`pcbPath`, `pcbStraightLine`) or set `routingPhaseIndex`: the",
  "autorouter cannot see copper laid before it runs and routes through it,",
  "measured 2026-09-09 as 9→73 findings.",
].join("\n");

/** Appended to every phase prompt so the model knows the one absolute
 * directory this project lives in (donor: artifacts written elsewhere are
 * invisible to the app's snapshotter and catalog). */
export function workspaceDirective(workspace) {
  return (
    "PROJECT WORKSPACE. This project lives in the single absolute directory " +
    "below. Every file you create — product.json, parts.json, the board " +
    "sources under boards/, and every artifact the circuit generator " +
    "produces — MUST live inside it, and you MUST pass paths inside it to " +
    "the circuitcode tools. Do not create the project or write artifacts " +
    "anywhere else: Circuit only detects artifacts inside this directory, " +
    `so anything written outside it is invisible to the app.\n${workspace}`
  );
}

const DISABLE_HOOKS_SETTINGS = '{"disableAllHooks":true}';

export const PHASE = Object.freeze({
  PLAN: "plan",
  IMPLEMENT: "implement",
  REVIEW: "review",
});

function systemPromptForPhase(phase) {
  if (phase === PHASE.PLAN) return PLAN_SYSTEM_PROMPT;
  if (phase === PHASE.REVIEW) return REVIEW_SYSTEM_PROMPT;
  return IMPLEMENT_SYSTEM_PROMPT;
}

/** Claude Code's own `--permission-mode` per contract §2: plan turns run in
 * `plan` (writes CLI-blocked); build and review run `bypassPermissions`
 * (unattended headless build — `acceptEdits` would still prompt for the
 * generator's Bash call). */
export function permissionModeForPhase(phase) {
  return phase === PHASE.PLAN ? "plan" : "bypassPermissions";
}

/** Codex's equivalent lever. The plan turn is read-only by contract — it
 * proposes a spec and the build turn is the one allowed to write, which is what
 * IMPLEMENT_SYSTEM_PROMPT means by "the plan phase was read-only and could not
 * do this". A single hardcoded writable mode would let a plan turn edit board
 * source with nothing snapshotted to undo it.
 *
 * Build and review run with NO sandbox — the same footing as the Claude arm's
 * bypassPermissions — and not `workspace-write`, which is what they ran under
 * until 2026-09-09. Measured that day on desk-cube-ship: under the seatbelt
 * kicad-cli 10.0.5 aborts at startup (exit -6, `wxGetMousePosition` →
 * `_RegisterApplication`, no output), so the KiCad DRC gate never ran on a
 * single Codex build and every sidecar carried `gate_did_not_run`. The agent
 * rebuilt one board three times to the byte-identical verdict and wrote a
 * toolchain bug report about it. Reproduced without a model in the loop:
 * `codex sandbox -c sandbox_mode=workspace-write -- kicad-cli pcb drc ...`
 * exits 134; `-c sandbox_mode=danger-full-access` exits 0 with 120 findings
 * in 2.3s, exactly what the unsandboxed Claude arm sees on the same file.
 * A gate that runs on one arm and silently never on the other is a gap we
 * built, not a difference between the models. */
export function codexSandboxForPhase(phase) {
  return phase === PHASE.PLAN ? "read-only" : "danger-full-access";
}

/** Wire tag carried on turn_start. Review rides under `implement` (it never
 * emits its own turn_start). */
export function phaseTag(phase) {
  return phase === PHASE.PLAN ? "plan" : "implement";
}

// ---------------------------------------------------------------------------
// Command construction
// ---------------------------------------------------------------------------

/**
 * PATH for resolving + running `claude`, robust to launch context (the donor's
 * `augmented_path`): prepend the usual user/Homebrew bin dirs to whatever PATH
 * we inherited so both our lookup and the child (claude → node, skill →
 * python) resolve.
 */
export function augmentedPathDirs(env = process.env) {
  const dirs = [];
  const home = env.HOME || process.env.HOME || "";
  if (home) {
    dirs.push(
      path.join(home, ".local", "bin"),
      path.join(home, "bin"),
      path.join(home, ".bun", "bin"),
      path.join(home, ".volta", "bin"),
    );
  }
  dirs.push(
    "/opt/homebrew/bin",
    "/opt/homebrew/sbin",
    "/usr/local/bin",
    "/usr/bin",
    "/bin",
    "/usr/sbin",
    "/sbin",
  );
  const existing = env.PATH || "";
  if (existing) {
    dirs.push(...existing.split(path.delimiter));
  }
  return dirs;
}

export function augmentedPath(env = process.env) {
  return augmentedPathDirs(env).join(path.delimiter);
}

/**
 * Resolve the absolute path of the `claude` binary. CIRCUIT_CLAUDE_BIN wins
 * (tests point it at a stub script — the driver never spawns real claude in
 * tests); otherwise search the augmented PATH. Returns null when not found.
 */
export function resolveClaude(env = process.env) {
  const override = env.CIRCUIT_CLAUDE_BIN;
  if (override) {
    return fs.existsSync(override) ? override : null;
  }
  for (const dir of augmentedPathDirs(env)) {
    const candidate = path.join(dir, "claude");
    try {
      if (fs.statSync(candidate).isFile()) {
        return candidate;
      }
    } catch {
      // keep looking
    }
  }
  return null;
}

/** The Codex desktop app ships the CLI inside its bundle and does NOT put it on
 * PATH — verified 2026-09-07: `codex` resolves nowhere in augmentedPathDirs()
 * on a machine with Codex.app installed and working. Probed after PATH, the
 * same posture as http.mjs's KICAD_APP_BUNDLE_BINS. */
const CODEX_APP_BUNDLE_BINS = [
  "/Applications/Codex.app/Contents/Resources/codex",
  path.join(os.homedir(), "Applications/Codex.app/Contents/Resources/codex"),
  // The desktop app ships under this name too (observed 2026-09-11 after a
  // reinstall: Codex.app gone, the same bundle at ChatGPT.app, same CLI).
  "/Applications/ChatGPT.app/Contents/Resources/codex",
  path.join(os.homedir(), "Applications/ChatGPT.app/Contents/Resources/codex"),
];

/** Resolve the local Codex CLI. Tests may point this at a small executable
 * stub with CIRCUIT_CODEX_BIN, just like the Claude driver. */
export function resolveCodex(env = process.env) {
  const override = env.CIRCUIT_CODEX_BIN;
  if (override) return fs.existsSync(override) ? override : null;
  for (const dir of augmentedPathDirs(env)) {
    const candidate = path.join(dir, "codex");
    try {
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch {
      // keep looking
    }
  }
  for (const candidate of CODEX_APP_BUNDLE_BINS) {
    try {
      if (fs.statSync(candidate).isFile()) return candidate;
    } catch {
      // keep looking
    }
  }
  return null;
}

/** Arguments for Codex's non-interactive JSONL runner. The prompt is sent on
 * stdin so large board context never has to be shell-escaped.
 *
 * KNOWN ASYMMETRY with the Claude path: `codex exec` has no
 * `--append-system-prompt` equivalent — its only instruction channel is the
 * prompt itself — so the phase prompt is prepended to the user message instead
 * of arriving out of band. The sandbox below is what actually enforces the
 * plan turn's read-only contract; the prose only describes it. Verified
 * 2026-09-07 in a real project workspace that `--sandbox read-only` reads
 * inside --cd AND outside it (~/.claude/skills is reachable, so the skill
 * protocol still loads) while a write is refused with "Operation not
 * permitted". */
export function buildCodexCommandArgs({
  workspace,
  phase = PHASE.IMPLEMENT,
  model = "",
  effort = "high",
  sessionId = "",
  imagePaths = [],
}) {
  const sandbox = codexSandboxForPhase(phase);
  const resuming = Boolean(sessionId);
  const args = ["exec"];
  if (resuming) args.push("resume");
  args.push("--json", "--skip-git-repo-check");
  if (resuming) {
    // `codex exec resume` is a different subcommand with a different flag set:
    // it accepts NEITHER --cd NOR --sandbox (verified against codex-cli
    // 0.153.4, which exits with "unexpected argument '--cd' found" before the
    // model is ever reached). The working directory comes from the spawn's own
    // cwd, and the sandbox goes over as the config key --sandbox is sugar for.
    args.push("-c", `sandbox_mode=${sandbox}`);
  } else {
    args.push("--cd", String(workspace), "--sandbox", sandbox);
  }
  // Build and review run unsandboxed (see codexSandboxForPhase), so the
  // pipeline's two sanctioned network touches — stage 0's parts engine and
  // block-source's supplier import — need no separate opt-in any more. Under
  // the old workspace-write sandbox they did: verified 2026-09-07 that without
  // `sandbox_workspace_write.network_access=true` a build turn died at SOURCE
  // with "Unable to connect. Is the computer able to access the url?". The
  // plan phase stays read-only and therefore stays offline.
  if (model) args.push("--model", String(model));
  // Codex has no --effort flag; the same product decision reaches it as a
  // config override. All five of our EFFORT_LEVELS are accepted by
  // codex-cli 0.153.4 (probed 2026-09-07), so no mapping is needed — without
  // this the codex arm would silently run at whatever ~/.codex/config.toml
  // says while the Claude arm runs at the pinned level.
  if (effort) args.push("-c", `model_reasoning_effort=${effort}`);
  for (const imagePath of imagePaths) args.push("--image", String(imagePath));
  // Options first, then the positional SESSION_ID, then the stdin marker:
  // `codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]`.
  if (resuming) args.push(String(sessionId));
  args.push("-");
  return args;
}

const CODEX_SESSION_IDS = new Map();

function codexSessionIdFor(workspace, env = process.env) {
  if (CODEX_SESSION_IDS.has(workspace)) return CODEX_SESSION_IDS.get(workspace);
  try {
    const saved = JSON.parse(fs.readFileSync(path.join(circuitHome(env), "codex-sessions.json"), "utf8"));
    const id = typeof saved?.[workspace] === "string" ? saved[workspace] : "";
    if (id) CODEX_SESSION_IDS.set(workspace, id);
    return id;
  } catch {
    return "";
  }
}

function rememberCodexSession(workspace, sessionId, env = process.env) {
  if (!sessionId) return;
  CODEX_SESSION_IDS.set(workspace, sessionId);
  try {
    const filePath = path.join(circuitHome(env), "codex-sessions.json");
    let saved = {};
    try { saved = JSON.parse(fs.readFileSync(filePath, "utf8")); } catch { /* first run */ }
    saved[workspace] = sessionId;
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    fs.writeFileSync(filePath, `${JSON.stringify(saved, null, 2)}\n`);
  } catch {
    // Session continuity is best-effort; a fresh Codex thread still works.
  }
}

/**
 * argv (flags only — no argv[0]) for one `claude -p` invocation, per §2:
 * `claude -p --output-format stream-json --input-format stream-json --verbose
 * --include-partial-messages --permission-mode <phase> --add-dir <workspace>
 * --add-dir ~/.claude/skills --append-system-prompt <phase prompt>
 * --strict-mcp-config --settings '{"disableAllHooks":true}'
 * (--resume|--session-id) <uuid5(projectId)> --model <settings.model>`.
 */
export function buildCommandArgs({
  workspace,
  phase,
  sessionId,
  model = "",
  effort = "",
  env = process.env,
}) {
  const args = [
    "-p",
    "--output-format",
    "stream-json",
    "--input-format",
    "stream-json",
    "--verbose",
    "--include-partial-messages",
    "--permission-mode",
    permissionModeForPhase(phase),
    "--add-dir",
    String(workspace),
    "--add-dir",
    path.join(claudeConfigDir(env), "skills"),
    "--append-system-prompt",
    `${systemPromptForPhase(phase)}\n\n${workspaceDirective(workspace)}`,
    "--strict-mcp-config",
    "--settings",
    DISABLE_HOOKS_SETTINGS,
  ];
  if (claudeSessionExists(workspace, sessionId, env)) {
    args.push("--resume", String(sessionId));
  } else {
    args.push("--session-id", String(sessionId));
  }
  if (model) {
    args.push("--model", String(model));
  }
  // Reasoning effort is a product decision here, not a preference. Designing a
  // board means composing it, reading two rendered images and judging what is
  // wrong with them — under-thinking that produces a board that needs a repair
  // round, which costs far more than the tokens saved.
  if (effort) {
    args.push("--effort", String(effort));
  }
  return args;
}

/** Pipeline vars the circuit generator understands (contract §1); keys.env
 * supplies them, the real environment wins overall. */
const PIPELINE_ENV_VARS = new Set([
  "CIRCUIT_PARTS_ENGINE",
  "CIRCUIT_FAB",
  "CIRCUIT_TOOLCHAIN",
  "CIRCUIT_WALL_CLOCK_S",
]);

/** Parse ~/.autonomous-circuit/keys.env (KEY=value lines, # comments). The
 * paste-a-setting file: drop CIRCUIT_PARTS_ENGINE / CIRCUIT_FAB /
 * CIRCUIT_TOOLCHAIN / CIRCUIT_WALL_CLOCK_S here and every build — app or
 * terminal — picks them up. */
export function readKeysEnv(env = process.env) {
  const filePath = path.join(circuitHome(env), "keys.env");
  const out = {};
  let text = "";
  try {
    text = fs.readFileSync(filePath, "utf8");
  } catch {
    return out;
  }
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#") || !line.includes("=")) continue;
    const eq = line.indexOf("=");
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim().replace(/^["']|["']$/g, "");
    if (PIPELINE_ENV_VARS.has(key) && value) out[key] = value;
  }
  return out;
}

/** Env for the spawned child: inherited env + augmented PATH + the donor's
 * self-updater/background-task guards + pipeline settings (keys.env < real
 * environment). */
export function buildChildEnv(env = process.env) {
  const child = {
    ...readKeysEnv(env),
    ...env,
    PATH: augmentedPath(env),
    DISABLE_AUTOUPDATER: "1",
    CLAUDE_CODE_DISABLE_BACKGROUND_TASKS: "1",
  };
  return child;
}

const IMAGE_MEDIA_TYPES = new Map([
  ["png", "image/png"],
  ["jpg", "image/jpeg"],
  ["jpeg", "image/jpeg"],
  ["webp", "image/webp"],
  ["gif", "image/gif"],
]);

function imageMediaType(filePath) {
  const ext = path.extname(String(filePath)).slice(1).toLowerCase();
  return IMAGE_MEDIA_TYPES.get(ext) || "image/png";
}

/**
 * The stream-json user message piped to claude's stdin (`--input-format
 * stream-json`): one text block (the prompt) followed by one base64 image
 * block per readable reference image. Unreadable images are skipped.
 */
export function streamJsonInput(prompt, imagePaths = []) {
  const content = [{ type: "text", text: String(prompt) }];
  for (const imagePath of imagePaths) {
    try {
      const data = fs.readFileSync(imagePath).toString("base64");
      content.push({
        type: "image",
        source: {
          type: "base64",
          media_type: imageMediaType(imagePath),
          data,
        },
      });
    } catch {
      // skip unreadable image
    }
  }
  return `${JSON.stringify({ type: "user", message: { role: "user", content } })}\n`;
}

// ---------------------------------------------------------------------------
// Artifact snapshotter
// ---------------------------------------------------------------------------

/** Extensions watched per §2: `.tsx .json .svg .png .zip .csv .md`. */
export const WATCHED_EXTENSIONS = new Set([
  "tsx",
  "json",
  "svg",
  "png",
  "zip",
  "csv",
  "md",
]);

/** Snapshot every watched file under `workspace` → Map(relPath → mtimeMs).
 * Recursive; skips inputs/, .circuit/, .claude/, blocks/, node_modules. */
export function snapshotWorkspace(workspace) {
  const snapshot = new Map();
  const skip = skipDirNames();
  const stack = [workspace];
  while (stack.length) {
    const dir = stack.pop();
    let dirents;
    try {
      dirents = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of dirents) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (!skip.has(entry.name)) {
          stack.push(full);
        }
        continue;
      }
      if (!entry.isFile()) {
        continue;
      }
      const ext = path.extname(entry.name).slice(1).toLowerCase();
      if (!WATCHED_EXTENSIONS.has(ext)) {
        continue;
      }
      let stat;
      try {
        stat = fs.statSync(full);
      } catch {
        continue;
      }
      const rel = path.relative(workspace, full).split(path.sep).join("/");
      snapshot.set(rel, stat.mtimeMs);
    }
  }
  return snapshot;
}

/** Diff two snapshots → one artifact_changed per new file or per file whose
 * mtime moved forward by ≥ 1 second (whole-second comparison, donor rule). */
export function diffSnapshots(before, after, turnId) {
  const events = [];
  const paths = [...after.keys()].sort();
  for (const file of paths) {
    const afterSecs = Math.floor(after.get(file) / 1000);
    let reason = null;
    if (!before.has(file)) {
      reason = "new";
    } else if (afterSecs > Math.floor(before.get(file) / 1000)) {
      reason = "modified";
    }
    if (reason) {
      events.push({ kind: "artifact_changed", turnId, file, reason });
    }
  }
  return events;
}

// ---------------------------------------------------------------------------
// Stream-json → ChatEvent translation
// ---------------------------------------------------------------------------

export function newStreamState() {
  return {
    pendingTools: new Map(), // toolUseId -> tool name
    textDeltaStreamed: false, // per-message (reset by each assistant message)
    anyTextEmitted: false, // per-turn
    planProposed: false,
    questionsAsked: false,
    codexSessionId: "",
  };
}

/** A ```circuit-plan fenced block, or null.
 *
 * The tool-free path to a proposed plan. `claude -p` subprocesses are not
 * handed ExitPlanMode, so the fence is what actually carries plans in
 * production; ExitPlanMode stays supported for hosts that do provide it.
 * Mirrors the ```circuit-questions fence the client already renders.
 */
export function planFromFencedBlock(text) {
  if (typeof text !== "string" || !text) return null;
  const match = text.match(/```circuit-plan[ \t]*\r?\n([\s\S]*?)```/);
  if (!match) return null;
  const plan = match[1].trim();
  return plan || null;
}

/** Extract the plan markdown from an ExitPlanMode tool input: prefer the
 * inline `plan`; else read the `planFilePath` file the model just wrote. */
export function planFromExitPlanMode(input) {
  const inline = String(input?.plan || "");
  if (inline.trim()) {
    return inline;
  }
  const planFilePath = String(input?.planFilePath || "");
  if (planFilePath) {
    try {
      return fs.readFileSync(planFilePath, "utf8");
    } catch {
      return "";
    }
  }
  return "";
}

/** Build the synthetic ```circuit-questions fenced block from an
 * AskUserQuestion tool input (donor mechanism, renamed fence). Null when the
 * tool carried no questions. */
export function questionsFenceFromAskUserQuestion(input) {
  const questions = input?.questions;
  if (!Array.isArray(questions) || questions.length === 0) {
    return null;
  }
  const json = JSON.stringify({ questions });
  return `\n\n\`\`\`circuit-questions\n${json}\n\`\`\`\n`;
}

function toolResultText(content) {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .filter((item) => item && item.type === "text" && typeof item.text === "string")
      .map((item) => item.text)
      .join("\n");
  }
  return "";
}

/** Short human summary of a tool result — a line count ("3 lines"); undefined
 * when empty/non-text. Shared with the session rehydrator so live and
 * reloaded traces read the same. */
export function summarizeToolResult(content) {
  const trimmed = toolResultText(content).trimEnd();
  if (!trimmed) {
    return undefined;
  }
  const lines = Math.max(trimmed.split("\n").length, 1);
  return `${lines} line${lines === 1 ? "" : "s"}`;
}

function fromStreamEvent(obj, turnId, state) {
  const ev = obj.event;
  if (!ev || ev.type !== "content_block_delta" || !ev.delta) {
    return [];
  }
  const delta = ev.delta;
  if (delta.type === "text_delta" && typeof delta.text === "string" && delta.text) {
    state.textDeltaStreamed = true;
    state.anyTextEmitted = true;
    return [{ kind: "text_delta", turnId, text: delta.text }];
  }
  if (delta.type === "thinking_delta" && typeof delta.thinking === "string" && delta.thinking) {
    return [{ kind: "thinking_delta", turnId, text: delta.thinking }];
  }
  return [];
}

function fromAssistant(obj, turnId, state) {
  const out = [];
  // The consolidated assistant message arrives after its own text_delta
  // stream events; snapshot + reset the per-message flag up front.
  const textAlreadyStreamed = state.textDeltaStreamed;
  state.textDeltaStreamed = false;

  const content = obj?.message?.content;
  if (!Array.isArray(content)) {
    return out;
  }
  for (const block of content) {
    const type = block?.type;
    if (type === "text") {
      // Emit the final text only when --include-partial-messages did NOT
      // already stream it as deltas (would duplicate); when deltas are
      // unavailable this is the only place the response text exists.
      if (!textAlreadyStreamed && typeof block.text === "string" && block.text) {
        state.anyTextEmitted = true;
        out.push({ kind: "text_delta", turnId, text: block.text });
      }
      // A headless `claude -p` subprocess is not given ExitPlanMode, so a plan
      // that only ever arrives as a tool call never arrives at all — the turn
      // ends with a good plan in prose, no approve button, and a dead loop.
      // Verified 2026-08-10: the model searched for the tool, failed to find
      // it, wrote the plan to a file and said "say go". The fenced block is
      // the transport that does not depend on tool availability.
      if (typeof block.text === "string" && block.text) {
        const fenced = planFromFencedBlock(block.text);
        if (fenced && !state.planProposed) {
          state.planProposed = true;
          state.anyTextEmitted = true;
          out.push({ kind: "plan_proposed", turnId, plan: fenced });
        }
      }
      continue;
    }
    if (type !== "tool_use") {
      continue;
    }
    const name = String(block.name || "");
    if (name === "ExitPlanMode") {
      state.anyTextEmitted = true;
      state.planProposed = true;
      out.push({ kind: "plan_proposed", turnId, plan: planFromExitPlanMode(block.input) });
      continue;
    }
    if (name === "AskUserQuestion") {
      const fence = questionsFenceFromAskUserQuestion(block.input);
      if (fence) {
        state.anyTextEmitted = true;
        state.questionsAsked = true;
        out.push({ kind: "text_delta", turnId, text: fence });
      }
      continue;
    }
    const toolUseId = String(block.id || "");
    state.pendingTools.set(toolUseId, name);
    out.push({
      kind: "tool_use_start",
      turnId,
      tool: name,
      toolUseId,
      input: block.input ?? {},
    });
  }
  return out;
}

function fromUser(obj, turnId, state) {
  const out = [];
  const content = obj?.message?.content;
  if (!Array.isArray(content)) {
    return out;
  }
  for (const block of content) {
    if (block?.type !== "tool_result") {
      continue;
    }
    const toolUseId = String(block.tool_use_id || "");
    // Pair the result to its start by id. A miss means the start was
    // deliberately suppressed (intercepted ExitPlanMode / AskUserQuestion) —
    // drop it so no phantom tool row appears.
    if (!state.pendingTools.has(toolUseId)) {
      continue;
    }
    const tool = state.pendingTools.get(toolUseId);
    state.pendingTools.delete(toolUseId);
    const isError = block.is_error === true;
    const resultSummary = summarizeToolResult(block.content);
    out.push({
      kind: "tool_use_end",
      turnId,
      tool,
      toolUseId,
      ok: !isError,
      ...(resultSummary ? { resultSummary } : {}),
    });
  }
  return out;
}

function fromResult(obj, turnId, state) {
  // Last-resort fallback: a whole turn with no text surfaces the result
  // line's top-level `result` string so the bubble isn't empty.
  if (state.anyTextEmitted) {
    return [];
  }
  const text = typeof obj.result === "string" ? obj.result : "";
  if (!text) {
    return [];
  }
  state.anyTextEmitted = true;
  return [{ kind: "text_delta", turnId, text }];
}

/**
 * Parse one line of `claude -p --output-format stream-json` output into
 * zero-or-more ChatEvents (without projectId — the envelope is stamped at
 * emission). Non-JSON and decorative lines are skipped.
 */
export function parseStreamLine(line, turnId, state) {
  const trimmed = String(line || "").trim();
  if (!trimmed) {
    return [];
  }
  let obj;
  try {
    obj = JSON.parse(trimmed);
  } catch {
    return [];
  }
  switch (obj?.type) {
    case "stream_event":
      return fromStreamEvent(obj, turnId, state);
    case "assistant":
      return fromAssistant(obj, turnId, state);
    case "user":
      return fromUser(obj, turnId, state);
    case "result":
      return fromResult(obj, turnId, state);
    default:
      return [];
  }
}

/** Translate Codex CLI JSONL events into the same small event vocabulary as
 * Claude Code. Codex deliberately owns tool execution; the app only needs
 * the visible assistant text, tool activity, plan fence, and turn boundary. */
export function parseCodexLine(line, turnId, state) {
  const trimmed = String(line || "").trim();
  if (!trimmed) return [];
  let obj;
  try {
    obj = JSON.parse(trimmed);
  } catch {
    return [];
  }
  if (obj?.type === "thread.started") {
    state.codexSessionId = String(obj.thread_id || obj.threadId || "");
    return [];
  }
  const item = obj?.item;
  if (obj?.type === "item.completed" && item?.type === "agent_message") {
    const text = typeof item.text === "string" ? item.text : "";
    if (!text) return [];
    state.anyTextEmitted = true;
    const out = [{ kind: "text_delta", turnId, text }];
    const plan = planFromFencedBlock(text);
    if (plan && !state.planProposed) {
      state.planProposed = true;
      out.push({ kind: "plan_proposed", turnId, plan });
    }
    return out;
  }
  if (obj?.type === "item.started" && item?.type === "command_execution") {
    const toolUseId = String(item.id || crypto.randomUUID());
    state.pendingTools.set(toolUseId, "shell");
    return [{ kind: "tool_use_start", turnId, tool: "shell", toolUseId, input: { command: item.command || "" } }];
  }
  if (obj?.type === "item.completed" && item?.type === "command_execution") {
    const toolUseId = String(item.id || "");
    if (!state.pendingTools.has(toolUseId)) return [];
    state.pendingTools.delete(toolUseId);
    return [{ kind: "tool_use_end", turnId, tool: "shell", toolUseId, ok: item.status !== "failed" }];
  }
  const failure = codexFailureMessage(obj);
  if (failure) {
    state.anyTextEmitted = true;
    return [{ kind: "error", turnId, message: failure }];
  }
  return [];
}

/** The message of a Codex stream line that says the turn failed, else null.
 *
 * Two shapes: a bare `{"type":"error","message"}` and a `turn.failed` carrying
 * `error.message`. Astra run #5 (2026-09-10 14:18): the account hit its usage
 * limit mid-run. The build turn ended on it; the two review rounds then each
 * ran for five seconds and ended on the same message — which nothing read,
 * because a review round drains its stdout unparsed. The loop reported
 * `structure-unresolved` as if the agent had tried twice. A failed provider
 * is not a round.
 */
export function codexFailureMessage(obj) {
  if (!obj || typeof obj !== "object") return null;
  if (obj.type === "error") return String(obj.message || "Codex request failed");
  if (obj.type === "turn.failed") {
    const err = obj.error;
    const msg = typeof err === "string" ? err : err?.message;
    return String(msg || "Codex turn failed");
  }
  return null;
}

// ---------------------------------------------------------------------------
// Plan recovery from the persisted transcript
// ---------------------------------------------------------------------------

const MIN_PLAN_CHARS = 200;

/** Recover the plan from the transcript when ExitPlanMode arrived empty: the
 * most recent substantial assistant text block, else thinking block. */
export function recoverPlanFromTranscript(contents) {
  let bestText = "";
  let bestThinking = "";
  for (const line of String(contents || "").split("\n")) {
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    if (obj?.type !== "assistant") {
      continue;
    }
    const content = obj?.message?.content;
    if (!Array.isArray(content)) {
      continue;
    }
    for (const block of content) {
      if (block?.type === "text" && typeof block.text === "string") {
        if (block.text.trim().length >= MIN_PLAN_CHARS) {
          bestText = block.text;
        }
      } else if (block?.type === "thinking" && typeof block.thinking === "string") {
        if (block.thinking.trim().length >= MIN_PLAN_CHARS) {
          bestThinking = block.thinking;
        }
      }
    }
  }
  return bestText || bestThinking;
}

export function recoverPlanFromSession(workspace, sessionId, env = process.env) {
  try {
    const contents = fs.readFileSync(sessionJsonlPath(workspace, sessionId, env), "utf8");
    return recoverPlanFromTranscript(contents);
  } catch {
    return "";
  }
}

// ---------------------------------------------------------------------------
// Review loop (silent, best-effort, caps mirrored from the donor: 2/3/2)
// ---------------------------------------------------------------------------

/**
 * Is every board in the workspace orderable right now?
 *
 * Returns null when no sidecar carries a `fab` block — "we do not know" is a
 * third answer and must not collapse into false, or the very first round would
 * look like a regression.
 */
/** The board sidecars of a workspace: `boards/<stem>.board.json`, nothing else.
 *
 * Until 2026-09-10 every reader below walked the whole tree for
 * `*.board.json`. Astra run #5 (pomodoro-puck-run5) kept its own checkpoints
 * at `work/best-build-1/boards/main.board.json` — a copy of build 1, one
 * blocking finding, `fab.ready: false` — and the loop counted it as a board:
 * "1 blocking" on a workspace whose real board was fab-ready with none, two
 * review rounds spent on a backup, `structure-unresolved` in the chat. Run #4
 * only escaped because Astra had named its copies `.board.json.txt`. The
 * contract's tree (§ "Project layout") puts sidecars beside the source under
 * `boards/`; the generator writes them nowhere else. Anything deeper is a
 * copy someone keeps, and a copy is not a board.
 */
export function boardSidecarPaths(dir) {
  const boards = path.join(dir, "boards");
  let names;
  try {
    names = fs.readdirSync(boards, { withFileTypes: true });
  } catch {
    return [];
  }
  return names
    .filter((e) => e.isFile() && e.name.endsWith(".board.json"))
    .map((e) => path.join(boards, e.name))
    .sort();
}

export function workspaceFabReady(dir) {
  let seen = 0;
  let ready = 0;
  for (const full of boardSidecarPaths(dir)) {
    try {
      const json = JSON.parse(fs.readFileSync(full, "utf8"));
      if (json?.fab && typeof json.fab.ready === "boolean") {
        seen += 1;
        if (json.fab.ready) ready += 1;
      }
    } catch {
      // malformed sidecar — tells us nothing either way
    }
  }
  if (seen === 0) return null;
  return ready === seen;
}

/** Wall clock for one turn, per phase. The review loop has always had round
 * caps; the turn running it had none, and on 2026-09-08 a build turn ran
 * **20 hours** — rebuilding an unchanged source over and over, 33 errors
 * becoming 76, with nothing able to stop it. A turn that has not converged in
 * this long is not about to. Provider-neutral: both CLIs can loop. Override
 * with CIRCUIT_TURN_MAX_S (0 disables, for a deliberately long session). */
export function turnBudgetMs(phase, env = process.env) {
  const override = Number(env.CIRCUIT_TURN_MAX_S);
  if (Number.isFinite(override) && override >= 0) return override * 1000;
  // These are a backstop against a runaway, not a schedule. The first numbers
  // were guessed, and the first plan turn they met was a healthy one — 97
  // items deep and still working when it was cut at 15 minutes. Measured
  // since: a plan turn runs 9-17 minutes, and a build turn carries up to eight
  // generator runs at two to six minutes each before the model even thinks.
  // Set them where only a genuinely stuck turn can reach them.
  if (phase === PHASE.PLAN) return 40 * 60 * 1000;
  if (phase === PHASE.REVIEW) return 60 * 60 * 1000;
  // A build is 20-40 minutes and the review loop can ask for eight of them,
  // so three hours only ever bought about five attempts.
  return 5 * 60 * 60 * 1000; // implement: still 4x under the 20h that prompted this
}

export const MAX_STRUCTURE_ROUNDS = 2;
export const MAX_ELECTRICAL_ROUNDS = 3;
export const MAX_CRAFT_ROUNDS = 2;
//: The panel is the last stage of every board. The skill has said so in
//: capitals since it was written — "hand it to the panel — always", "not an
//: optional extra", "finishing a board without a panel verdict is a defect in
//: your turn" — and on 2026-08-14 a turn finished a fab-ready board and asked
//: the user whether to run it instead. Two turns before it had run it
//: unprompted. Same instruction, same wording, different choice: that is what
//: an instruction is, and it is why the mandatory part belongs here rather
//: than in prose the model may weigh.
//:
//: One round. The panel runs its own bounded loop internally; the driver's job
//: is only to guarantee it happens at all.
export const MAX_PANEL_ROUNDS = 1;

/** Does this workspace contain a board at all? True as soon as one
 * `*.board.json` sidecar exists (skip-list honored) — the sidecar is what
 * every review phase reads, so its absence means there is nothing to review. */
export function workspaceHasBoard(dir) {
  return boardSidecarPaths(dir).length > 0;
}

/** The rendered board images a craft round needs to look at: `_schematic.png`
 * and `_pcb.png` under every `*_review/` directory (skip-list honored).
 *
 * Claude reads these off disk with its own Read tool. Codex can open an image
 * by path too (ImageView), but only if it goes looking; attaching the current
 * renders pins the round to the board as it stands. Each craft round is its
 * own spawn, so round 2 sees round 1's output. */
export function reviewImagePaths(dir) {
  // `boards/<stem>_review/` only — where the generator writes renders — and
  // not a checkpoint's copy of them (see `boardSidecarPaths`).
  const boards = path.join(dir, "boards");
  let entries;
  try {
    entries = fs.readdirSync(boards, { withFileTypes: true });
  } catch {
    return [];
  }
  const out = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !entry.name.endsWith("_review")) continue;
    for (const name of ["_schematic.png", "_pcb.png"]) {
      const full = path.join(boards, entry.name, name);
      if (fs.existsSync(full)) out.push(full);
    }
  }
  // Schematic first, then PCB, and stable across rounds so a diff of the two
  // rounds' prompts is a diff of the board, not of directory order.
  return out.sort();
}

/** Read every `*.board.json` sidecar under `dir` (skip-list honored) and
 * collect `validation.warnings`. Best-effort; malformed sidecars skipped. */
export function collectBoardWarnings(dir) {
  const out = [];
  for (const full of boardSidecarPaths(dir)) {
    let json;
    try {
      json = JSON.parse(fs.readFileSync(full, "utf8"));
    } catch {
      continue;
    }
    const warnings = json?.validation?.warnings;
    if (!Array.isArray(warnings)) {
      continue;
    }
    for (const w of warnings) {
      out.push({
        part: String(w?.part ?? ""),
        kind: String(w?.kind ?? ""),
        detail: String(w?.detail ?? ""),
        severity: String(w?.severity ?? "warning"),
      });
    }
  }
  return out;
}

/** Severity routing is the driver's ONLY gate (contract §1): `error` blocks. */
export function isBlocking(warning) {
  return warning.severity === "error";
}

/** Phase-2 kinds (contract §2): the board builds but would not work or could
 * not be ordered. The severity gate stays the only *blocking* gate; this set
 * only routes non-blocking warnings into the electrical-function rounds. */
export const ELECTRICAL_KINDS = new Set([
  "functional",
  "power_budget",
  "part_not_orderable",
  "part_drift",
  "netlist_mismatch",
]);

export function isElectrical(warning) {
  return ELECTRICAL_KINDS.has(warning.kind);
}

function warningLines(warnings) {
  return warnings.map((w) => `- [${w.part}] ${w.kind}: ${w.detail}`).join("\n");
}

export function buildStructurePrompt(warnings) {
  if (!warnings.length) {
    return null;
  }
  return (
    "An automatic structural check found these blocking problems in the " +
    "board(s) you just built. Fix every one, then regenerate with the " +
    "circuitcode generator until the check is clean:\n\n" +
    `${warningLines(warnings)}\n`
  );
}

export function buildElectricalPrompt(warnings) {
  if (!warnings.length) {
    return null;
  }
  return (
    "An automatic ELECTRICAL-FUNCTION check found these problems in the " +
    "board(s) you just built — the board builds but would not work or could " +
    "not be ordered as-is (power budget, part orderability, part drift, " +
    "netlist parity). Fix the board source (re-lock parts with the " +
    "parts-book skill when a part must change), regenerate, and repeat " +
    "until every one is clear:\n\n" +
    `${warningLines(warnings)}\n`
  );
}

export function buildCraftPrompt(hints = []) {
  let body =
    "Structure and electrical function are clean. Do ONE craft verification " +
    "pass now. Rebuild the board so the generator refreshes " +
    "<stem>_review/_schematic.png and <stem>_review/_pcb.png, `Read` both " +
    "images, and check: net labels present and legible? decoupling " +
    "capacitors adjacent to their ICs? connectors oriented and placed at " +
    "the board edge? silkscreen legible (no refdes under parts)? mounting " +
    "holes where the enclosure needs them? Fix anything wrong in the TSX " +
    "source, regenerate, and re-check. If everything already reads right, " +
    "change nothing. Then stop.\n";
  if (hints.length) {
    body +=
      "\nThe deterministic checks flagged these advisories (verify before " +
      "acting — some may be intentional):\n" +
      `${warningLines(hints)}\n`;
  }
  return body;
}

/** Phase 4: the expert panel, which the skill calls the last stage of every
 * board. Asks first whether it already ran this turn, because a turn that did
 * the right thing on its own must not pay for it twice — a wasted panel is
 * about half an hour. */
export function buildPanelPrompt() {
  return (
    "This board is fab-ready, so the panel is the one stage still owed.\n\n" +
    "If you ALREADY ran the design-review skill during this turn and acted on " +
    "its must-fix notes, reply with exactly NO_CHANGES and stop — do not run " +
    "it twice.\n\n" +
    "Otherwise run the design-review skill on this project now. Route its " +
    "must-fix notes back into the board source, regenerate, and stop when the " +
    "panel is satisfied or when it reports nothing that has to change. Do not " +
    "ask whether to run it: it is not optional, and the person waiting has no " +
    "way to know it is owed.\n"
  );
}

// ---------------------------------------------------------------------------
// Subprocess driver
// ---------------------------------------------------------------------------

function spawnClaude(claudePath, args, { workspace, env }) {
  // Test stubs are node scripts; spawn them through the current node binary
  // so they need no chmod/shebang gymnastics on any platform.
  const viaNode = /\.(mjs|cjs|js)$/.test(claudePath);
  const bin = viaNode ? process.execPath : claudePath;
  const argv = viaNode ? [claudePath, ...args] : args;
  return spawn(bin, argv, {
    cwd: workspace,
    env: buildChildEnv(env),
    stdio: ["pipe", "pipe", "pipe"],
    // Own process group, so `killChild` can reach what the provider spawned.
    detached: OWN_PROCESS_GROUP,
  });
}

function spawnCodex(codexPath, args, { workspace, env }) {
  const viaNode = /\.(mjs|cjs|js)$/.test(codexPath);
  const bin = viaNode ? process.execPath : codexPath;
  const argv = viaNode ? [codexPath, ...args] : args;
  return spawn(bin, argv, {
    cwd: workspace,
    env: buildChildEnv(env),
    stdio: ["pipe", "pipe", "pipe"],
    detached: OWN_PROCESS_GROUP,
  });
}

/** Providers get their own process group everywhere that has them. */
const OWN_PROCESS_GROUP = process.platform !== "win32";

/** The build `.circuit/build-status.json` says is running, if the process
 * writing it is still alive — else null.
 *
 * A status file says `running` for as long as its writer lives, and for ever
 * after if the writer was killed mid-build, so the file alone cannot say
 * whether a build is in flight. The pipeline writes its pid and pgid
 * (`status.py`); a signal-0 probe of the pid settles it.
 */
export function liveBuild(workspace) {
  try {
    const status = JSON.parse(
      fs.readFileSync(path.join(workspace, ".circuit", "build-status.json"), "utf8"),
    );
    if (status?.state !== "running") return null;
    const pid = Number(status.pid);
    if (!Number.isInteger(pid) || pid <= 0) return null;
    try {
      process.kill(pid, 0);
    } catch {
      return null; // the writer is gone: a stale file, not a build
    }
    const pgid = Number(status.pgid);
    return {
      pid,
      pgid: Number.isInteger(pgid) && pgid > 0 ? pgid : null,
      runId: String(status.runId || ""),
      stage: String(status.stage || ""),
    };
  } catch {
    return null;
  }
}

/** Kill a generator the stopped provider left running. Returns whether one was.
 *
 * The build prompt tells the agent it may run the generator in the background
 * and check back, and Codex does; that build is not a child the provider's
 * death takes with it. Measured 2026-09-10 (pomodoro-puck run #4): a build
 * launched 13:01:58 survived the 13:05:38 chop and landed at 13:09:26 with 4
 * blocking findings — after the driver had recorded 1 and deleted both undo
 * copies. The board on disk and the verdict in the chat disagreed, and
 * nothing could put either right.
 */
export function killStrayBuild(workspace) {
  const live = liveBuild(workspace);
  if (!live) return false;
  const targets = [];
  if (live.pgid && OWN_PROCESS_GROUP) targets.push(-live.pgid);
  targets.push(live.pid);
  for (const target of targets) {
    try {
      process.kill(target, "SIGKILL");
    } catch {
      // already gone, or not a group leader — the direct pid follows
    }
  }
  log(`killed a build the stopped turn left running (pid ${live.pid}, stage ${live.stage || "?"})`);
  return true;
}

const BUILD_SETTLE_POLL_MS = 2_000;
const BUILD_SETTLE_MAX_MS = 20 * 60 * 1000;

/** Wait for a build still in flight to finish before the workspace is read.
 *
 * The verdict is the sidecar on disk; a build that is still writing it makes
 * every number read from it stale within minutes. Bounded: a build has its
 * own stage limits, and a reader that waits for ever is a hung turn.
 */
export async function awaitBuildSettled(workspace, {
  maxMs = BUILD_SETTLE_MAX_MS,
  pollMs = BUILD_SETTLE_POLL_MS,
} = {}) {
  let live = liveBuild(workspace);
  if (!live) return true;
  log(`a build is still running (pid ${live.pid}, stage ${live.stage || "?"}) — waiting for it before reading the board`);
  const started = Date.now();
  while (live && Date.now() - started < maxMs) {
    await new Promise((resolve) => setTimeout(resolve, pollMs));
    live = liveBuild(workspace);
  }
  if (live) log(`a build is still running after ${Math.round(maxMs / 60000)}min — reading the board anyway`);
  return !live;
}

function spawnProvider(provider, executable, args, options) {
  return provider === "codex"
    ? spawnCodex(executable, args, options)
    : spawnClaude(executable, args, options);
}

/** Kill the provider and everything it spawned.
 *
 * The provider is its own process-group leader (`detached` above), so the
 * negative pid reaches the generator chain it launched in the foreground —
 * python → tscircuit-cli → bun — which `child.kill` alone never did: it
 * signalled the provider and left the chain to finish and overwrite the board
 * after the turn had ended. A build the agent put in its own group is
 * `killStrayBuild`'s job.
 */
function killChild(child) {
  if (OWN_PROCESS_GROUP && child.pid) {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch {
      // group already gone
    }
  }
  try {
    child.kill("SIGKILL");
  } catch {
    // already gone
  }
}

function waitForExit(child) {
  return new Promise((resolve) => {
    if (child.exitCode !== null || child.signalCode) {
      resolve();
      return;
    }
    child.once("close", () => resolve());
    child.once("error", () => resolve());
  });
}

function shortId(id) {
  return String(id).slice(0, 8);
}

/**
 * Spawn one `claude -p` turn, stream + translate its output, snapshot/diff
 * the workspace, and forward ChatEvents to `onEvent`. Emits turn_start first
 * and turn_end last; failures emit `error` **then** `turn_end`. On abort,
 * kills the child and emits `error{message:"cancelled"}` + `turn_end`.
 *
 * Returns `{ proposedPlan, cancelled, sawOutput }` — `proposedPlan` is a
 * string when ExitPlanMode fired (possibly empty after recovery) and null
 * otherwise; the autopilot gate is plan-PRESENT, not plan-non-empty.
 */
export async function spawnTurn({
  workspace,
  sessionId,
  message,
  imagePaths = [],
  turnId,
  phase,
  provider = "claude",
  model = "",
  effort = "",
  onEvent,
  signal,
  env = process.env,
}) {
  onEvent({ kind: "turn_start", turnId, phase: phaseTag(phase) });

  const fail = (msg) => {
    onEvent({ kind: "error", turnId, message: msg });
    onEvent({ kind: "turn_end", turnId });
  };

  const executable = provider === "codex" ? resolveCodex(env) : resolveClaude(env);
  if (!executable) {
    fail(
      provider === "codex"
        ? "`codex` CLI not found. Install Codex and sign in."
        : "`claude` CLI not found. Install Claude Code (https://claude.ai/install).",
    );
    return { proposedPlan: null, cancelled: false, sawOutput: false };
  }

  try {
    fs.mkdirSync(workspace, { recursive: true });
  } catch (error) {
    fail(`failed to create workspace dir: ${error?.message || error}`);
    return { proposedPlan: null, cancelled: false, sawOutput: false };
  }

  const preSnapshot = snapshotWorkspace(workspace);
  const args = provider === "codex"
    ? buildCodexCommandArgs({ workspace, phase, model, effort, imagePaths, sessionId: codexSessionIdFor(workspace, env) })
    : buildCommandArgs({ workspace, phase, sessionId, model, effort, env });
  const resume = args.includes("--resume");
  log(
    `turn ${phase} start provider=${provider} session=${shortId(sessionId)} (${resume ? "resume" : "new"})` +
      `${model ? ` model=${model}` : ""}`,
  );

  let child;
  try {
    child = spawnProvider(provider, executable, args, { workspace, env });
  } catch (error) {
    fail(`failed to spawn claude: ${error?.message || error}`);
    return { proposedPlan: null, cancelled: false, sawOutput: false };
  }

  // Feed the stream-json user message (prompt + image blocks) and close stdin
  // so claude's `-p` reader sees EOF and starts the turn.
  child.stdin.on("error", () => {});
  child.stdin.end(provider === "codex" ? `${systemPromptForPhase(phase)}\n\n${workspaceDirective(workspace)}\n\n${message}\n` : streamJsonInput(message, imagePaths));

  // Drain stderr concurrently: an undrained pipe deadlocks the child, and a
  // fast failure (bad session id, auth, missing node) prints its reason here
  // with nothing on stdout.
  let stderrBuf = "";
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    if (debugEnabled()) {
      process.stderr.write(`[circuit:claude:err] ${chunk}`);
    }
    if (stderrBuf.length < 8192) {
      stderrBuf += chunk;
    }
  });

  const state = newStreamState();
  let cancelled = false;
  let sawOutput = false;
  let proposedPlan = null;
  let artifactsChanged = false;
  let runningSnapshot = preSnapshot;

  let timedOut = false;
  const onAbort = () => {
    cancelled = true;
    killChild(child);
  };
  if (signal) {
    if (signal.aborted) {
      onAbort();
    } else {
      signal.addEventListener("abort", onAbort, { once: true });
    }
  }
  const budgetMs = turnBudgetMs(phase, env);
  const budgetTimer = budgetMs
    ? setTimeout(() => {
        timedOut = true;
        log(`turn ${phase} exceeded its ${Math.round(budgetMs / 60000)}min budget — stopping`);
        onAbort();
      }, budgetMs)
    : null;
  if (budgetTimer?.unref) budgetTimer.unref();

  // The implement turn's own ratchet. Inside this one child the agent
  // rebuilds as often as it likes for up to five hours, and the review
  // ratchet sees none of it — it guards rounds the DRIVER runs, which start
  // only after this child exits. Measured 2026-09-10 (pomodoro-puck, Astra
  // run #3): one implement turn walked its own rebuilds 3 → 4 → 2 → 8 with no
  // round boundary for anything to act on. Keep a copy of the best settled
  // build as it goes; if the clock or the user stops the turn while the board
  // is worse than that copy, hand the copy back (`shouldRestoreBestBuild`
  // says why a turn that ends on its own keeps what it ended on).
  let best = null; // { count, dir }
  let lastSettledRunId = null;
  const bestWatcher = phase === PHASE.IMPLEMENT
    ? setInterval(() => {
        const runId = settledBuildRunId(workspace);
        if (!runId || runId === lastSettledRunId) return;
        if (buildIsStale(workspace)) return; // source already moved on; wait for the next build
        lastSettledRunId = runId;
        let count;
        try {
          count = collectBoardWarnings(workspace).filter(isBlocking).length;
        } catch {
          return;
        }
        if (best && !(count < best.count)) return;
        const dir = snapshotForUndo(workspace, BEST_BUILD_DIR);
        if (dir) {
          best = { count, dir };
          log(`turn implement: best rebuild so far has ${count} blocking finding(s) — kept a copy`);
        }
      }, REVIEW_SIDECAR_POLL_MS)
    : null;
  if (bestWatcher?.unref) bestWatcher.unref();

  const rl = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  try {
    for await (const line of rl) {
      if (cancelled) {
        break;
      }
      if (debugEnabled()) {
        process.stderr.write(`[circuit:${provider}:out] ${line}\n`);
      }
      const events = provider === "codex"
        ? parseCodexLine(line, turnId, state)
        : parseStreamLine(line, turnId, state);
      const stopTurn = state.planProposed || state.questionsAsked;
      let toolJustEnded = false;
      for (let event of events) {
        sawOutput = true;
        if (event.kind === "tool_use_end") {
          toolJustEnded = true;
        }
        if (event.kind === "plan_proposed") {
          // Empty plan (model exited plan mode without restating it, typical
          // on resume, no planFilePath either): recover from the transcript.
          if (!event.plan.trim()) {
            event = {
              ...event,
              plan: recoverPlanFromSession(workspace, sessionId, env),
            };
          }
          proposedPlan = event.plan;
        }
        onEvent(event);
      }
      // Incremental artifact emission: when a tool just finished, diff against
      // the running snapshot so artifacts materialize mid-build; advancing the
      // snapshot keeps the final diff from re-emitting the same files.
      if (toolJustEnded) {
        const nowSnapshot = snapshotWorkspace(workspace);
        const incremental = diffSnapshots(runningSnapshot, nowSnapshot, turnId);
        if (incremental.length) {
          artifactsChanged = true;
          for (const event of incremental) {
            onEvent(event);
          }
        }
        runningSnapshot = nowSnapshot;
      }
      // ExitPlanMode (plan ready) or AskUserQuestion (preference fork) ends
      // the turn deterministically — kill rather than waiting for `-p` EOF.
      if (stopTurn) {
        killChild(child);
        break;
      }
    }
  } catch {
    // stream torn down (kill/cancel) — fall through to the wait
  } finally {
    rl.close();
  }

  await waitForExit(child);
  if (budgetTimer) clearTimeout(budgetTimer);
  if (bestWatcher) clearInterval(bestWatcher);
  // The board is read below; make sure nothing is still writing it. A
  // stopped turn's stray build is killed, a finished turn's is waited for.
  if (cancelled) {
    killStrayBuild(workspace);
  } else {
    await awaitBuildSettled(workspace);
  }
  if (timedOut) {
    onEvent({
      kind: "error",
      turnId,
      message:
        `The ${phase} turn ran past its ${Math.round(budgetMs / 60000)} minute budget and was stopped. ` +
        "Whatever it had written is still here; send another message to carry on.",
    });
  }
  if (provider === "codex" && state.codexSessionId) {
    rememberCodexSession(workspace, state.codexSessionId, env);
  }
  if (signal) {
    signal.removeEventListener("abort", onAbort);
  }

  // Silent failure: claude exited without emitting any stream-json.
  if (!cancelled && !sawOutput) {
    const detail = stderrBuf.trim() || `${provider} exited without output (code ${child.exitCode})`;
    onEvent({ kind: "error", turnId, message: `${provider} produced no response: ${detail}` });
  }

  // Post-turn workspace diff — even when cancelled (the user still wants to
  // see artifacts produced before the cancel).
  const postSnapshot = snapshotWorkspace(workspace);
  const diffEvents = diffSnapshots(runningSnapshot, postSnapshot, turnId);
  if (diffEvents.length) {
    artifactsChanged = true;
  }
  for (const event of diffEvents) {
    onEvent(event);
  }

  // Hand a stopped implement turn its best rebuild back (watcher above).
  if (best) {
    let finalBlocking = null;
    try {
      finalBlocking = collectBoardWarnings(workspace).filter(isBlocking).length;
    } catch { /* unreadable → never called worse */ }
    if (shouldRestoreBestBuild({ stoppedEarly: cancelled, bestBlocking: best.count, finalBlocking })) {
      const restored = restoreFromUndo(workspace, best.dir);
      log(
        `turn implement stopped at ${finalBlocking} blocking finding(s); its best rebuild had ` +
          `${best.count} — ${restored ? "restored" : "COULD NOT RESTORE"}`,
      );
      onEvent({
        kind: "text_delta",
        turnId,
        text: restored
          ? `\n\n_The turn was stopped while the board had ${finalBlocking} findings that stop it being made; ` +
            `its best rebuild had ${best.count}, so I put that one back._`
          : `\n\n_The turn was stopped while the board had ${finalBlocking} findings that stop it being made; ` +
            `its best rebuild had ${best.count}, and I could not put that one back._`,
      });
      if (restored) {
        artifactsChanged = true;
        for (const event of diffSnapshots(postSnapshot, snapshotWorkspace(workspace), turnId)) onEvent(event);
      }
    } else if (finalBlocking !== null && finalBlocking > best.count) {
      log(
        `turn implement ended on its own at ${finalBlocking} blocking finding(s); its best rebuild had ` +
          `${best.count} — kept as the agent left it`,
      );
    }
    fs.rmSync(best.dir, { recursive: true, force: true });
  }

  // Automatic post-build review, silent, inside this build turn. `artifactsChanged`
  // alone is too loose a gate: a turn that stops before writing board source
  // still touches the workspace (a blocked SOURCE step leaves records under
  // sourcing/), and the review loop then spends rounds reviewing a board that
  // does not exist. Every phase of it reads `*.board.json`, so require one.
  if (
    phase === PHASE.IMPLEMENT &&
    !cancelled &&
    sawOutput &&
    artifactsChanged &&
    workspaceHasBoard(workspace)
  ) {
    await runReviewFixLoop({
      provider,
      executable,
      workspace,
      sessionId,
      turnId,
      model,
      onEvent,
      signal,
      env,
    });
  }

  if (cancelled) {
    onEvent({ kind: "error", turnId, message: "cancelled" });
  }
  onEvent({ kind: "turn_end", turnId });
  log(`turn ${phase} end session=${shortId(sessionId)}${cancelled ? " (cancelled)" : ""}`);
  return { proposedPlan, cancelled, sawOutput };
}

/** One silent review round: spawn a Review-phase child, drain its stdout to
 * EOF WITHOUT parsing (review chatter never reaches the user), then diff the
 * workspace and surface changed artifacts. Returns whether files changed. */
async function runReviewRound({
  provider = "claude",
  executable,
  workspace,
  sessionId,
  turnId,
  model,
  effort = "",
  prompt,
  imagePaths = [],
  onEvent,
  signal,
  env,
}) {
  const pre = snapshotWorkspace(workspace);
  const args = provider === "codex"
    ? buildCodexCommandArgs({ workspace, phase: PHASE.REVIEW, model, effort, imagePaths, sessionId: codexSessionIdFor(workspace, env) })
    : buildCommandArgs({ workspace, phase: PHASE.REVIEW, sessionId, model, effort, env });
  let child;
  try {
    child = spawnProvider(provider, executable, args, { workspace, env });
  } catch {
    return false; // best-effort: a build that can't be reviewed just ends
  }
  child.stdin.on("error", () => {});
  // Attaching beats leaving it to chance. Codex can open an image by path (its
  // ImageView tool), but nothing guarantees it looks at the right renders at
  // the right moment, and the shared craft prompt says "rebuild, then Read
  // both images" — a sequence it cannot follow for images made in this turn.
  const attached = provider === "codex" && imagePaths.length
    ? "\n\nThe board's current renders are ATTACHED to this message — look at " +
      "them directly rather than hunting for the files. They show the board as " +
      "it stands right now. Fix what reads wrong in the TSX source " +
      "and regenerate; the next round attaches the refreshed renders.\n"
    : "";
  child.stdin.end(
    provider === "codex"
      ? `${REVIEW_SYSTEM_PROMPT}\n\n${workspaceDirective(workspace)}\n\n${prompt}${attached}\n`
      : streamJsonInput(prompt),
  );
  child.stderr.resume();

  const onAbort = () => killChild(child);
  if (signal) {
    if (signal.aborted) {
      onAbort();
    } else {
      signal.addEventListener("abort", onAbort, { once: true });
    }
  }
  // The round's own wall clock. The build child arms `turnBudgetMs` and
  // clears it when it exits; the review loop runs AFTER that, so until
  // 2026-09-09 a review round had no clock at all — only the round caps
  // bounded it, and one round is one resumed session free to rebuild as often
  // as it likes. Measured that day: structure round 1 ran 2h06 on a single
  // board before the turn was stopped by hand.
  const budgetMs = turnBudgetMs(PHASE.REVIEW, env);
  let timedOut = false;
  const budgetTimer = budgetMs
    ? setTimeout(() => {
        timedOut = true;
        log(`review round exceeded its ${Math.round(budgetMs / 60000)}min budget — stopping it`);
        onAbort();
      }, budgetMs)
    : null;
  if (budgetTimer?.unref) budgetTimer.unref();
  // The round is silent by contract, but the sidecar is not: every rebuild
  // inside the round rewrites it. Watching it is the only way the person
  // waiting sees 14 → 3 → 78 rather than one status line for two hours.
  let lastBlocking = null;
  try {
    lastBlocking = collectBoardWarnings(workspace).filter(isBlocking).length;
  } catch { /* no sidecar yet */ }
  const watcher = setInterval(() => {
    let now;
    try {
      now = collectBoardWarnings(workspace).filter(isBlocking).length;
    } catch {
      return;
    }
    if (now === lastBlocking) return;
    const was = lastBlocking;
    lastBlocking = now;
    onEvent?.({
      kind: "text_delta",
      turnId,
      text: `\n\n_Rebuilt: ${now} finding(s) that stop the board being made` +
        (was === null ? "" : ` (was ${was})`) + "._",
    });
  }, REVIEW_SIDECAR_POLL_MS);
  if (watcher?.unref) watcher.unref();
  // Drain stdout so a full pipe cannot deadlock the child. Review chatter
  // never reaches the user, but a line that says the provider FAILED does:
  // run #5 burned both structure rounds in five seconds each on "You've hit
  // your usage limit" and reported the board unresolved as if it had tried.
  let failure = null;
  const reviewLines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  reviewLines.on("line", (line) => {
    if (debugEnabled()) process.stderr.write(`[circuit:${provider}:review] ${line}\n`);
    if (failure) return;
    let obj;
    try {
      obj = JSON.parse(String(line || "").trim());
    } catch {
      return;
    }
    failure = codexFailureMessage(obj);
  });
  await waitForExit(child);
  reviewLines.close();
  clearInterval(watcher);
  if (budgetTimer) clearTimeout(budgetTimer);
  if (signal) {
    signal.removeEventListener("abort", onAbort);
  }
  // The loop reads the sidecar the moment this returns. A chopped round's
  // build must not land after that read (run #4: it did, 1 became 4 on disk
  // with both undo copies already gone); a finished round's must be complete.
  if (timedOut || signal?.aborted) {
    killStrayBuild(workspace);
  } else {
    await awaitBuildSettled(workspace);
  }
  if (timedOut) {
    onEvent?.({
      kind: "text_delta",
      turnId,
      text: `\n\n_The review round ran past its ${Math.round(budgetMs / 60000)} minute budget and was stopped; the board stands as its last rebuild left it._`,
    });
  }

  const post = snapshotWorkspace(workspace);
  const diff = diffSnapshots(pre, post, turnId);
  for (const event of diff) {
    onEvent(event); // only artifact diffs surface from a review round
  }
  if (failure) log(`review round failed before it could work: ${failure}`);
  return { changed: diff.length > 0, failure };
}

function emitUnresolvedNote(turnId, label, remaining, onEvent) {
  const parts = [...new Set(remaining.map((w) => w.part).filter(Boolean))].sort();
  onEvent({
    kind: "text_delta",
    turnId,
    text:
      `\n\n_Note: automatic ${label} review left ${remaining.length} issue(s) ` +
      `unresolved (parts: ${parts.length ? parts.join(", ") : "board"}). ` +
      "You may want to inspect those parts._",
  });
}

//: Build litter and things no review round edits. `blocks/` is deliberately
//: NOT here: a round may edit a vendored block, and one board did.
const REVIEW_SNAPSHOT_SKIP = new Set([".circuit", "node_modules", ".git", "__pycache__"]);

const REVIEW_SNAPSHOT_DIR = ".circuit/review-undo";

/** Where an implement turn keeps its best settled rebuild (see the watcher in
 * `spawnTurn`). A sibling of the review undo copy, never the same directory:
 * the review loop runs after the implement child and must not clobber it. */
export const BEST_BUILD_DIR = ".circuit/best-build";

/** Copy the workspace aside so a round that breaks the board can be undone.
 *
 * Lives under `.circuit/`, which the artifact snapshotter already ignores, so
 * taking a backup never looks like the board changed.
 */
export function snapshotForUndo(workspace, relDir = REVIEW_SNAPSHOT_DIR) {
  const dest = path.join(workspace, relDir);
  try {
    fs.rmSync(dest, { recursive: true, force: true });
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(workspace)) {
      if (REVIEW_SNAPSHOT_SKIP.has(entry)) continue;
      fs.cpSync(path.join(workspace, entry), path.join(dest, entry), {
        recursive: true,
      });
    }
    return dest;
  } catch (error) {
    log(`review: could not snapshot the workspace (${error?.message || error})`);
    return null;
  }
}

/** Put the board back exactly as it was before the round.
 *
 * Restores artifacts as well as source. They were produced from the source
 * being restored, so putting back one without the other would leave a sidecar
 * describing a board that no longer exists — the failure this repo names as
 * "one gate, two surfaces, opposite answers".
 *
 * Files the round created and the snapshot does not have are removed, or the
 * undo would leave its own litter behind.
 */
export function restoreFromUndo(workspace, snapshotPath) {
  if (!snapshotPath) return false;
  try {
    const kept = new Set(fs.readdirSync(snapshotPath));
    for (const entry of fs.readdirSync(workspace)) {
      if (REVIEW_SNAPSHOT_SKIP.has(entry) || kept.has(entry)) continue;
      fs.rmSync(path.join(workspace, entry), { recursive: true, force: true });
    }
    for (const entry of kept) {
      const target = path.join(workspace, entry);
      fs.rmSync(target, { recursive: true, force: true });
      fs.cpSync(path.join(snapshotPath, entry), target, { recursive: true });
    }
    return true;
  } catch (error) {
    log(`review: could not undo the round (${error?.message || error})`);
    return false;
  }
}

/** The run id of the build that last SETTLED in this workspace, or null.
 *
 * The pipeline writes `.circuit/build-status.json` on every stage change and
 * marks it `done` only after the sidecar AND the circuit.json have landed
 * (`progress.finish` is the last thing `build_board` does). A `running` file
 * means the artifacts on disk are mid-flight and must not be copied; a fresh
 * run id means a build the caller has not looked at yet.
 */
export function settledBuildRunId(workspace) {
  try {
    const status = JSON.parse(
      fs.readFileSync(path.join(workspace, ".circuit", "build-status.json"), "utf8"),
    );
    if (status?.state !== "done") return null;
    return typeof status.runId === "string" && status.runId ? status.runId : null;
  } catch {
    return null;
  }
}

const SOURCE_ROOTS = ["boards", "blocks", "product.json", "parts.json"];

/** True when any board source is newer than the newest sidecar.
 *
 * The best-build watcher copies a workspace up to a poll interval after a
 * build settles, and by then the agent may already be editing the TSX for
 * its next attempt. Copying that would pair a sidecar with source it did not
 * come from — "one gate, two surfaces, opposite answers". Skip the copy; the
 * next settled build is a candidate again.
 */
export function buildIsStale(workspace) {
  let newestSidecar = -Infinity;
  let boards;
  try {
    boards = fs.readdirSync(path.join(workspace, "boards"));
  } catch {
    return true;
  }
  for (const name of boards) {
    if (!name.endsWith(".board.json")) continue;
    try {
      newestSidecar = Math.max(newestSidecar, fs.statSync(path.join(workspace, "boards", name)).mtimeMs);
    } catch { /* unreadable → not a sidecar we can trust */ }
  }
  if (!Number.isFinite(newestSidecar)) return true;
  // Source is the TSX/TS under boards/ and blocks/ plus the two root JSON
  // files. `boards/` also holds `<stem>_fab/` and `<stem>_review/`, written
  // AFTER the sidecar by design — those are outputs, never a reason to call
  // the build stale.
  const isSource = (full) =>
    /\.tsx?$/.test(full) ||
    full === path.join(workspace, "product.json") ||
    full === path.join(workspace, "parts.json");
  const stack = SOURCE_ROOTS.map((rel) => path.join(workspace, rel));
  while (stack.length) {
    const full = stack.pop();
    let stat;
    try {
      stat = fs.statSync(full);
    } catch {
      continue;
    }
    if (stat.isDirectory()) {
      let names;
      try {
        names = fs.readdirSync(full);
      } catch {
        continue;
      }
      for (const name of names) stack.push(path.join(full, name));
      continue;
    }
    if (isSource(full) && stat.mtimeMs > newestSidecar) return true;
  }
  return false;
}

/** Say which review phase is running, and how far through it is.
 *
 * The loop used to be silent to the user by design — it only wrote to the
 * server log. That is fine when it takes a minute; it is not fine at ninety.
 * A person watching a board build for an hour and a half with no phase, no
 * round count and no idea a panel is even owed cannot tell working from hung,
 * and the honest reading is the pessimistic one.
 *
 * Uses `text_delta` like :func:`emitUnresolvedNote` rather than a new event
 * kind: the ChatEvent union is name-coupled to the client (contract §3), and a
 * status line is not worth an edit on both sides of it.
 */
/** How often a review round re-reads the sidecar to report a rebuild. */
export const REVIEW_SIDECAR_POLL_MS = 15_000;

/** The per-round ratchet, as one pure rule so the test can pin it.
 *
 * "Worse" is strictly more blocking findings than the round started with. A
 * round that ends equal is kept: it may have traded one finding for another
 * on the way somewhere, and the next round sees the same board either way.
 * A round that ends with fewer is progress. `null` on either side means the
 * count could not be read (no sidecar), and an unreadable board is never
 * called worse — that would undo a round for a reason nobody can see.
 */
export function roundMadeItWorse(blockingBefore, blockingAfter) {
  if (!Number.isFinite(blockingBefore) || !Number.isFinite(blockingAfter)) return false;
  return blockingAfter > blockingBefore;
}

/** How many rounds a phase may run once `undone` of them were put back.
 *
 * An undone round leaves the board exactly where it started, so the slot it
 * used bought nothing — and under the round clock that slot can be a full
 * hour of work thrown away. Measured 2026-09-09 (pomodoro-puck, Astra run
 * #3): structure round 1 was chopped at 60 min and undone (5→8), round 2 then
 * took 8 minutes to go 5→3, and the 2-round cap ended the run there with the
 * board still not buildable. One spare slot per phase, not one per undo: a
 * phase that keeps making things worse must still end.
 */
export function roundCapAfterUndo(cap, undone) {
  const n = Number.isFinite(undone) && undone > 0 ? Math.floor(undone) : 0;
  return cap + Math.min(n, 1);
}

/** Whether an implement turn that was STOPPED should hand back its best
 * settled rebuild instead of whatever it was in the middle of.
 *
 * Only a stopped turn — the clock or the user. A turn that ends on its own
 * keeps what it chose to end on: it may have just implemented "make the board
 * smaller", which reads as a regression to a blocking-count poll and is not
 * one. `null` on either side means a count could not be read, and a board
 * nobody can read is never called worse.
 */
export function shouldRestoreBestBuild({ stoppedEarly, bestBlocking, finalBlocking }) {
  if (!stoppedEarly) return false;
  return roundMadeItWorse(bestBlocking, finalBlocking);
}

export function emitPhaseNote(turnId, onEvent, { phase, round, rounds, detail }) {
  const of = rounds > 1 ? ` ${round}/${rounds}` : "";
  onEvent?.({
    kind: "text_delta",
    turnId,
    text: `\n\n_Checking: ${phase}${of}${detail ? ` — ${detail}` : ""}. `
      + "Each round rebuilds the board, so this takes a few minutes._",
  });
}

/**
 * The silent 3-phase post-build review loop (contract §2, donor caps 2/3/2):
 *
 * 1. **Structure** (≤2): while any `severity:"error"` warning remains in the
 *    `*.board.json` sidecars, resume the session with the warning list.
 * 2. **Electrical function** (≤3): while any warning with
 *    `kind ∈ ELECTRICAL_KINDS` remains, fix power budget / orderability /
 *    part drift / netlist parity.
 * 3. **Craft** (≤2, ALWAYS runs once): rebuild, Read `_schematic.png` +
 *    `_pcb.png`, fix what reads wrong; break when a round changes no files.
 *    Non-blocking leftovers seed the prompt as hints.
 *
 * Best-effort throughout — never fails the build turn.
 */
export async function runReviewFixLoop({
  provider = "claude",
  executable,
  workspace,
  sessionId,
  turnId,
  model = "",
  onEvent,
  signal,
  env = process.env,
}) {
  let changed = false;
  const aborted = () => Boolean(signal?.aborted);

  // Keep the warning count each round already computes. Without this the loop
  // converges silently and the app can only ever describe the board's current
  // state, never the fact that it started at six blockers and is now at one.
  // `fabReady` stays null: the fab gate has not re-run mid-loop, and writing
  // false here would draw failures that never happened.
  const snapshot = (phase, roundNo, warnings) =>
    recordRevision(workspace, {
      turnId,
      phase,
      round: roundNo,
      counts: countWarnings(warnings, { isBlocking, isElectrical }),
      fabReady: null,
    });

  // The ratchet. The agent eval caught a board that was fab-ready on its first
  // build and was NOT fab-ready five repair rounds later: the loop took a
  // finished board and broke it, chasing cosmetic findings. The router already
  // refuses a retry that is not strictly better (stage 0b); the board-level
  // loop had no such rule, so nothing stopped it walking downhill.
  //
  // A round may leave the board no worse than it found it. The moment one
  // turns orderable into not-orderable, the loop stops — one bad round instead
  // of five — and says so out loud rather than quietly handing back a board
  // that used to be shippable.
  let regressed = false;
  // Set by `round()` when the provider itself failed (usage limit, auth, a
  // dead binary). Every phase loop stops on it.
  let providerFailed = null;
  // Set by `round()` when the blocking ratchet put the board back. The phase
  // loops read it to give the phase one spare round (`roundCapAfterUndo`).
  let lastRoundUndone = false;
  const round = async (prompt, imagePaths = []) => {
    lastRoundUndone = false;
    const readyBefore = workspaceFabReady(workspace);
    // Always worth the copy now. The first ratchet only guarded a board that
    // was already orderable; a board that was NOT orderable could be walked
    // from 2 blocking findings to 78 and left there (2026-09-09, pomodoro-puck,
    // structure round 1). Count the blockers going in as well.
    const blockingBefore = collectBoardWarnings(workspace).filter(isBlocking).length;
    const undo = snapshotForUndo(workspace);
    const outcome = await runReviewRound({
      provider,
      executable,
      workspace,
      sessionId,
      turnId,
      model,
      prompt,
      imagePaths,
      onEvent,
      signal,
      env,
    });
    let didChange = outcome.changed;
    if (outcome.failure) {
      // The provider, not the board. Say so once and stop the loop: a round
      // that could not run is not a round, and the caps are for rounds.
      providerFailed = outcome.failure;
      onEvent?.({
        kind: "assistant_message",
        turnId,
        text:
          "_I stopped the automatic review: the model could not run it " +
          `(${outcome.failure}). The board stands as the build left it; ` +
          "send another message once that is resolved to carry on._",
      });
    }
    const blockingAfter = collectBoardWarnings(workspace).filter(isBlocking).length;
    if (readyBefore !== true && didChange && roundMadeItWorse(blockingBefore, blockingAfter)) {
      // Not the orderable→un-orderable case below (that one stops the loop);
      // this board was not orderable to begin with. Put the round back and let
      // the next round try again from the better board, not from the wreck.
      const postRound = snapshotWorkspace(workspace);
      const undone = restoreFromUndo(workspace, undo);
      log(
        `review: a round went from ${blockingBefore} to ${blockingAfter} blocking finding(s) — ${
          undone ? "undone" : "COULD NOT UNDO"
        }`,
      );
      onEvent?.({
        kind: "text_delta",
        turnId,
        text: undone
          ? `\n\n_That round made things worse (${blockingBefore} → ${blockingAfter} findings that stop the board), so I put the board back to ${blockingBefore}._`
          : `\n\n_That round made things worse (${blockingBefore} → ${blockingAfter} findings that stop the board) and I could not put it back._`,
      });
      if (undone) {
        // The workspace is back where the round started; the artifact events
        // the round emitted described files that no longer exist, so tell the
        // viewer about the ones that just changed back.
        for (const event of diffSnapshots(postRound, snapshotWorkspace(workspace), turnId)) onEvent(event);
        didChange = false;
        lastRoundUndone = true;
      }
    }
    if (readyBefore === true && workspaceFabReady(workspace) === false) {
      regressed = true;
      // Stopping was never enough. Twice this loop broke an orderable board
      // and only the model's own diligence put it back; the guard itself left
      // the damage where it fell. Undo it, then stop.
      const undone = restoreFromUndo(workspace, undo);
      log(
        `review: a round made an orderable board un-orderable — ${
          undone ? "undone, stopping" : "COULD NOT UNDO, stopping"
        }`,
      );
      onEvent?.({
        kind: "assistant_message",
        turnId,
        text: undone
          ? "_I stopped the automatic review: the last change made a board " +
            "that could be ordered no longer orderable, so I put the board " +
            "back the way it was. It is orderable again, and in your hands " +
            "rather than being polished further._"
          : "_I stopped the automatic review: the last change made a board " +
            "that could be ordered no longer orderable, and I could not undo " +
            "it. The board needs a look before it is sent anywhere._",
      });
    }
    if (undo) fs.rmSync(undo, { recursive: true, force: true });
    return didChange;
  };

  // Phase 1 — structure (blocking = severity "error"). An undone round does
  // not use up a slot (once per phase — see `roundCapAfterUndo`).
  let structureUndone = 0;
  for (let i = 0; i < roundCapAfterUndo(MAX_STRUCTURE_ROUNDS, structureUndone); i += 1) {
    if (aborted() || regressed || providerFailed) return changed;
    const all = collectBoardWarnings(workspace);
    const blocking = all.filter(isBlocking);
    const prompt = buildStructurePrompt(blocking);
    if (!prompt) break;
    snapshot("structure", i + 1, all);
    log(`review structure round ${i + 1}: ${blocking.length} blocking warning(s)`);
    emitPhaseNote(turnId, onEvent, {
      phase: "structure",
      round: i + 1,
      rounds: roundCapAfterUndo(MAX_STRUCTURE_ROUNDS, structureUndone),
      detail: `${blocking.length} finding(s) that stop the board being made`,
    });
    changed = (await round(prompt)) || changed;
    if (lastRoundUndone) structureUndone += 1;
  }
  if (aborted() || regressed || providerFailed) return changed;
  const afterStructure = collectBoardWarnings(workspace);
  const structureRemaining = afterStructure.filter(isBlocking);
  if (structureRemaining.length) {
    snapshot("structure-unresolved", 0, afterStructure);
    emitUnresolvedNote(turnId, "structure", structureRemaining, onEvent);
    return changed;
  }

  // Phase 2 — electrical function (contract kind set). Same spare slot rule.
  let electricalUndone = 0;
  for (let i = 0; i < roundCapAfterUndo(MAX_ELECTRICAL_ROUNDS, electricalUndone); i += 1) {
    if (aborted() || regressed || providerFailed) return changed;
    const all = collectBoardWarnings(workspace);
    const electrical = all.filter(isElectrical);
    const prompt = buildElectricalPrompt(electrical);
    if (!prompt) break;
    snapshot("electrical", i + 1, all);
    log(`review electrical round ${i + 1}: ${electrical.length} electrical warning(s)`);
    emitPhaseNote(turnId, onEvent, {
      phase: "electrical function",
      round: i + 1,
      rounds: roundCapAfterUndo(MAX_ELECTRICAL_ROUNDS, electricalUndone),
      detail: `${electrical.length} thing(s) that would stop it working`,
    });
    changed = (await round(prompt)) || changed;
    if (lastRoundUndone) electricalUndone += 1;
  }
  if (aborted() || regressed || providerFailed) return changed;
  const afterElectrical = collectBoardWarnings(workspace);
  const electricalRemaining = afterElectrical.filter(isElectrical);
  if (electricalRemaining.length) {
    snapshot("electrical-unresolved", 0, afterElectrical);
    emitUnresolvedNote(turnId, "electrical-function", electricalRemaining, onEvent);
    return changed;
  }

  // Phase 3 — craft. ALWAYS at least one round; break when a round changes
  // nothing. Remaining advisory warnings seed the prompt as hints.
  let hints = collectBoardWarnings(workspace).filter(
    (w) => !isBlocking(w) && !isElectrical(w),
  );
  for (let i = 0; i < MAX_CRAFT_ROUNDS; i += 1) {
    if (aborted() || regressed || providerFailed) return changed;
    snapshot("craft", i + 1, collectBoardWarnings(workspace));
    log(`review craft round ${i + 1}`);
    emitPhaseNote(turnId, onEvent, {
      phase: "craft",
      round: i + 1,
      rounds: MAX_CRAFT_ROUNDS,
      detail: "reading the schematic and PCB images",
    });
    // Claude reads the renders itself; attaching them would change what the
    // Claude arm receives, so only the codex arm gets the attachment.
    const roundChanged = await round(
      buildCraftPrompt(hints),
      provider === "codex" ? reviewImagePaths(workspace) : [],
    );
    changed = roundChanged || changed;
    if (!roundChanged) {
      break;
    }
    if (i + 1 < MAX_CRAFT_ROUNDS) {
      hints = collectBoardWarnings(workspace).filter(
        (w) => !isBlocking(w) && !isElectrical(w),
      );
    }
  }
  // Phase 4 — the panel. Only for a board that reached fab-ready: the skill's
  // rule is "the moment fab.ready is true", and a board still failing has
  // nothing for seven lenses to score.
  for (let i = 0; i < MAX_PANEL_ROUNDS; i += 1) {
    if (aborted() || regressed || providerFailed) return changed;
    if (workspaceFabReady(workspace) !== true) break;
    snapshot("panel", i + 1, collectBoardWarnings(workspace));
    log(`review panel round ${i + 1}`);
    emitPhaseNote(turnId, onEvent, {
      phase: "the expert panel",
      round: i + 1,
      rounds: MAX_PANEL_ROUNDS,
      detail: "seven lenses score the board before anyone pays a fab",
    });
    changed = (await round(buildPanelPrompt())) || changed;
  }

  snapshot("final", 0, collectBoardWarnings(workspace));
  return changed;
}

// ---------------------------------------------------------------------------
// Session JSONL → chat history rehydration (donor: chat.rs)
// ---------------------------------------------------------------------------

/** Prefix of the synthetic approve-plan prompt; rehydration drops user lines
 * starting with it. Keep in sync with `approvedPlanMessage`. */
export const APPROVE_PLAN_PREAMBLE = "The plan below is approved. Implement it now";

/** Marker beginning the machine-readable attachment note; stripped from
 * rehydrated user bubbles. */
export const ATTACHMENT_NOTE_MARKER = "\n\n[Attached reference image";

/** The synthetic message that kicks off the build phase from an approved (or
 * auto-approved) plan. */
export function approvedPlanMessage(planText) {
  const body = String(planText || "").trim()
    ? String(planText)
    : "(Implement the plan you just designed in this session.)";
  return (
    "The plan below is approved. Implement it now: write the board " +
    "source and generate the board and all fab artifacts as described.\n\n" +
    body
  );
}

function extractVisibleText(content) {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .filter((b) => b && b.type === "text" && typeof b.text === "string")
      .map((b) => b.text)
      .join("\n\n");
  }
  return "";
}

/**
 * Parse a Claude Code session JSONL transcript into ChatSessionState history:
 * one entry per user prompt, one grouped assistant entry per response whose
 * `blocks` rebuild the live trace (thinking/text/tool_use with resolved
 * status, summary, and timings). `isMeta` injections, the synthetic
 * approve-plan prompt, attachment notes, and intercepted
 * ExitPlanMode/AskUserQuestion tool calls are dropped.
 */
/** The Codex arm's transcript, in the shape the chat panel already renders.
 *
 * Codex keeps its own rollout under `~/.codex/sessions/<y>/<m>/<d>/`; nothing
 * of it reaches the Claude transcript `sessionState` reads, so a Codex
 * project's chat came back empty on every reload — the opening prompt gone
 * (2026-09-09, the owner had to recover it from this file by hand). The
 * rollout is JSONL of `{timestamp, payload:{type, role, content}}`; the
 * user rows carry the app's phase preamble inline (Codex has no
 * --append-system-prompt), so the user's own words are what follows the
 * PROJECT WORKSPACE directive. Review-round rows are the silent loop's
 * prompts, not conversation, and are dropped the way the review child's
 * stdout is.
 */
export function parseCodexRolloutHistory(contents, { workspace = "" } = {}) {
  const history = [];
  for (const rawLine of String(contents || "").split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    const payload = obj?.payload;
    if (!payload || payload.type !== "message") continue;
    const role = payload.role === "user" ? "user" : payload.role === "assistant" ? "assistant" : "";
    if (!role) continue;
    const content = payload.content;
    let text = Array.isArray(content)
      ? content.map((part) => (typeof part?.text === "string" ? part.text : "")).filter(Boolean).join("\n")
      : typeof content === "string" ? content : "";
    if (!text.trim()) continue;
    const at = Date.parse(obj.timestamp || "") || 0;
    if (role === "user") {
      // Codex's own environment rows, not the conversation.
      if (text.startsWith("<environment_context>") || text.includes("<recommended_plugins>")) continue;
      // The silent review loop's prompts — the Claude arm never shows these
      // either, because its review child's transcript is never parsed as chat.
      if (/An automatic\s+post-build review of the board you just built is running/.test(text)) continue;
      // Strip the phase preamble: the user's words follow the workspace line.
      const marker = workspace ? `${workspace}\n` : "";
      const idx = marker ? text.lastIndexOf(marker) : -1;
      if (idx >= 0) {
        text = text.slice(idx + marker.length);
      } else if (text.startsWith("You are running inside Autonomous Circuit")) {
        const i = text.indexOf("\n\n", text.indexOf("PROJECT WORKSPACE"));
        const j = i >= 0 ? text.indexOf("\n\n", i + 2) : -1;
        text = j >= 0 ? text.slice(j + 2) : text;
      }
      // The app's effort suffix is a setting, not something the user typed.
      text = text.replace(/\n*\[Effort: [^\]]*\]\s*$/s, "").trim();
      if (!text) continue;
      history.push({ role, content: text, at, blocks: [] });
    } else {
      history.push({ role, content: text.trim(), at, blocks: [{ kind: "text", text: text.trim() }] });
    }
  }
  return history;
}

/** Where Codex wrote the rollout for a session id, or "" when it has none.
 *
 * `~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-<stamp>-<session id>.jsonl`;
 * the date is the day the session started, which the app does not record, so
 * walk the tree from the newest day down. `CODEX_HOME` moves the root the
 * same way it does for the CLI.
 */
export function findCodexRolloutPath(sessionId, env = process.env) {
  if (!sessionId) return "";
  const root = path.join(env.CODEX_HOME || path.join(os.homedir(), ".codex"), "sessions");
  const suffix = `-${sessionId}.jsonl`;
  const list = (dir) => {
    try {
      return fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return [];
    }
  };
  const dirsDesc = (dir) => list(dir).filter((e) => e.isDirectory()).map((e) => e.name).sort().reverse();
  for (const y of dirsDesc(root)) {
    for (const m of dirsDesc(path.join(root, y))) {
      for (const d of dirsDesc(path.join(root, y, m))) {
        const day = path.join(root, y, m, d);
        const hit = list(day).find((e) => e.isFile() && e.name.endsWith(suffix));
        if (hit) return path.join(day, hit.name);
      }
    }
  }
  return "";
}

export function parseSessionHistory(contents) {
  const history = [];
  let blocks = [];
  let textParts = [];
  let turnAt = 0;
  let pending = new Map(); // toolUseId -> index into blocks

  const flush = () => {
    if (blocks.length) {
      history.push({
        role: "assistant",
        content: textParts.join("\n\n"),
        at: turnAt,
        blocks,
      });
    }
    blocks = [];
    textParts = [];
    turnAt = 0;
    pending = new Map();
  };

  for (const rawLine of String(contents || "").split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    if (!obj || typeof obj !== "object" || obj.isMeta === true) {
      continue;
    }
    const type = obj.type;
    if (type !== "user" && type !== "assistant") {
      continue;
    }
    const at = Date.parse(obj.timestamp || "") || 0;
    const content = obj?.message?.content;

    if (type === "assistant") {
      if (turnAt === 0) {
        turnAt = at;
      }
      if (!Array.isArray(content)) {
        continue;
      }
      for (const block of content) {
        const bt = block?.type;
        if (bt === "thinking") {
          if (typeof block.thinking === "string" && block.thinking) {
            blocks.push({ kind: "thinking", text: block.thinking, at });
          }
        } else if (bt === "text") {
          if (typeof block.text === "string" && block.text) {
            textParts.push(block.text);
            blocks.push({ kind: "text", text: block.text });
          }
        } else if (bt === "tool_use") {
          const name = String(block.name || "");
          // Not tool chips live — they become a plan / question card.
          if (name === "ExitPlanMode" || name === "AskUserQuestion") {
            continue;
          }
          const id = String(block.id || "");
          if (id) {
            pending.set(id, blocks.length);
          }
          blocks.push({
            kind: "tool_use",
            tool: name,
            toolUseId: id,
            input: block.input ?? {},
            status: "ok",
            at,
            endedAt: at,
          });
        }
      }
      continue;
    }

    // A user turn carrying tool_result blocks isn't a prompt — it resolves
    // the current assistant turn's pending tools and continues it.
    const isToolResult =
      Array.isArray(content) && content.some((b) => b?.type === "tool_result");
    if (isToolResult) {
      for (const block of content) {
        if (block?.type !== "tool_result") {
          continue;
        }
        const id = String(block.tool_use_id || "");
        if (!pending.has(id)) {
          continue;
        }
        const idx = pending.get(id);
        const target = blocks[idx];
        if (target && target.kind === "tool_use") {
          target.status = block.is_error === true ? "error" : "ok";
          target.endedAt = at;
          const summary = summarizeToolResult(block.content);
          if (summary) {
            target.resultSummary = summary;
          }
        }
      }
      continue;
    }

    // A real user prompt closes the previous assistant turn, then lands.
    flush();
    let text = extractVisibleText(content);
    const markerIdx = text.indexOf(ATTACHMENT_NOTE_MARKER);
    if (markerIdx !== -1) {
      text = text.slice(0, markerIdx);
    }
    const trimmed = text.trim();
    if (!trimmed || trimmed.startsWith(APPROVE_PLAN_PREAMBLE)) {
      continue;
    }
    history.push({ role: "user", content: trimmed, at, blocks: [] });
  }
  flush();
  return history;
}

// ---------------------------------------------------------------------------
// Attachments (reference images → <workspace>/inputs/)
// ---------------------------------------------------------------------------

const MAX_ATTACHMENTS = 6;
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;

const IMAGE_EXTENSIONS = new Map([
  ["image/png", "png"],
  ["image/jpeg", "jpg"],
  ["image/jpg", "jpg"],
  ["image/webp", "webp"],
  ["image/gif", "gif"],
]);

function invalidArgument(message) {
  const err = new Error(message);
  err.code = "INVALID_ARGUMENT";
  err.statusCode = 400;
  return err;
}

/**
 * Decode and persist reference images into `<workspace>/inputs/` (uuid-named,
 * never the user-supplied name), returning workspace-relative paths. Written
 * before the turn spawns so they predate the mtime baseline; `inputs/` is on
 * the skip-list so they never surface as artifacts or catalog entries.
 */
export function persistAttachments(workspace, images = []) {
  if (images.length > MAX_ATTACHMENTS) {
    throw invalidArgument(`too many images: ${images.length} (max ${MAX_ATTACHMENTS})`);
  }
  const dir = path.join(workspace, "inputs");
  fs.mkdirSync(dir, { recursive: true });
  const rels = [];
  for (const image of images) {
    const ext = IMAGE_EXTENSIONS.get(String(image?.mediaType || "").trim().toLowerCase());
    if (!ext) {
      throw invalidArgument(`unsupported image type: ${image?.mediaType}`);
    }
    let bytes;
    try {
      bytes = Buffer.from(String(image?.dataBase64 || ""), "base64");
      // Node's base64 decoder is lenient; round-trip to reject garbage.
      if (!bytes.length && String(image?.dataBase64 || "").trim()) {
        throw new Error("empty decode");
      }
    } catch {
      throw invalidArgument("invalid base64 image data");
    }
    if (!bytes.length || bytes.length > MAX_ATTACHMENT_BYTES) {
      throw invalidArgument(
        `image must be 1..=${MAX_ATTACHMENT_BYTES} bytes, got ${bytes.length}`,
      );
    }
    const name = `${crypto.randomUUID()}.${ext}`;
    fs.writeFileSync(path.join(dir, name), bytes);
    rels.push(`inputs/${name}`);
  }
  return rels;
}

/** The note appended to a user message so the model opens the attached
 * images with Read. Begins with ATTACHMENT_NOTE_MARKER (stripped on
 * rehydration). */
export function attachmentNote(rels) {
  if (!rels.length) {
    return "";
  }
  return `${ATTACHMENT_NOTE_MARKER}(s): ${rels.join(", ")}. View each with the Read tool before responding.]`;
}

// ---------------------------------------------------------------------------
// Chat service — turn registry + autopilot chaining (donor: chat.rs)
// ---------------------------------------------------------------------------

/**
 * Create the chat orchestration service.
 *
 * - `projectDir(projectId)` → absolute workspace dir.
 * - `settings.read()` → `{ autoBuild, provider, model }`.
 * - `emit(projectId, event)` → deliver one enveloped ChatEvent.
 *
 * `startTurn` returns the turnId synchronously (the run continues in the
 * background); **autopilot**: a PLAN turn that proposed a plan (ExitPlanMode
 * fired — plan-present, not plan-non-empty) chains straight into a build turn
 * when `autoBuild !== false`.
 */
export function createChatService({ projectDir, settings, emit, env = process.env }) {
  const turns = new Map(); // turnId -> { projectId, controller }

  function activeModel() {
    try {
      return settings.read().model || "";
    } catch {
      return "";
    }
  }

  function activeProvider() {
    try {
      return settings.read().provider === "codex" ? "codex" : "claude";
    } catch {
      return "claude";
    }
  }

  function activeEffort() {
    try {
      return settings.read().effort || "";
    } catch {
      return "";
    }
  }

  function runTurn({ projectId, sessionId, message, imagePaths, phase, turnId }) {
    const controller = new AbortController();
    turns.set(turnId, { projectId, sessionId: resolvedSessionId(projectId, sessionId), controller });
    const workspace = projectDir(projectId);
    const activeSessionId = resolvedSessionId(projectId, sessionId);
    const onEvent = (event) => emit(projectId, { ...event, sessionId: activeSessionId });

    const run = spawnTurn({
      workspace,
      sessionId: activeSessionId,
      message,
      imagePaths,
      turnId,
      phase,
      provider: activeProvider(),
      model: activeModel(),
      effort: activeEffort(),
      onEvent,
      signal: controller.signal,
      env,
    })
      .catch((error) => {
        // Defensive: spawnTurn reports its own failures; this catch only
        // guards a driver bug so the turn still closes error → turn_end.
        onEvent({ kind: "error", turnId, message: `driver failure: ${error?.message || error}` });
        onEvent({ kind: "turn_end", turnId });
        return { proposedPlan: null, cancelled: false, sawOutput: false };
      })
      .then((result) => {
        turns.delete(turnId);
        return result;
      });

    return { run };
  }

  function startTurn({ projectId, sessionId, message, imagePaths = [], phase }) {
    const turnId = crypto.randomUUID();
    const activeSessionId = resolvedSessionId(projectId, sessionId);
    const { run } = runTurn({ projectId, sessionId: activeSessionId, message, imagePaths, phase, turnId });

    if (phase === PHASE.PLAN) {
      // Autopilot: after a plan turn that PROPOSED a plan, build it — gated
      // on the ExitPlanMode event, NOT on the plan text being non-empty (an
      // empty proposed plan must still build; the resumed session carries the
      // reasoning). A turn that stopped for questions proposes no plan.
      run.then(({ proposedPlan, cancelled }) => {
        if (proposedPlan === null || cancelled) {
          return;
        }
        let autoBuild = true;
        try {
          autoBuild = settings.read().autoBuild !== false;
        } catch {
          autoBuild = true;
        }
        if (!autoBuild) {
          return;
        }
        log(`autopilot: chaining build turn for project ${projectId}`);
        const buildTurnId = crypto.randomUUID();
        runTurn({
          projectId,
          sessionId: activeSessionId,
          message: approvedPlanMessage(proposedPlan),
          imagePaths: [],
          phase: PHASE.IMPLEMENT,
          turnId: buildTurnId,
        });
      });
    }

    return turnId;
  }

  function cancelTurn(turnId) {
    const entry = turns.get(turnId);
    if (!entry) {
      return false;
    }
    turns.delete(turnId);
    entry.controller.abort();
    return true;
  }

  function turnInProgress(projectId, sessionId = "") {
    for (const entry of turns.values()) {
      if (entry.projectId === projectId && (!sessionId || entry.sessionId === sessionId)) {
        return true;
      }
    }
    return false;
  }

  function sessionState(projectId, requestedSessionId = "") {
    const sessionId = resolvedSessionId(projectId, requestedSessionId);
    const workspace = projectDir(projectId);
    let history = [];
    try {
      const contents = fs.readFileSync(sessionJsonlPath(workspace, sessionId, env), "utf8");
      history = parseSessionHistory(contents);
    } catch {
      history = [];
    }
    if (!history.length) {
      // No Claude transcript: a project driven by the Codex arm keeps its
      // conversation in Codex's own rollout instead.
      const rollout = findCodexRolloutPath(codexSessionIdFor(workspace, env), env);
      if (rollout) {
        try {
          history = parseCodexRolloutHistory(fs.readFileSync(rollout, "utf8"), { workspace });
        } catch {
          history = [];
        }
      }
    }
    let activeTurnId = "";
    for (const [id, entry] of turns) {
      if (entry.projectId === projectId && (!sessionId || entry.sessionId === sessionId)) {
        activeTurnId = id;
        break;
      }
    }
    // `activeTurnId` is what `chat_cancel_turn` needs; until now a turn the
    // server chained itself (autopilot) had an id nobody outside the SSE
    // stream could learn, so a silent review round could not be stopped
    // from anywhere but the browser tab that happened to be open.
    return { sessionId, turnInProgress: Boolean(activeTurnId), activeTurnId, history };
  }

  function sessionList(projectId) {
    const workspace = projectDir(projectId);
    const sessionsDir = path.dirname(sessionJsonlPath(workspace, sessionIdForProject(projectId), env));
    let entries = [];
    try {
      entries = fs.readdirSync(sessionsDir, { withFileTypes: true });
    } catch {
      return [];
    }
    const summaries = [];
    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.endsWith(".jsonl")) continue;
      const sessionId = entry.name.slice(0, -".jsonl".length);
      if (!SESSION_ID_RE.test(sessionId)) continue;
      const file = path.join(sessionsDir, entry.name);
      try {
        const contents = fs.readFileSync(file, "utf8");
        const history = parseSessionHistory(contents);
        if (!history.length) continue;
        const firstUser = history.find((item) => item.role === "user");
        const fallback = String(firstUser?.content || "New chat").replace(/\s+/g, " ").trim();
        const title = parseLatestAiTitle(contents) || fallback.slice(0, 54) || "New chat";
        const updatedAt = Math.max(
          fs.statSync(file).mtimeMs,
          ...history.map((item) => Number(item.at) || 0),
        );
        summaries.push({ sessionId, title, updatedAt, messageCount: history.length });
      } catch {
        // A transcript can be mid-write; omit it until the next refresh.
      }
    }
    summaries.sort((a, b) => b.updatedAt - a.updatedAt || a.sessionId.localeCompare(b.sessionId));
    return summaries;
  }

  function createSession(projectId) {
    return {
      sessionId: resolvedSessionId(projectId, crypto.randomUUID()),
      title: "New chat",
      updatedAt: Date.now(),
      messageCount: 0,
    };
  }

  function close() {
    for (const { controller } of turns.values()) {
      controller.abort();
    }
    turns.clear();
  }

  return { startTurn, cancelTurn, turnInProgress, sessionState, sessionList, createSession, close };
}
