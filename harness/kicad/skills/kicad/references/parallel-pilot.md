# Astra / Sol team pilot

This is the lower-level experimental CLI for a **new isolated run**. A new Codex KiCad project
in Harness has `.circuit/team-auto`: its entry agent passes each build request to the lead with
`kicadpy.parallel_tile start <workspace> --prompt <message-file> --execute`. The CLI selects
`teams` mode for the bridge. The first build message creates the one sealed contract; every later
build or change message is delivered verbatim to the lead as an append-only supplemental release
note. The lead routes that note to the responsible teams. The bridge makes worker progress and checked
output visible in that project's viewer.
Questions and plan-only requests use the bridge's read-only `ask` command, which sends the
question to the lead without creating a contract, release note, or build. It works before the
first contract and after. Save the exact question in an immutable UTF-8 history file, then run:

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel_tile ask /absolute/project \
  --prompt /absolute/project/prompts/history/exact-question.md --execute
```

Changes to the product use `start` and become lead-routed release notes after the first build.
If a build is still active, send the new exact-message file through `parallel_tile note`.
Copy newly attached images and reference files into the visible project's `prompts/assets/`
before either command. The lead receives bounded, hash-checked copies alongside the verbatim
new message; the message itself is not rewritten to invent an attachment path.
The lead records the release note while workers continue. After that run ends, use
`parallel_tile resume <workspace> --execute` to dispatch the new note without writing it
again. `resume` also continues an incomplete bounded run under the same contract when no
new user message exists.
Existing KiCad projects without the marker, and the Claude and Grok sibling tiles, keep their
single-agent workflow. The commands
below are for operators who explicitly want an isolated pilot or comparison; reading this
reference alone authorizes no model call or benchmark. A standalone pilot never adopts an
active pet workspace. The bridge may seed a new follow-up pilot from accepted prior work.
Neither path orders, prints, flashes or operates hardware. A previous project's flash
permission is not inherited.

On macOS, an executing Harness bridge run holds a scoped `caffeinate -i` assertion from team
initialization through completion. It releases the assertion on normal return, error or
interrupt; `-w` also ties it to the bridge process if that process is killed. This prevents
idle sleep while the team runs, but a closed laptop lid or thermal protection can still sleep
the machine. Standalone `kicadpy.parallel` commands do not use this bridge assertion.

## Start and inspect

Run from a source checkout with the existing KiCad / firmware / CadQuery toolchain installed,
Node available, and a current authenticated `codex` CLI supporting `exec --json`,
`--ignore-user-config`, `--ephemeral` and `--output-schema`. Use the checkout's Python wrapper;
no separate API key or Python dependency is added. The existing CLI login is used.

```bash
export KICAD_HARNESS_PYTHON="$PWD/harness/kicad/toolchain/python"
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel init /tmp/sensor-team --prompt /absolute/brief.txt --mode teams
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel status /tmp/sensor-team
# The next two commands make real model calls. Init/status/metrics do not.
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel plan /tmp/sensor-team --execute
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel run /tmp/sensor-team --execute
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel metrics /tmp/sensor-team
```

`init` requires a nonexistent directory. Optional `--seed /clean/git/workspace` archives the
committed owned artifacts and README/project marker, rejects dirty seeds and symlinks, and
never changes the seed. Create the pilot outside the seed. Flash policies, hooks, sessions,
`.circuit`, `.harness`, `.kicadpy` and git configuration are not copied. Existing printed-part constraints
belong in the brief and seeded case documents; preserve proven parts and generate local repair
parts/plates when that is the brief. A seed is a source baseline, not physical-test evidence.
If the prompt references images, use stable absolute file paths or durable URLs in the supplied
brief; `init` copies prompt text but does not copy adjacent image files. Keep an image index with
source, purpose and current/superseded status. Verify each referenced file still exists before
`plan`; a bare `[Image #]` marker or temporary clipboard path does not give the lead visual
evidence. If an image is missing, say so in the brief and avoid guessing measurements from it.

Defaults: lead `gpt-6-astra` at medium effort, workers and testers `gpt-6.1-sol` at high effort. Set
`--lead-model`, `--worker-model` and `--timeout` at init when needed; there is no silent model
fallback. The per-job timeout defaults to 1800 seconds, bounded to 10–14400 at initialization.
The limit is per call, not a total token/dollar budget. Standalone `parallel init` defaults to
the older `parallel` comparison mode; use `--mode teams` to exercise the bridge's independent
team flow manually. Do not read the old `parallel` mode as the new tile behavior.

## Contract and stages

The lead produces a strict JSON interface contract: board outline, thickness and mounting
holes; mm origin/axes; power sources/rails; device pins, signal/net names and voltage; module
locations and envelopes; total mechanical envelope; firmware behavior; assumptions and three
scoped tasks. Unknown hardware uses a named reference with **GIẢ ĐỊNH**, without technical
questions to the owner. Host validation rejects malformed/nonfinite geometry, degenerate
outlines, duplicate device pins and invalid dimensions. It is schema/interface validation,
not a proof of electrical safety or mechanical fit.

The host seals `PILOT_CONTRACT.json` with a revision, prompt/instruction hashes and canonical
SHA256. Frozen original prompt lives at `.pilot/prompt.txt`, mirrored as `PILOT_BRIEF.md`.
Snapshots of AGENTS/tool card/references are under `.pilot/instructions`. Pipeline/checker
source and tile environment-manifest hashes are recorded and checked again; use a pinned checkout
throughout a pilot. In a team project, later messages reuse the exact sealed first contract.
The bridge records the lead's routing as hash-chained release notes under `.pilot/addenda/`;
the base contract is never rewritten or replanned. A later note supersedes the base and earlier
notes only for the contract fields it names. Every other requirement remains in force.

1. On the first request, Astra plans read-only and the host seals the base contract once. On
   each later request, Astra reads that base and all earlier notes, then writes a supplemental
   note and selects the affected teams. The user always speaks to the lead through the bridge.
2. Board, firmware, and case each have a Sol builder and a separate Sol tester. The selected
   teams start from the same contract without waiting for another team's output or a lead gate.
   The firmware team implements the defined behavior against provisional pins; the case team
   builds against the contracted geometry. Both must later reconcile with actual board files.
   A tester inspects its team's real output and findings; a rejection sends work back to the
   same builder for a bounded repair attempt, without making the lead replan.
   The lead seals only cross-team pins, rails, outline, mounts and envelopes. When a sealed
   detail inside one domain proves wrong or infeasible (for example a datasheet contradicts
   an internal IC pin, or an agent-chosen size cannot be met), the builder picks the closest
   verified V0 option, records it in `engineering/`, `firmware/` or `case/DEVIATIONS.md` and
   continues. Testers accept recorded deviations; only user-dependent issues remain blockers.
3. The bridge copies bounded drafts to a distinct viewer preview area as work advances. Each
   preview is marked unverified. Valid role output is imported only after schema, contract,
   ownership, Git state, tester and relevant native checks; the bridge then mirrors checked
   checkpoints to the main viewer. A sibling's blocker does not erase already accepted work.
4. The host republishes the integrated native board and performs final checks for written and
   current built firmware, wiring, case status and guide, and the prepared print-project binding.
   An independent integration tester inspects the real combined artifacts and identifies the
   responsible team for any interface mismatch. Bounded repair rounds return findings to those
   teams. `candidate_ready` and the bridge's `teamReady` require both the native checks and
   integration test to pass. A model approval cannot override a failed native checker.

The board-to-firmware pin comparison and broader mechanism/overview assessment are **model
review**, not a new deterministic equivalence/physics checker. Print project's hash, canonical
STL/rotation and exactly-once production plate membership use the existing export validator;
the project-local slicer verifier still supplies arrangement/clearance evidence. No slicer or
print is launched by this orchestration. Physical fit, strength and motion remain untested.
`candidate_ready` describes this experimental workflow, not permission to order or flash.
Existing native verdict artifacts retain their existing meaning; no verdict is fabricated.
The bridge records checked checkpoints as completed roles, board, or final. Its `teamReady`
stays false during these checkpoints and becomes true only after the final native gate and
integration test both pass. A native board verdict of `ready: true` describes that board check;
it does not mean the full team build is ready. Failed or rejected role output may appear only
as an explicitly unverified progress preview, never as an accepted viewer artifact.

Ownership is enforced at import, not an OS security boundary:

- Board: `design/`, `boards/`, `engineering/`, `product.json`, `parts.json`, `manufacturing.json`, `tools/board/`, `build/board/`.
- Firmware: `firmware/`, `tools/firmware/`, `build/firmware/`.
- Case: `case/`, `tools/case/`, `build/case/`.

Put worker helper scripts and temporary routing/build outputs in those paths; generic
`tools/foo.py` or `build/cand/` is not owned. Other domains and shared inputs are read-only.
Scratch native state `.circuit/`, `.harness/` and `.kicadpy/` (including adjacent native snapshot stores) is excluded and not imported. The host reruns
native publication in the integrated workspace. Worktrees share a **private pilot** repository
with no remote; the host commits individually named files. Workers must not commit, push,
delegate models or alter gates. Lead runs read-only; workers use `danger-full-access` with approval policy never, matching
the existing tile. Its documented native DRC/pcbnew/router failure under workspace-write is
why this mode is required. Import ownership is a workflow guard, not host isolation. Running
`--execute` starts native-capable workers with that same access; review this before launching. Pinned firmware/block tool paths are loaded from the tile manifest. Host Harness context and thread ID are removed, hooks
are not configured, and the nested advisor is disabled.

## Failures, repairs, and follow-up requests

Read `.pilot/state.json`, `.pilot/jobs/*/receipt.json`, `events.jsonl`, `stderr.log`, and
`.pilot/rounds/<revision>/{board-check,final-check}.json`. Failed worker outputs remain in their
worktrees for diagnosis and may appear only as unverified viewer previews. An invalid first
planning attempt retains usage and the previous readable contract.

In `teams` mode, builder/tester work is bounded to three rework rounds per role and integration
repair to two rounds. A tester or final native check can reject output; `teamReady` stays false
and the bridge reports findings. This does not trigger a new base contract. For a later build,
repair, or change request, save **only the new user's words** in a new immutable file and pass it
to the lead:

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel_tile start /absolute/project \
  --prompt /absolute/project/prompts/history/next-user-message.md --execute
```

The bridge starts a fresh private pilot with the exact original sealed contract, carries its
verified release-note chain, records the lead's new note, and assigns the teams it names. It
seeds from the preceding clean pilot checkpoint. The next run may also copy source-only drafts
from an unfinished role into that role's private worktree after ownership and hash checks; those
drafts remain unverified until its tester and native gates pass. The teams preserve compatible work and
reconcile interfaces against the latest notes and actual files. Do not edit
`PILOT_CONTRACT.json`, rewrite an earlier message, or call `plan --revise` for a new user request.

Within one dispatch, a structured model-capacity failure may retry only the affected job,
within a fixed attempt limit. The retry keeps the same sealed contract and may continue
role-owned partial files in that worker's isolated worktree. It refuses changed shared Git
metadata, a moved HEAD or any unowned file change. Each attempt keeps its own receipt and
usage; completed sibling workers are called once. Exhausted capacity reports
`model_unavailable`, without contract replanning.

A worker timeout may continue in its own worktree only when it left role-owned partial work.
The board stage can make up to six continuations, each bounded by the configured per-job timeout,
only if **each timed-out attempt changes owned-file contents**; touching or rebuilding identical
files does not count. Its controller wall deadline is three hours (or a longer explicitly
configured initial per-job timeout), including capacity backoff. Other worker stages retain one
600-second continuation and the original per-job timeout plus 600-second controller deadline.
A timeout without fresh progress or one that exhausts its attempt or wall limit reports
`execution_limit`. Receipts retain every attempt, configured time limit and reported usage;
process cleanup and receipt I/O can add a little wall time. Interrupted work reports
`interrupted`. These outcomes do not count as engineering approval. Independently validated
and imported sibling outputs stay visible.

The older standalone `parallel`, `sequential`, and `solo` modes retain explicit contract
revision for comparisons. For those modes only:

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel plan /tmp/sensor-legacy --revise --execute
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel run /tmp/sensor-legacy --execute
```

That legacy revision includes prior findings and preserves accepted compatible artifacts. It
changes the contract hash, uses fresh worktrees, and reruns its stage checks. Old receipts and
contracts remain for audit. It is not the team tile's follow-up path. An external change to
candidate artifacts makes `status` report stale. A file lock prevents two controller commands
from operating on the same pilot at once.

Ctrl-C stops the current foreground call or kills the active worker process groups, retains
available receipts and marks the round failed/interrupted. Timeouts also kill process groups
and retain partial reported usage. SIGKILL/power loss cannot be cleaned up automatically: do
not rerun an orphaned active pilot; inspect/stop its recorded CLI jobs and create a fresh pilot.
No live-run daemon or existing pet process is involved.

## Compare without claiming 3× speed

Keep original brief, instruction/pipeline hashes, seed revision, model/effort and checker
rules fixed. After the team's **revision-one** plan, create control pilots with the same
brief, instructions and seed, and reuse its sealed plan without paying for another plan:

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel init /tmp/sensor-serial --prompt /absolute/brief.txt --mode sequential
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel plan /tmp/sensor-serial --contract-from /tmp/sensor-team
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel run /tmp/sensor-serial --execute
```

`sequential` uses identical role worktrees/stages but executes worker calls in sequence;
this isolates concurrency rather than changing handoffs. `solo` uses one Sol builder for the
initial board→firmware→frame stage, then one Sol for reconciliation/covers after the same
native/lead checkpoint. It is a **contract-conditioned single-builder control**, not the old
self-planning single-Sol tile or a persistent chat. Lead still plans and reviews. Neither
control is launched by the normal Harness bridge. Reusing a plan records its source and excludes the original
planning call from that control's own usage; report shared planning cost explicitly, not as
free planning. A control with a different input hash or seed commit is rejected.

Metrics include each lead/worker job, configured models, round/repair counts, sum of active
controller wall time (planning + bounded rounds), separate elapsed time since init, summed
agent duration (overlaps under parallelism), and reported input/cached/output/reasoning
usage. Fresh input is input minus cached input. Reasoning is an output subset, not additional
billable output. Missing usage is unknown (`usageComplete: false`), never an asserted zero
bill. Disabled nested-advisor and approval calls are recorded as zero configured calls.
Dollar fields stay null: CLI token receipts do not establish actual subscription billing or
API-equivalent price. No API price is invented or applied to a logged-in subscription.

Report quality/native/physical failures and repair effort along with time and usage. Do not
compare a green partial frame to a finished board/firmware/case run. Historical tile baselines
need their own matching prompt/rules/hardware and full-session accounting. Offline fake-worker
tests establish orchestration behavior only; a real pilot is required before performance or
engineering-quality claims.

Codex CLI flags and JSON usage follow [OpenAI's non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).

Runtime guards: a team run refuses to start below 8 GB free disk (`CIRCUIT_TEAM_MIN_FREE_GB`).
Firmware workers share one toolchain cache (`CIRCUIT_FIRMWARE_CACHE`, default
`~/.cache/autonomous-circuit/firmware`) through `PLATFORMIO_CORE_DIR`, `ARDUINO_DIRECTORIES_DATA`
and `PIP_CACHE_DIR`; never install cores or SDKs inside a worktree. Board and case both get long
timeout continuations while they keep making owned file progress.
Snapshots skip symlinks under `build/` (tool scratch such as headless Chrome profiles) and still
reject symlinks anywhere else; worktree snapshot bounds are 2 GB / 20000 files.

Team loop: each builder repairs until its tester approves, with a fresh stage clock per repair
round (ceilings: 8 rounds, 6 h per role). When the tester cites the same file again and the
builder left it byte-identical, the lead is called once as arbiter (read-only): repair with a
concrete fix, waive the tester finding (recorded in `.pilot/waivers/<stage>.json`, never for
native, safety or polarity defects, shown to later testers and the integration tester),
interface_change or external_blocker. Still stuck on the same file after that ruling stops the
team with `needs_revision` instead of looping.
