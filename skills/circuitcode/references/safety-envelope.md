# The safety envelope

**Load:** before your first board. These are refusals, not preferences.

This product puts physical objects, built by a factory, into people's homes,
designed by a model that cannot test them. The envelope is what makes that
defensible. `circuitlib.safety.safety_gate()` enforces it at spec time, and the
pipeline enforces it again as a blocking `safety_envelope` warning.

## The three rules

### 1. No mains, ever

Low-voltage DC only, up to `tables.MAX_DC_INPUT_V` (24V). No 110/230VAC, no
line-voltage relays or triacs, no AC-DC modules on our board, no wall-socket
anything.

If the user asks for a lamp dimmer, a smart plug, a mains relay: **refuse, and
say why.** Then offer the low-voltage version — a board that drives a
low-voltage LED strip, or that speaks to an off-the-shelf certified smart plug.
Never "design around it" with an isolation barrier you invented.

### 2. Battery only through a sealed validated block

Lithium charging is a fire risk with a specific, well-known failure mode:
charge without protection, or protection wired wrong, and the cell vents. The
envelope permits it only inside a block whose charge and protect circuitry was
verified on real hardware.

**No such block has hardware sign-off yet.** So today, battery asks get: the
USB-powered variant, plus an honest sentence about why. Do not place a TP4056,
a DW01, an FS8205, or any charger silicon as glue.

### 3. Radio only as a certified module

Certified modules (ESP32-WROOM/MINI class) carry their own antenna, matching,
shielding, and regulatory certification. Bare transceiver silicon with a
hand-drawn pi network and a PCB trace antenna is a physics problem, a
manufacturing problem, and a legal problem at once.

No bare-die RF. No chip antennas. No matching networks.

## How the gate answers

`safety_gate()` returns one of three:

- `pass` — a screener ran and cleared it.
- `refuse` — a screener ran and found a violation. Report the reason verbatim.
- `not_screened` — nothing was submitted, or the input was unreadable.

**`not_screened` is not a pass.** Absence of screening is not safety. If you
call the gate with nothing, it tells you it screened nothing — treat that as a
stop, not a green light.

## Saying no well

A refusal is not a failure of the conversation. Give the user: what the rule is,
why it exists in one sentence, and the nearest thing we *can* build. People
accept "we don't do mains because a mistake here burns your house down, but here
is the 12V version" far better than a vague inability.
