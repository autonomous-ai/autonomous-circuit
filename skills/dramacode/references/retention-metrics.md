# Retention metrics — what "bingeable" actually means, measured

**Load when:** reasoning about whether a series will *actually* hook an audience,
wiring analytics, or turning audience data into fixes. This defines the ground-
truth the whole platform optimizes toward: the metrics that operationally *are*
binge. The deterministic eval (`dramalib.evals`) is a proxy for these; when
the network ships and we have real numbers, these supersede the proxy as the
reward (flywheel.md loop #8). Backbone: `dramalib.metrics`.

## The metrics that define binge

Binge isn't a vibe — it's a funnel of behaviors, each measurable, each tied
to a craft dimension the tool controls. (Target bands are directional ranges from
the market study, `docs/short-drama-playbook.md` Part 3 — **targets to calibrate,
not our measurements.**)

| metric | what it measures | target | grades |
|---|---|---|---|
| `hook_retention_3s` | still watching at 3s | ≥ ~55% | hook_strength |
| `episode_completion` | finish the episode | ≥ ~60% | pacing_fit |
| `next_episode_start` | **start the next episode — the binge pull** | ≥ ~60% | cliffhanger_pull |
| `binge_depth` | consecutive episodes / session | ≥ 3 | bingeability |
| `d1_return` / `d7_return` | come back day 1 / day 7 | ~20-30% / ~8-12% | bingeability |
| `paywall_conversion` | pay at the gate | ~2-5% | wish_fulfillment |
| `ad_hook_ctr` | click the ad-cut feeding paid UA | — | clip_ability |

**The one to watch:** `next_episode_start`. Completion says the episode was fine;
*next-episode-start* says the cliffhanger worked and the binge loop closed.
It's the truest single proxy for "can't stop watching."

## The loop: real signal → dimension → fix

`dramalib.metrics.diagnose_metrics(observed=…)` maps each underperforming metric
to the binge dimension it grades and the specific rework:

```python
diagnose_metrics(observed={"next_episode_start": 0.2})
# [{"metric": "next_episode_start", "dimension": "cliffhanger_pull",
#   "fix": "cut on the peak, delete the resolution", ...}]
```

This closes the audience-reward loop even before the network exists: the contract
is ready, so the day we get real numbers they become the reward that drives the
variant-select (`rank_variants`) and the auto-rework — replacing the proxy. Low
next-episode-start → sharpen cliffhangers; low hook_retention → regenerate the
cold open; low paywall_conversion → the wish-fulfillment payoff isn't landing.

## Why spec it now (before we have the data)

Three reasons: (1) it names the **target** the whole platform optimizes, so every
other meta-element points the same way; (2) it makes the reward **swappable** —
the eval proxy and the real metric share the same dimension keys, so wiring the
network is a data change, not a redesign; (3) it keeps us **honest** — "bingeable"
becomes a number we'll be measured on, not a claim.

## Pitfalls

- **Treating the proxy as truth.** The eval reward is a stand-in; when real
  metrics disagree with it, the metrics win (and we recalibrate the proxy).
- **Optimizing completion over next-episode-start.** A satisfying, self-contained
  episode can complete well and still not pull the next tap — that's the opposite
  of a series. Optimize the *series* pull.
- **Vanity numbers.** Installs and views without return/conversion are UA spend,
  not binge. Retention + payment are the real signal.
