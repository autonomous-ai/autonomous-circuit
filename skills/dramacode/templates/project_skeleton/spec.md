# Series spec — `<series title>`

Human-readable series intent. The agent reads this before touching code.
Fill in every `<...>`; delete sections that genuinely don't apply.

## Premise

<one-sentence logline: who wants what, who stands in the way, what's the
secret that detonates>

## Genre + market

- Genre: `<revenge | zhuixu | zhanshen | chongsheng | bazong | werewolf |
  billionaire | ...>` (must resolve via `dramalib.tropes.trope_for_genre`)
- Market: `<cn | overseas | free>` — decides the gate plan AND episode length
- Format: `<ai-drama | manju>` — keys `EPISODE_LENGTH_S`
- Style preset: `<photoreal-drama | manhwa | anime>`

## Cast (2-3 leads)

- `<id>`: <name> — <look, one line> — <voice key>
- `<id>`: <name> — <look, one line> — <voice key>

(Reference images: run the cast-book skill; 4-6 refs per character,
four-view sheet. Consistency is the #1 dropout driver.)

## Season arc

- Episodes: `<total>`
- Golden window (eps 1-10): <protagonist + core conflict + reason to watch>
- Development (11-30): <the middle game>
- Escalation (31+): <climax cascade + payoffs>
- Core reversals (2-3 per series): <ep ~N: what flips>, <ep ~M: what flips>

## Gate plan

From `dramalib.helpers.gate_plan(market=..., total_episodes=...)`:

- Gates at eps: `<[10, 20, 30] | [8, 28] | none>`
- Gate-1 episodes (`<eps 9-10>`) carry the loaded hooks
- Free market instead: every episode ad-break tolerant (beat every 20-30s)

## Episode-1 beat sheet

The beat law, applied (times for a `<length>`s episode):

| t | beat | what happens |
|---|---|---|
| 0-3s | hook | <the image that stops the scroll> |
| 3-10s | world | <conflict + relations + goal, all of it> |
| 10-30s | first reversal | <suppression → eruption> |
| ...every 20-30s | escalation | <the next emotional beat> |
| final 5-10s | cliffhanger | <cut at the peak, dead stop> |

## Rules

- Every duration comes from `dramalib.tables` — never guessed.
- Episode files define `gen_episode()` returning the envelope, with
  `validate()` asserts + `functional_warnings()` (see `episodes/ep001.py`).
- Never edit generated `.mp4` / `.srt` / `.episode.json` — edit the `.py`,
  re-run.
