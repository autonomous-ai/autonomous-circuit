# Autonomous Circuit — expert review before we release the app

We built a tool that lets people who are not electrical engineers design a
circuit board by describing it, and order the result. **It runs with no engineer
in the loop** — that is the product, not a gap in it. A user types what they
want, approves a plan, and receives a fab packet.

We are asking you to audit that work before we put it in front of strangers.

## The question

> **Would you let a non-engineer use this unsupervised and order the boards it
> produces?**

Everything below is evidence for or against that one question. A "no" with a
reason is the most useful thing you can send back.

To be explicit about what this review is *not*: it is not a step in the product,
and nobody is proposing an engineer sign off on user boards. This is the same
shape as reviewing a compiler before shipping it — you audit the standard
library once, at build time; you do not station a human beside every compile.

## Five things to look at, in order of how much they matter

### 1. The nine golden blocks ← the foundation

| Block | Role |
|---|---|
| [`i2c-bus`](../packages/golden-blocks/blocks/i2c-bus/REVIEW.md) | frozen · composed into user boards unchanged |
| [`ldo-3v3`](../packages/golden-blocks/blocks/ldo-3v3/REVIEW.md) | frozen · composed into user boards unchanged |
| [`rp2040-core`](../packages/golden-blocks/blocks/rp2040-core/REVIEW.md) | frozen · composed into user boards unchanged |
| [`sensor-bme280`](../packages/golden-blocks/blocks/sensor-bme280/REVIEW.md) | frozen · composed into user boards unchanged |
| [`status-led`](../packages/golden-blocks/blocks/status-led/REVIEW.md) | frozen · composed into user boards unchanged |
| [`sw-tact`](../packages/golden-blocks/blocks/sw-tact/REVIEW.md) | frozen · composed into user boards unchanged |
| [`usb-c-data`](../packages/golden-blocks/blocks/usb-c-data/REVIEW.md) | frozen · composed into user boards unchanged |
| [`usb-c-power`](../packages/golden-blocks/blocks/usb-c-power/REVIEW.md) | frozen · composed into user boards unchanged |
| [`ws2812-chain`](../packages/golden-blocks/blocks/ws2812-chain/REVIEW.md) | frozen · composed into user boards unchanged |

**Why these carry the most weight.** No deterministic check catches a wrong
resistor value, a mirrored pinout, or swapped SDA/SCL — every representation we
produce (schematic, PCB render, 3D, gerbers, DRC report) derives from the same
source, so when the source is wrong they all agree with each other, confidently.
The board looks perfect and does not work, and you find out in two weeks.

Our answer to that class of error is that these values are never generated. They
live in nine frozen blocks the AI composes and never edits, so the risk
concentrates here instead of spreading across every board. **That design is only
as good as this audit** — which is why the blocks come before the boards.
Whatever you find gets fixed once and is then correct in every board the tool
ever produces.

Each folder has the source, the block's datasheet, and a sign-off sheet asking
specific questions against the manufacturer's datasheet. Please check against
the datasheet rather than our documentation — our docs and our source were
written together and can be wrong together.

### 2. The rules the tool enforces

[`docs/review/fab-limits.md`](../docs/review/fab-limits.md) is every number our DFM gate
checks, with the value and what it is checked against. If one of these numbers
is wrong, every board the tool ever produces is graded against a wrong rule.
Please check them against JLCPCB's published capabilities.

[`docs/review/safety-envelope.md`](../docs/review/safety-envelope.md) is what the tool refuses
to design, at planning time, before anything is built.

### 3. Three boards the tool produced

| Board | State |
|---|---|
| [harness-puck](harness-puck/REVIEW.md) | clean |
| [hydrate-coaster](hydrate-coaster/REVIEW.md) | clean |
| [terminal-keyboard](terminal-keyboard/REVIEW.md) | clean |

These are evidence, not deliverables — nobody is asking you to approve three
boards. They are here because they are the only way to see what the tool
actually emits, end to end, on real products we intend to build.

Each folder has gerbers, BOM, CPL, a KiCad project you can open and run your own
DRC on, and the full finding list. **Please run your own DRC.** Where your tool
disagrees with ours is the single most valuable thing this review can produce,
because it means one of us has a bug and we currently think it is not us.

### 4. Whether it holds for boards nobody has designed

Three good boards prove the tool works three times. Releasing it as an app is a
claim about the boards a stranger will ask for next week, and that claim rests
on two measured numbers rather than on these examples:

- **Composition closure** — the share of legal block combinations that build
  clean, each as a real board through the real pipeline. Every block passes
  alone; this asks whether they pass *together*.
- **First-build fab-ready rate** — the tool given briefs it has never seen, an
  empty directory each, no human turn.

Current values are in [`docs/lessons.md`](../docs/lessons.md) and the night logs, and move daily. The
question for you: **what would those numbers have to be before you would
release this?** We would rather have your threshold now than argue about ours
later.

### 5. The claim we make to users

The tool tells a user a board is "ready to order" when it has zero
error-severity findings and gerbers independently produced by `kicad-cli` from
the file KiCad checked. Is that bar high enough to put in front of someone who
cannot evaluate a board themselves? If not, what is missing from it?

## What we are not asking

- Not asking you to design anything, or to fix what you find. Finding it is the
  work; we will fix it.
- Not asking you to read our code. Judge the engineering, not the
  implementation — the pipeline, the agents and the tests are our problem.
- Not asking for a rubber stamp. **A block you reject is a better outcome for us
  than a block you wave through**, because the first costs a week and the second
  ships in every board a stranger orders.
- Not asking you to be in the loop afterwards. Once this is fixed and released,
  the tool runs without an engineer, which is the entire point.

## How to record a verdict

Fill in the sheets in place — each has a verdict block at the bottom — or reply
with the block or board id and your notes, whichever is faster. Findings routed
back to us as "must fix before launch" versus "should fix later" is the
distinction we most need.

## What we do with it

Everything you find becomes structural, not advice. A wrong value gets fixed in
the block and pinned with your name and the date. A rule you correct gets fixed
in the fab profile, which is the single place every board is graded against. A
defect class you point out that we cannot currently catch becomes a new check
plus a permanent entry in the failure corpus, paired with the legal geometry
just the other side of the line, so it cannot regress quietly.

If you would have done something differently but it is not a defect, say so
anyway. A preference an expert holds and a user will never know to ask for is
exactly what should become a default.
