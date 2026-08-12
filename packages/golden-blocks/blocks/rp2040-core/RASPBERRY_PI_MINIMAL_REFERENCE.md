# Raspberry Pi Minimal R3-S1 reference record

The RP2040/flash/decoupling placement and critical QSPI geometry used by this
block are adapted from Raspberry Pi Ltd's official **Minimal R3-S1** KiCad
design. This record pins the reviewed upstream input so a later edit cannot
quietly substitute a different reference board.

- Upstream archive: <https://datasheets.raspberrypi.com/rp2040/Minimal-KiCAD.zip>
- Retrieved: 2026-08-12
- Archive SHA-256:
  `8fdae5c1d3d8e58f43a45cd604ce9836b1ad4649f11eca4a9bea97eec6c2093a`
- Upstream board: `RPI-RP2040-MINIMAL_R3-S1.kicad_pcb`
- Upstream schematic: `RPI-RP2040-MINIMAL_R3-S1.kicad_sch`
- Upstream license-file SHA-256:
  `b01f852b57e955edeb4001c02fb3a204bd7309a19d8185a6312598f977d656cc`
- Preserved notice: [RASPBERRY_PI_MINIMAL_LICENSE.txt](RASPBERRY_PI_MINIMAL_LICENSE.txt)
- Preserved notice SHA-256 after repository newline normalization:
  `b7d06548be326cac80f52434578cc6a5e7c32e555619ee4aac3e2a03490a25a6`

The reference is a geometry and topology input, not an acceptance shortcut.
The adapted block still has to compile from the repository's pinned parts,
pass the complete independent copper/clearance/topology checks, route every
non-authored connection with accumulated prior copper, pass the bottom-face
transform regression, and survive the repository's KiCad and fabrication
packet gates. Differences in the repository footprint, refdes allocation, or
component values must be documented and rechecked rather than assumed safe
because the upstream board is manufactured.
