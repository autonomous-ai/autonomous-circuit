# The safety envelope

Refused at planning time — in the plan, before any toolchain process runs, so a
refused design never becomes files on disk.

- **No mains voltage, ever.** Low-voltage DC, 24V or less, only.
- **Battery power only through a sealed, validated charge/protect block.** No
  such block exists yet, so the tool currently refuses battery designs outright.
  The slot is deliberately empty rather than filled with something plausible —
  lithium charging is a fire risk and we would rather refuse than guess.
- **Radio only as pre-certified modules.** No discrete RF design.

## Questions

| # | Question | Verdict |
|---|---|---|
| 1 | Is this envelope **tight enough** to put in front of a non-engineer? | yes / **no** |
| 2 | What should be **added** to it? | notes |
| 3 | Is anything here **too strict** — refusing work we could do safely? | notes |
| 4 | When a battery block does exist, what must it prove before it ships? | notes |

Question 4 is the one we would like answered before we build that block, rather
than after.
