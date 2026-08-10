# Autonomous Circuit: chat with AI → a board you can order and build

> ⚠ v0 scaffold: drama-domain content below is being replaced by the circuit tracks.

With Autonomous Circuit, designing a PCB is a conversation: describe the gadget,
approve the plan, and the pipeline produces a fab packet you order from a fab
service and assemble with 3D-printed parts.

**1. Describe:**
Tell Circuit what you want to build — a device, a feature, a vibe.

**2. Approve:**
Review the plan. Circuit designs the board and produces the fab outputs.

**3. Iterate:**
Review the result in the built-in viewer. Give notes — Circuit regenerates only
what changed. You supply taste; Circuit supplies the engineering labor.

## Status

v0 scaffold — freshly forked from autonomous-tv; the circuit pipeline, skills,
and board viewer are being built by the circuit tracks. The repo layout and
provider notes below still describe the drama donor.

## Repo layout

- `viewer/` — the web app: Vite + React chat surface + artifact workspace,
  and the Node server driver that runs the `claude` subprocess
- `packages/dramapy/` — donor Python episode pipeline (the pipeline track forks it)
- `skills/` — Claude Code skills bundled with the app (donor drama skills, kept
  as living templates for the circuit skills)
- `docs/` — `video-interfaces.md` (the donor's frozen contract),
  `oss-decisions.md` (what we build on); the circuit docs land separately
- `scripts/` — dev/build helpers

## Render providers (donor)

Shots render through a provider abstraction (`CIRCUIT_PROVIDER`); `mock` is the
default — the full pipeline runs with zero GPUs, zero keys, zero network.

## Prerequisites

- Claude Code installed on PATH: <https://claude.ai/install>
- Node 22.12+
- Python 3.10+

## v1 LLM stance

Autonomous Circuit uses the user's existing Claude Code subscription.
