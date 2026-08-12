# ldo-3v3 — audited protected 3.3V rail

**Function:** convert protected `net.V5` into `net.V3_3` with the exact Diodes
AP7361C-33E-13 (`C500795`) and two Samsung 10uF X5R 10V capacitors
(`C19702`). The selected fixed-output E package has four physical contacts:
pin 1 `VIN`, pin 2 `GND1`, pin 3 `VOUT`, and broad tab/pin 4 `GND2`.
It has no EN or NC pin, and its tab is never a 3V3 output.

**Status:** v2. Compile- and artifact-verified against tscircuit@0.0.2279 on
top and bottom; not hardware-verified (first article pending).

## Composition contract

| Net | Meaning |
|---|---|
| `net.V5` (`vinNet` overrides) | protected 5V input |
| `net.V3_3` (`voutNet` overrides) | regulated 3.3V output |
| `net.GND` | both pin 2 and broad tab/pin 4 |

U2.VIN reaches C2.pin1 through an authored 0.2mm, same-face, no-via branch.
U2.VOUT reaches C3.pin1 through an authored 0.8mm, same-face, no-via branch.
Each pad-edge gap is 0.525mm and each routed centerline length is 1.725mm,
below the 2mm block limit. Both physical regulator GND contacts and both cap
grounds terminate in the solved face pour.

For a board-owned V5 tree, set `externalInputPowerTrunkPort="VIN"` and attach
at `.<cin> > .pin1`; only the named V5 boundary is suppressed. For a
board-owned 3V3 tree, set `externalPowerTrunkPort="VOUT"` and start at
`.<u> > .VOUT`; only the named V3_3 boundary is suppressed. There is no TAB
alias. A cross-face board tree owns an explicit off-pad 0.8/0.5mm transition;
the block-local capacitor branches remain on the component face.

`layer="bottom"` mirrors local X and complements rotations. The compiled
bottom artifact is the exact X/layer transform of the top artifact: endpoints,
1.725mm paths, widths, cap locality, and both material GND contacts are
preserved.

## Audited operating envelope

- input operating range 2.2–6V; protected-USB verification uses 5.25V worst
  case and 3.3V output;
- product ceiling: **150mA continuous**, not the part headline current;
- maximum ground current: 0.08mA;
- AP7361C SOT-223 theta-JA: 110 C/W, conditional on the manufacturer land;
- at 150mA, 5.25V input, 60 C ambient: 0.29292W, junction 92.2212 C,
  leaving 32.7788 C to the 125 C design limit (required margin >=30 C);
- manufacturer requires at least 1uF input and 2.2uF ceramic output; the exact
  10uF X5R C19702 parts provide margin on both sides.

## Pinned parts

| Ref | Exact part | LCSC | Package | Note |
|---|---|---|---|---|
| U2 | AP7361C-33E-13 | C500795 | SOT-223 E | Extended/MSL1; revalidate live availability at order time |
| C2/C3 | CL10A106KP8NNNC, 10uF ±10%, X5R, 10V | C19702 | 0603 | Basic; audited input/output ceramic |

## Land and provenance

The exact C500795 EasyEDA record pins identity, pin numbering, and 3D model.
Its imported copper is intentionally replaced by Diodes DS37274 Rev. 5-2
(Oct 2020), page 21 recommended land, rotated into block-local X:

- three lead pads 1.20 x 1.60mm on 2.30mm pitch;
- broad GND tab 3.30 x 1.60mm;
- lead/tab row centers 6.40mm apart; total outer span 8.00mm.

The EasyEDA copper used 2.4649938 x 1.0500106mm leads at 5.715mm row-center
spacing and a 2.4649938 x 3.539998mm tab. Although it has more aggregate area,
it does not implement the manufacturer's recommended dimensions and therefore
cannot support the reviewed 110 C/W thermal model. Tests freeze both the
selected-part provenance and the intentional land delta.

The datasheet is still marked ADVANCE INFORMATION; lifecycle and orderability
must be revalidated for each purchasing run. Stock counts are deliberately not
part of this frozen contract because they are volatile.
