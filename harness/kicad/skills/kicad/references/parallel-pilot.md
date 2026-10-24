# Astra / Sol team pilot

This is the lower-level experimental CLI for a **new isolated run**. A new Codex KiCad project
in Harness has `.circuit/team-auto`: its entry agent automatically calls
`kicadpy.parallel_tile start <workspace> --prompt <brief> --execute` after the person sends a
board-build prompt, and the bridge makes checked output visible in that project's viewer.
Questions and plan-only requests do not start a pilot. Existing KiCad projects without the
marker, and the Claude and Grok sibling tiles, keep their single-agent workflow. The commands
below are for operators who explicitly want an isolated pilot or comparison; reading this
reference alone authorizes no model call or benchmark. A standalone pilot never adopts an
active pet workspace. The bridge may seed a new follow-up pilot from accepted prior work.
Neither path orders, prints, flashes or operates hardware. A previous project's flash
permission is not inherited.

## Start and inspect

Run from a source checkout with the existing KiCad / firmware / CadQuery toolchain installed,
Node available, and a current authenticated `codex` CLI supporting `exec --json`,
`--ignore-user-config`, `--ephemeral` and `--output-schema`. Use the checkout's Python wrapper;
no separate API key or Python dependency is added. The existing CLI login is used.

```bash
export KICAD_HARNESS_PYTHON="$PWD/harness/kicad/toolchain/python"
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel init /tmp/sensor-team --prompt /absolute/brief.txt
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

Defaults: lead `gpt-6-astra` at medium effort, workers `gpt-6.1-sol` at high effort. Set
`--lead-model`, `--worker-model` and `--timeout` at init when needed; there is no silent model
fallback. The per-job timeout defaults to 1800 seconds, bounded to 10–14400 at initialization.
The limit is per call, not a total token/dollar budget. The board worker runs first;
firmware and case can then overlap, so there are at most two concurrent worker calls
and one bounded integration round per `run`; no automatic repair loop.

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
source and tile environment-manifest hashes are recorded and checked again; use a pinned checkout throughout a pilot.

1. Astra plans read-only; host seals the contract.
2. A Sol board worker builds in a detached worktree. Host validates its contract/stage receipt,
   blockers, ownership and unchanged HEAD, imports the output, then republishes the actual
   native board with the existing manufacturing checker. The bridge shows this checked board
   and its real native verdict, including a failed verdict. After a passing native check, Astra
   reviews the actual board and interfaces. Firmware and case wait for this lead approval.
3. Sol firmware and functional-frame workers start from the same approved, committed
   board/netlist/STEP in separate worktrees. They may overlap. Host validates and imports each
   completed role independently, then shows that role's artifacts in the viewer; a sibling's
   blocker does not erase accepted work. The frame includes actual device mounts, fasteners,
   joints and wire routes. Covers wait; shared interfaces are frozen and uncertainty becomes a
   blocker. Astra then reviews the integrated structure and actual files.
4. After structural approval, firmware and case finishers start from the integrated work.
   Firmware reconciles its real pins and `WIRING.md`; case adds removable covers and rechecks
   full motion with hardware/wires, guide/interactive overview and production/TEST slicer
   projects. Static cases use an appropriately simple base.
5. Host republishes the native board, requires written/current built firmware and WIRING,
   fresh passing case status, case README, and Print all's existing prepared-3MF binding
   validator. Astra reviews actual artifacts against the contract. Failure from either side
   prevents `candidate_ready`; an approval cannot override a failed native checker.

The board-to-firmware pin comparison and broader mechanism/overview assessment are **model
review**, not a new deterministic equivalence/physics checker. Print project's hash, canonical
STL/rotation and exactly-once production plate membership use the existing export validator;
the project-local slicer verifier still supplies arrangement/clearance evidence. No slicer or
print is launched by this orchestration. Physical fit, strength and motion remain untested.
`candidate_ready` describes this experimental workflow, not permission to order or flash.
Existing native verdict artifacts retain their existing meaning; no verdict is fabricated.
The bridge records `lastCheckpointStage` as board, board-approved, each completed role, or
final. Its `teamReady` stays false during these checkpoints and becomes true only after the
final native gate and lead review both pass. A native board verdict of `ready: true` describes
that board check; it does not mean the full team build is ready. A failed or rejected role's
private worktree is not shown as an accepted viewer artifact.

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

## Failures and bounded revision

Read `.pilot/state.json`, `.pilot/jobs/*/receipt.json`, `events.jsonl`, `stderr.log`, and
`.pilot/rounds/<revision>/{board-check,final-check}.json`. Failed worker outputs remain in their
worktrees for diagnosis; they are never silently promoted. An invalid planning attempt retains
usage and the previous readable contract; planning attempts have distinct log directories.

Within one dispatch, a structured model-capacity failure may retry only the affected job,
within a fixed attempt limit. The retry keeps the same sealed contract and may continue
role-owned partial files in that worker's isolated worktree. It refuses changed shared Git
metadata, a moved HEAD or any unowned file change. Each attempt keeps its own receipt and
usage; completed sibling workers are called once. A role's output is integrated only after
that role passes schema, contract, ownership and hash checks. Exhausted capacity reports
`model_unavailable` and stops the bridge before a new contract revision. A true design
revision uses fresh worktrees because its contract hash has changed; old worker results are
never silently reused. The next revision may copy only source drafts from the immediately
preceding same-role worktree after checking its contract, ownership, Git state and file hashes.
The worker and lead must reconcile those drafts against the new contract; reports, build output
and prior approvals are excluded. `.pilot/drafts/` records copied source paths and hashes.

A worker timeout may make one bounded continuation in its own worktree when it left only
role-owned partial work. A timeout without progress, or one that exhausts that continuation,
reports `execution_limit` and also stops before a new contract revision. Receipts retain each
attempt and its configured time limit. All same-stage retries share a controller deadline of
the original per-job timeout plus 600 seconds, including capacity backoff; process cleanup and
receipt I/O can add a little wall time. Interrupted work reports `interrupted`. These execution
outcomes do not count as engineering approval. Independently validated and imported sibling
outputs stay visible; the failed role's private output is not promoted.

```bash
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel plan /tmp/sensor-team --revise --execute
"$KICAD_HARNESS_PYTHON" -m kicadpy.parallel run /tmp/sensor-team --execute
```

Revision includes prior findings and preserves accepted compatible artifacts. It changes the
contract hash, uses fresh worktrees for **every** worker, and reruns reconciliation/checks and
both reviews. Old receipts/contracts remain for audit and cost accounting. A failed/completed
round cannot just be rerun against its old outputs. Fixes needing brief/instruction changes
start a new pilot with explicit updated inputs; do not edit frozen snapshots. An external
change to candidate artifacts makes `status` report stale. A file lock prevents two controller
commands from operating on the same pilot at once.

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
