# Pattern: add a button

**Trigger:** "add a button", "another key", "a reset switch", "make it a 4-key
macropad".

**Why this exists:** buttons are the most-repeated edit on a board, and the two
mistakes (no pull-up story, and a grid laid out by hand) are both avoidable.

**Use the block:**

```tsx
import { SwTact } from "../blocks/sw-tact/sw-tact"

<SwTact name="SW1" signal="KEY1" pcbX={-6} pcbY={0} schX={-2} schY={0} />
<SwTact name="SW2" signal="KEY2" pcbX={6}  pcbY={0} schX={2}  schY={0} />
```

`sw-tact` is TS-1187A (LCSC C318884, a JLC **Basic** part — no feeder fee, so
adding keys is nearly free). Each instance is one switch to ground on its
`signal` net; the pull-up is the MCU's internal one, which you enable in
firmware. Do not add external pull-ups unless the user asked for a long cable
run.

**For a grid**, place on a regular pitch and say what the pitch is — the
enclosure needs it. 19.05mm is the standard keycap pitch; 9–12mm suits bare
tactiles under a printed cap.

**Pitfalls:**

- One net per key. Reusing a `signal` name silently wires two switches in
  parallel — every deterministic check passes and both keys do the same thing.
- Check the MCU has the pins free before adding keys; put the allocation in the
  plan rather than discovering it at build time.
- Buttons want silkscreen labels. An unlabelled 4-key board is a puzzle.
- Hot-swap sockets (MX/Choc) are **not** a block yet — tactile switches only.
  Say so rather than improvising a socket footprint.
