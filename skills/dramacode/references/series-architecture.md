# Series architecture — episode counts, gates, and season pacing

**Load when:** planning any multi-episode series, choosing gate episodes,
setting series length for a market, or deciding where the season's big
reversals land.

## Format by market

| Market | Episodes | Episode length | Notes |
|---|---|---|---|
| China paid mini-program | 80-100 | 45-90s (ai-drama) | gates at fixed eps |
| 漫剧 (manju) | ~60 | 2-3 min (60-180s) | anime style, fast cutting |
| ReelShort-class overseas app | 50-100 | 1-3 min | gate placement varies by app |
| 红果 / free | flexible | 45-120s | no gates; ad-break tolerant instead |

Universal: 9:16, 1080×1920, H.264 MP4. Never upsample. (The research
notes 25/30fps platform norms; this pipeline's contract default is
**24fps** via `series.py` — keep one fps per series and move on.)

## Paywall gates (卡点)

Owned by `dramalib.tables.PAYWALL_GATES`; derive with `gate_plan()`:

- **China**: first gate ~ep 10 — and the HOOKS are loaded into eps 9-10,
  not into the gate episode itself. Then 20, 30.
- **Overseas**: first gate ep 5-12 (varies by app — treat as
  configurable, default 8), major gate eps 26-30 (default 28).
- **Free (红果)**: no gates. Every episode must instead tolerate ~15s ad
  breaks (`AD_BREAK_TOLERANCE_S`) — see
  `references/patterns/ad-break-tolerant-episode.md`.

The three drivers that make a gate convert (`PAYWALL_HOOK_DRIVERS`):
**climax, reversal, borderline-risqué** — the third is compliance-gated;
prefer the first two.

## Season pacing

- **Eps 1-10 — the golden window.** Protagonist + core conflict + the
  reason to watch, all established. This is where retention is won.
- **Eps 11-30 — main development.** Minor reversal every 5-10 eps.
- **Eps 31-80 — escalating climaxes + payoffs.** Major reversal every
  20-30 eps.
- **2-3 core reversals per series**, no more. Each core reversal should
  re-price every relationship in the show.

Constants: `dramalib.tables.SERIES_PACING`.

## Genre → arc

`trope_for_genre()` returns the genre's canonical beat pattern — it is a
SEASON shape as much as an episode shape. Examples:

- 赘婿/战神 (male-lead): humiliation → concealed identity → forced
  reveal → 打脸 cascade. The identity reveal is a core reversal; place it
  at a gate.
- 重生/复仇 (female-lead): death-or-betrayal → rebirth/awakening →
  preemptive strike → cascade. The awakening belongs in the golden
  window, not at ep 20.
- Overseas: werewolf/vampire (NA), billionaire/secret-heiress (SEA),
  revenge (universal). ReelShort adapts validated web-novel IP —
  when the user has no premise, propose a proven trope, not an original.

## Using the helpers

```python
from dramalib.helpers import gate_plan, episode_length_for
from dramalib.tropes import trope_for_genre

plan = gate_plan(market="cn", total_episodes=80)     # {'gates': [10, 20, 30], ...}
lo, hi = episode_length_for(format="ai-drama")       # (45.0, 90.0)
beats = trope_for_genre(genre="zhuixu")["beats"]
```

Record the resulting plan in the project's `spec.md` (`## Gate plan`) so
every later episode is written against it.
