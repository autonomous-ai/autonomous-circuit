# Short-drama mega-hit teardown — the repeatable formula

What separates a mega-hit from an also-ran on ReelShort/DramaBox/短剧, from a
teardown of the biggest titles. These are the patterns to bake into the genre
packs, the hook library, the binge engine, and the paywall logic.

## The seven patterns (and where each lands in the tool)

1. **Open IN the injustice — the "dignity-theft" cold open (公开夺权).** The
   winning first shot isn't a generic slap; it's the lead being *publicly stripped
   of a specific named identity/right* by a named antagonist — divorce papers
   shoved across a table, "I, Alpha X, reject you," "you're fired, effective today
   you work for your rival," disinherited at the will reading. → new cold-open type
   in `dramacode/references/patterns/cold-open-hook.md`; canonical opening lines as
   templates.

2. **虐/爽 RATIO and PROXIMITY, not payoff count.** Hits *over-invest in the
   torment* — the free run can be almost pure humiliation-stacking — so the first
   big 打脸 detonates with 8 episodes of accrued pressure behind it. The binge lever
   is *debt accrued before release*, not more payoffs. → an binge-engine knob:
   grievance-heavy opening (small wins only), first MAJOR payoff withheld to the
   paywall.

3. **The DRAMATIC-IRONY GAP.** From ~ep 2 the *audience* knows the protagonist's
   hidden power/identity/wealth/foreknowledge; the on-screen tormentors don't.
   Every scene after is loaded — you watch *knowing the reckoning is coming*. The
   single most repeatable structural device. → an 8th force in the binge engine
   + a series-level flag.

4. **PEELED reveal (马甲 stack), not a single reveal.** Hidden-identity/revenge
   engines expose one "vest" at a time, each face-slap to a bigger audience:
   secret doctor → secret CEO → secret heir → the one everyone answers to. →
   satisfaction-ladder = an ordered stack of escalating reveals, not one.

5. **Titles are a compressed plot promise, not a name.** `[relationship/role] +
   [hidden reversal] + [possessive stakes]`: "The Double Life of My Billionaire
   Husband," "Never Divorce a Secret Billionaire Heiress," "Fated to My Forbidden
   Alpha." → title generator = subject+secret+stakes template.

6. **Paywall bound to the craving peak, not an episode number.** The free run
   (~ep 8-12) maximizes accrued grievance and stops **one beat before** the first
   series-defining face-slap. You pay at maximum owed catharsis. →
   `paywall_ep = first_major_payoff_ep − 1`, ending on the cliff.

7. **The self-insert wound is demographically targeted — that targeting is the
   growth lever.** US werewolf/billionaire: "the overlooked woman is secretly
   destined/chosen." CN 2024 breakouts: flash-marry-into-wealth for the *older*
   overlooked self-insert (闪婚豪门), rebirth-to-fix-a-ruined-life. → demographic
   self-insert as an explicit genre-pack parameter + two new engines
   (flash-marry-senior, rebirth-homemaker).

## Encodable checklist (baked in as we go)

- [x] Dramatic-irony gap + 虐/爽 ratio → `binge-engine.md` (force #8 + the ratio knob)
- [x] Dignity-theft cold open + canonical opening lines → `patterns/cold-open-hook.md`
- [x] Paywall bound to craving peak (`paywall_ep = first_major_payoff_ep − 1`) → `patterns/paywall-gate-episode.md`
- [x] Peeled-reveal (马甲 stack) satisfaction ladder → `patterns/face-slap-cascade.md`
- [x] Demographic-targeted genre engine `flashmarry` (闪婚豪门) → `TROPE_TABLE` (rebirth already = `chongsheng`)
- [x] Title = subject+secret+stakes generator -> `dramalib.titles.title_candidates` + `patterns/ad-cut-sheet.md`
- [x] NA revenge-follow-through localization → `genre-playbook.md` ("localization is rewrite")

## The NA localization rule (already doctrine, reinforced)

NA audiences demand active revenge **follow-through**, not the CN silent-suffer/
win-back arc (追妻火葬场). When `market=NA`, convert "endure then be chosen" into
"strike back visibly." (See `genre-playbook.md` "localization is rewrite.")
