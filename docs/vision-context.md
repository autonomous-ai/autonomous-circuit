# Vision context (not v1 scope)

Recorded 2026-08-09 from Dee, so build decisions stay compatible. **None of this is in
v1 scope. v1 = Create, perfected.**

Autonomous TV ("Video", née Steve) is the **create** layer of a future **social network of short dramas**: anyone
watches, anyone creates, anyone remixes or extends a series. The short-drama market is
growing fast but supply-capped — a few studios produce everything. Video removes the cap:
a stay-at-home mom, a student in Brazil, a small artist team in New York can all make
dramas. A new form of entertainment.

The house pattern, third instance:

| | engine | creator tool ("Claude Code for X") | network |
|---|---|---|---|
| Hardware | CadQuery/cadpy | **Vibe** | **Panda** (panda-social-backend, panda-mobile) |
| Short drama | dramapy + video providers | **Video** (this repo) | *(later — do not build yet)* |

Design consequences for Create, today:

1. **Keep the publish seam.** The donor's `project_publish` → panda-social-backend flow
   and the cover-by-filename convention (`_review/_poster.png` here) are the attachment
   points the network will reuse. Don't design them away.
2. **Series must be portable/remixable objects.** A project dir (series.py + episodes/)
   is self-contained and copyable — that's the future "remix/extend a series" unit.
   The render cache and cast assets travel with it. Forklore/Storyforest (branching
   community episodes) is the adjacent concept for extend-lineage.
3. **Creator-grade defaults, not studio-grade knobs.** The target creator is a person,
   not a pipeline team — every decision the beat-law tables can make silently, they make.
4. **Watch-side artifacts** (episode gates, teaser cards, ad-break tolerance) are already
   modeled in dramalib tables so network monetization needs no re-authoring later.

Reference: the social stack lives in autonomous-ai private repos — panda-social-backend,
panda-social-cc-agent, panda-social-pi-agent, panda-mobile, panda-website. A contract
skim is archived in the org repo's project notes.
