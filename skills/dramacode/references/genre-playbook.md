# Genre playbook — the nine engines and how to run each

**Load when:** the user names or implies a genre ("a werewolf romance", "a
CEO drama", "revenge", "she's the mistreated daughter-in-law"), when picking
which trope skeleton to instantiate, or when a draft is structurally fine but
generic — the wrong genre's beats are firing. `dramalib.tropes.trope_for_genre`
resolves a genre string to its beat spine; this doc is the *why* behind each
spine and the craft that makes it land.

## The one law under all nine: familiar shape, fresh skin

Short-drama audiences don't want novelty — they want the **familiar fantasy
delivered fresh**. Pick the engine that matches the fantasy, run its mandatory
beats, and reskin the surface. A genre is not a setting; it's a **promised
emotional payoff** and the beat ladder that delivers it. Ship the payoff the
genre promises or the audience feels cheated, however good the writing.

## 男频 / 女频 — decide this first

Every genre skews to one of two core fantasies. Set it before the beats.

- **女频 (female-freq):** the fantasy is being **cherished + vindicated** —
  love, status, and revenge together. Emphasize the bond, the longing, the
  humiliation-then-being-chosen, the rival's defeat.
- **男频 (male-freq):** the fantasy is **respect, power, dominance** — the
  dismissed nobody revealed as the strongest in the room, the adoring crowd,
  the enemy who kneels. Emphasize the reveal, the reversal of contempt, the
  scale of the win.

The same premise runs differently on each track. "Hidden identity" female-freq
is *he cherishes the woman everyone dismissed*; male-freq is *they mocked him,
now they kneel*. Each `TROPE_TABLE` entry carries an `audience` tag — honor it.

## The nine engines

Each drops straight onto a `TROPE_TABLE` key. Format: **core fantasy ·
archetypes · mandatory beats · signature tropes · title shape.**

### CEO / billionaire romance — `bazong` (霸总), female
- **Fantasy:** chosen and cherished by an all-powerful, cold, obscenely rich
  man who is soft only for you.
- **Archetypes:** cold domineering CEO; ordinary kind heroine (Cinderella);
  scheming other-woman (白莲花); overbearing mother-in-law; loyal assistant.
- **Beats:** collision meeting → forced proximity → she doesn't know he's the
  CEO → rival's jealousy → separating misunderstanding → possessive rescue →
  devotion revealed → public claiming.
- **Tropes:** contract marriage, "you belong to me", wall-slam (壁咚), black-card
  rescue, "she's my wife — apologize."
- **Titles:** "The CEO's Contract Wife", "Married to the Billionaire's Secret Heir".

### Revenge / counterattack — `revenge` / `fuchou` (复仇), all / female
- **Fantasy:** those who wronged you grovel; you rise from ruin to dominance.
- **Archetypes:** wronged protagonist (betrayed spouse / framed heir / discarded
  first wife); the backstabbers; a hidden ally.
- **Beats:** the wrong (framing/betrayal/ruin) → rock bottom → the turn (hidden
  power / benefactor / return after years) → systematic dismantling of each
  enemy → escalating face-slaps → final public downfall → vindication.
- **Tropes:** "you'll regret this", return after N years transformed, secret
  identity as the new boss, public fraud-exposure.
- **Titles:** "Back from the Brink", "The Divorced Heiress's Comeback".

### Werewolf / Luna / fated-mates — `werewolf`, female (the dominant NA paranormal)
- **Fantasy:** you are destined, special, fought over by a powerful alpha;
  rejection turns to obsessive regret.
- **Archetypes:** rejected she-wolf/omega heroine; arrogant-then-obsessed alpha;
  Luna rival; packmaster; second-chance mate; hidden royal lineage.
- **Beats:** the rejection ("I, Alpha X, reject you") → exile/transformation →
  discovery of hidden power / second mate / royal blood → alpha's regret &
  pursuit → mate-bond pull → reveal of true rank → triumphant return.
- **Tropes:** "I reject you as my mate", the mate bond, heat/marking, the Moon
  Goddess, pack politics, hidden pregnancy with alpha twins.
- **Titles:** "Rejected by My Alpha Mate", "Fated to the Cursed Alpha King".

### Mafia / possessive male — `mafia` (黑帮), female
- **Fantasy:** a dangerous, morally-gray, powerful man who will burn the world
  for you.
- **Archetypes:** mafia boss/don; innocent heroine pulled into his world; rival
  family; loyal enforcer.
- **Beats:** forced captivity/protection → danger from rivals → possessive
  claiming → a debt/deal that binds them → violence in her defense → softening.
- **Tropes:** "you're mine now", kidnapping-to-love, protective violence, forced
  marriage to settle a debt.
- **Titles:** "The Mafia Boss's Innocent Bride", "Captive of the Ruthless Don".

### Time-travel / rebirth / regret — `chongsheng` (重生), female (huge in CN)
- **Fantasy:** a do-over — you know the future, so you win this time and punish
  those who wronged you.
- **Archetypes:** reborn protagonist (dies, wakes in the past); future betrayers
  now unaware; the person she'll save or avoid.
- **Beats:** death/betrayal → rebirth to a key earlier moment → foreknowledge
  reverses a specific past humiliation → pre-empt the betrayal → change fate &
  love → punish enemies "before they do it."
- **Tropes:** "I lived this life before", foreknowledge advantage, saving the
  person she failed, avoiding the ruinous marriage.
- **Titles:** "Reborn to Take Revenge", "Reborn: The Heiress Returns".

### Hidden-identity / secret royalty — `zhuixu` (赘婿, male) · `billionaire` (overseas)
- **Fantasy:** everyone underestimates you; you are secretly the most powerful
  person in the room; the reveal is the payoff.
- **Archetypes:** the "trash" protagonist (poor son-in-law / janitor / nobody);
  arrogant mockers; the few believers.
- **Beats:** establish the mockery → small hints of power → escalating reveal of
  each "vest" (马甲 — secret doctor, war god, richest man) → each reveal
  face-slaps a bigger group → final total-identity reveal.
- **Tropes:** "the son-in-law is actually the king", peeling secret identities
  one at a time, the summons, the war-god/genius-doctor/richest-man reveal.
- **Titles:** "The Almighty Son-in-Law", "Hidden Billionaire".

### Rags-to-riches / sudden wealth — `riches` (逆袭暴富 / 神豪), all (神豪 male-skew)
- **Fantasy:** sudden wealth/power and the respect and revenge it buys.
- **Archetypes:** broke protagonist; the windfall (inheritance / system / skill
  / backer); the doubters.
- **Beats:** poverty & humiliation → the windfall → strategic rise → buying back
  dignity → outshining those who looked down.
- **Tropes:** surprise inheritance, "I'll buy the whole company", the net-worth
  reveal, the luxury flex.
- **Titles:** "From Rags to Billionaire", "The Broke Heir's Inheritance".

### Contract / fake marriage — `contract` (契约婚姻), female
- **Fantasy:** forced closeness becomes real love; safety + status via the deal,
  then genuine devotion.
- **Archetypes:** two reluctant partners; the reason for the deal (visa /
  inheritance clause / business / protection / revenge on an ex); a jealous
  rival who makes it real.
- **Beats:** the deal & its rules → forced cohabitation → rule-breaking (real
  feelings) → rival threatens the deal → "it was supposed to be fake" crisis →
  real confession.
- **Tropes:** "only one bed", rules on paper broken, jealousy exposes feelings,
  the contract expiry as a ticking clock.
- **Titles:** "Fake Marriage to the CEO", "Our Contract, His Real Love".

### Daughter-in-law / family drama — `inlaw` (婆媳), female (older; strong SEA/LATAM)
- **Fantasy:** the mistreated wife/daughter-in-law is vindicated; the cruel
  family eats their words.
- **Archetypes:** virtuous mistreated wife; tyrannical mother-in-law; useless/
  cheating husband; scheming sister-in-law; the wife's hidden backer.
- **Beats:** the mistreatment → the wife's secret worth revealed → the family's
  dependence on her exposed → role reversal → the family grovels.
- **Tropes:** "you were never good enough for this family" → she's the real
  power; the divorce that ruins them; the hidden rich family.
- **Titles:** "The Underestimated Daughter-in-Law", "Mrs. CEO in Disguise".

## Sub-genre overlays (modifiers, not engines)

Stack these on any of the nine, don't treat them as separate spines: sweet/dote
(甜宠), abuse-then-sweet (虐恋), system/LitRPG (系统), apocalypse (末世), historical/
palace-intrigue (宫斗), genius-doctor (神医), campus/youth. They flavor the surface;
the engine underneath is still one of the nine.

## Localization is rewrite, not translation

Same emotional engine, swapped skin per market — and the *beats* change, not
just the words:
- **NA/US:** werewolf/fated-mate, billionaire/CEO, mafia, second-chance romance,
  revenge. NA wants the **revenge follow-through** after the reversal — not the
  Chinese silent-suffer/win-back arc (追妻火葬场).
- **SEA / India:** family & in-law melodrama, romance, revenge (collectivist,
  family-centric).
- **LATAM:** telenovela DNA — betrayal, secret parentage, revenge, class-crossing.
- **CN:** rebirth, revenge, 婆媳, 战神/赘婿 (male), 甜宠.

Even props flip: US stocks green = up, China red = up. When you localize, port
the *fantasy*, then rebuild the beats to the market's expectation — a literal
translation of a CN hit reads wrong overseas.

## Pitfalls

- **Setting mistaken for genre.** "A drama set in a hospital" is not a genre;
  the genius-doctor *reveal engine* is. Pick the payoff, not the backdrop.
- **Wrong track.** Running a 男频 reveal ladder on a 女频 premise (or vice-versa)
  makes it feel off even when every scene is competent. Set audience first.
- **Skipping the grovel.** Every revenge/hidden-identity/in-law engine owes a
  visible comeuppance (the enemy kneels/apologizes). Cutting it to seem
  "classy" kills the 爽 the genre promised. See
  `references/patterns/face-slap-cascade.md`.
