# ad-cut-sheet (投流表)

**Trigger:** load when the user says "ad", "trailer", "promo", "hook clips",
"投流", "marketing cuts", "how do I promote this", "make clips for TikTok/
Reels", or when finishing a series/episode for release. Also load it by default
when packaging a series — every series ships with this.

## Why this exists (read once)

In short drama, **paid acquisition is 80-90% of the cost**, and it runs on
short ad-hooks cut from the episodes. The ad clip — not the app-store page — is
the real storefront: the algorithm shows a 15-30s hook, the viewer taps, and
lands on the paywall. So the deliverable is never just the episode; it's **the
episode + the ad-cuts that sell it.** A beautifully-made series with no liftable
hook is inventory that can't be marketed. Produce the cut sheet as a first-class
output, the same way you produce the board and the poster.

## The output: a 投流表 (ad-cut sheet)

A ranked list of **5-10 candidate hooks per series** (not per episode — pull the
best moments across the whole run). Each row:

| field | what it is |
|---|---|
| `hook_type` | which engine it fires (see the taxonomy below) |
| `source` | episode + shot id(s) the cut is lifted from |
| `window_s` | the in/out inside the episode — **15-30s**, tight |
| `key_line` | the one screenshot-worthy 金句 (the line people quote) |
| `first_frame` | the exact opening image — must shock at t=0, no setup |
| `emotion` | the feeling it promises (revenge / desire / injustice / awe) |
| `cta` | the button line ("Watch what happens next", "She had no idea…") |
| `market` | who it's cut for (localize the line + framing per market) |

Write it to `ad_cuts.md` (or a `## Ad-cut sheet` block in `spec.md`) at series
finish. When assembly can lift the windows, it exports the clips; until then the
sheet tells the editor exactly what to cut.

## Which beats become hooks

The peaks you already wrote ARE the ad-hooks — you don't invent new ones, you
*mark* them:
- the **cold-open hook** of ep 1 (`references/patterns/cold-open-hook.md`)
- each **face-slap / reversal** (`face-slap-cascade.md`) — the biggest 爽点
- the **gut-punch** (`emotional-core.md`) — the moment that aches
- the **gate cliffhanger** (`paywall-gate-episode.md`) — the strongest so far
- the **shock document / reveal** (a bank alert, a DNA result, a will)

Pick the 5-10 that land hardest and **vary `hook_type` across the set** — don't
ship five slaps; ship a slap, a betrayal, a reveal, a longing beat, a
cliffhanger, so the algorithm can find different audiences.

## Hook-type taxonomy (pick one per cut)

`public_humiliation` · `betrayal_in_progress` · `rebirth_jolt` · `ultimatum` ·
`secret_identity_irony` · `overheard_plot` · `disinherited` · `face_slap` ·
`shock_document` · `in_medias_res` · `direct_address_line` · `time_jump_reversal`
· `secret_baby` · `mistaken_identity` · `false_accusation`.

## The rules that make a cut convert

- **Open ON the shock, not before it.** t=0 is the slap / the line / the reveal.
  No logo, no establishing, no "meanwhile." The first frame is the hook.
- **Muted-legible.** Most viewers see it on mute in-feed — the caption + image
  must carry it; the `key_line` goes on screen as text (`emotion-to-action.md`
  "turn off the sound" test applies double here).
- **15-30s, one beat.** One hook per cut. End on the question, never the answer.
- **One 金句.** A single quotable line per cut, big on screen.
- **Localize per market** — same beat, rewritten line/framing (see
  `references/genre-playbook.md` "localization is rewrite"). US wants the revenge
  *follow-through*; don't ship a literal translation.

## The title (the ad above the ad)

The title is a compressed plot promise, not a name: `[relationship/role] +
[hidden reversal] + [possessive stakes]`, 4-8 words, highest-value word first
("The Double Life of My Billionaire Husband"). Generate candidates from the
premise with `dramalib.titles.title_candidates(genre=…, role=…, secret=…)` (fills
proven per-genre patterns) + the `TRIGGER_WORDS` bank; pick/edit one. See
`docs/hit-teardowns.md`.

## The sales-package (ships alongside)

A `sales-package.md` — the one-screen pitch a distributor or the algorithm needs:
logline (1 line), genre + market + 男频/女频, the fantasy it sells, the 3
strongest hooks (from the sheet), target audience, 2-3 comparable hits, and the
gate plan (`gate_plan()`). This pairs the *what to cut* (投流表) with the *why it
sells* (sales-package). Scaffold it with `dramalib.package.sales_package(genre=…)`.

## Pitfalls

- **Treating the episode as the deliverable.** Without ad-cuts there's no funnel;
  the series never reaches anyone. The cut sheet is not optional.
- **Hooks that need context.** If a cut only lands after you've seen earlier
  episodes, it's not an ad-hook — it can't convert a cold viewer.
- **All one flavor.** Five slaps find one audience; vary the hook types.
- **Burying the line.** If the 金句 isn't on screen as text, muted viewers miss
  the whole hook.
- **The clip over-promises.** The cut must be a real beat from the series, not a
  bait moment that never pays off — post-install churn punishes it.
