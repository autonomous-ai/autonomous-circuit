# Getting real video: providers and keys

Without a key, every shot renders through the **mock** provider — deliberate
placeholder gradients + synthesized audio so the whole pipeline runs with zero
GPUs and zero spend. Real dramas need exactly one hosted-API key.

## Paste a key

Create `~/.autonomous-video/keys.env`:

```
# any one (or more) of:
FAL_KEY=...                # https://fal.ai/dashboard/keys
MINIMAX_API_KEY=...        # https://www.minimax.io/platform  (native audio+dialogue)
DASHSCOPE_API_KEY=...      # https://modelstudio.console.alibabacloud.com (Qwen/Wan)
```

Then pick the provider in **Settings → Render provider** (or set
`VIDEO_PROVIDER=fal|dashscope|minimax`). Existing episodes re-render on the
next build turn; unchanged shots re-render only when the provider changes
(the render cache is keyed by provider+model).

## Which key first

| Provider | What you get | Sound | Rough cost, 60s episode* |
|---|---|---|---|
| **minimax** | Hailuo — strongest single choice: video **with native dialogue audio** in one pass | native | ~$2–6 |
| **fal** | Wan 2.2 and friends, fastest signup, pay-as-you-go | silent (animatic voices fill in) | ~$1–5 |
| **dashscope** | Alibaba Model Studio (Wan 2.x line; 2.5+ has audio) | model-dependent | ~$1–8 |

*15 shots × ~4s at published per-second prices, one take per shot; notes/rerolls
add proportionally. All three implementations are network-untested until the
first live smoke — expect one round of API-shape fixes on first use (marked
`TODO(verify-live)` in `packages/dramapy/src/dramapy/providers/`).

## No key? Animatic mode

The mock provider doubles as **animatic mode**: motion (slow push-ins), film
grain, style palettes, burned subtitles, BGM, and spoken dialogue via macOS
built-in TTS — instant and free. Use it to lock story, beats, and pacing;
switch the provider to render the real footage from the same episode source
(only the provider changes; the render cache re-renders every shot once).
