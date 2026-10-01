# sg90-continuous-module — a 9 g "360°" continuous-rotation micro servo as a wheel motor

**Function:** a wheel. An SG90-class servo whose feedback pot is replaced by a trimmer (FEETECH FS90R
class, sold as "Servo SG90 quay 360 độ"): the pulse sets *speed and direction*, not angle. One per
wheel on a `servo-header` block (pin 1 GND, pin 2 V+ in the middle, pin 3 signal — never move V+).

**Pulse contract (FS90R product page, read 2026-10-01):** 50 Hz; **1.5 ms = stop** (the default rest
point, trimmed by the pot on the servo's underside); above the rest point → counter-clockwise, faster
as the pulse grows; below → clockwise. Full speed at 6 V: 130 RPM no-load. Both 5 V and 3.3 V logic
levels on the signal are accepted. 9 g, 22.5 × 12.1 × 23.4 mm, 250 mm lead with a JR plug.

**The stop point drifts per unit.** Two wheels never share one rest point: the firmware keeps a
per-servo stop offset in non-volatile storage and a command to set it (the Deck board did the same
for its encoder); a pet that creeps when told STOP has an uncalibrated servo, not a bug in the board.

**Current (FEETECH FS90 "Specification of Product" V1.0 §4-5, the same gearbox and motor):** stall
**700 mA @ 4.8 V, 800 mA @ 6 V**; idle a few mA. Two wheels stalled against a wall = 1.4–1.6 A — never
from a host USB port, never through a dev module's 5V pin. Give them their own 5 V rail from the
board's own input (power bank or supply), with the bulk capacitor sized from the stall current the
way the servo bench did (C ≥ I·dt/ΔV; 1000 µF for 4 servos was the 2026-09-30 answer) and the rail
copper on the `power` rule's 2 mm-class width.

**Boot rule:** no pulse until the host says so — the GPIO stays input/pull-down through reset, blank
flash and BOOTSEL, so a freshly flashed pet does not drive off the desk.

**Mounting:** standard SG90 body; wheel on the single-sided output shaft; the two servos sit mirror
image, so one wheel's "forward" is the other's reverse — the firmware mirrors one channel.

**Measured:** none on hardware; the 130 RPM and current numbers are the supplier's.
