# paywall-gate-episode

**Trigger:** load when the user says "paywall", "gate", "卡点",
"monetization", "which episode do we charge from", or when planning
episodes 9-10 / 5-12 / 26-30 of a paid-market series.

## Why this exists

A gate episode is where the platform starts charging — and the episodes
**before** it are where the conversion is actually won. The China
pattern: first gate ~ep 10, with the heaviest hooks loaded into eps 9-10;
then gates at 20 and 30. Overseas apps gate earlier and variably (first
gate ep 5-12), with a major gate at 26-30. The three drivers that make a
gate convert: **climax, reversal, borderline** — the third is
compliance-gated, so engineer the first two.

A gate episode ends on the series' strongest cliffhanger so far, and the
episode after the gate must repay the payment within its first 10s.

## Use the helper

```python
from dramalib.helpers import gate_plan
from dramalib.tables import PAYWALL_GATES, PAYWALL_HOOK_DRIVERS

plan = gate_plan(market="cn", total_episodes=80)
# {'gates': [10, 20, 30], 'ad_break_tolerant': False}
```

Write the plan into `spec.md` (`## Gate plan`) at series creation, then
mark gate-adjacent episodes when writing them: eps `gate-1` and `gate`
get hero-shot budgets and a core-reversal beat; ep `gate+1` opens on the
payoff, not a recap.

## Pitfalls

- **Hooks in the gate episode instead of before it**: viewers who
  haven't paid never see them. Load eps 9-10, charge at 10.
- **Gate on a quiet episode**: a gate without a climax or reversal
  converts nobody — move the gate or move the reversal.
- **Hardcoding gate numbers per market from memory**: overseas first
  gates vary by app — `gate_plan()` defaults to 8, and the number is
  configurable; state it as an assumption the user can correct.
- **Free-market series with gates**: 红果 has none — the ad-break
  pattern applies instead (`references/patterns/ad-break-tolerant-episode.md`).
- **Post-gate letdown**: the first paid episode must be the strongest
  episode so far, or refunds and churn follow.

## Bind the gate to the craving peak, not a number (mega-hit rule)

`gate_plan()` gives a market-typical episode number, but the biggest hits place
the paywall by *craving*, not calendar: the free run maximizes accrued grievance
(虐) and stops **one beat before the first series-defining face-slap** — you pay at
maximum owed catharsis, zero 爽 spent. Rule of thumb:
`paywall_ep = first_major_payoff_ep − 1`, and end that free-run finale on the cliff
right before the payoff. So: schedule the first big 打脸 first, then set the gate
one episode earlier — don't pick the gate number and hope a payoff lands near it.
(See `docs/hit-teardowns.md` and the 虐/爽 ratio in `references/binge-engine.md`.)
