# Native repair fixtures

`tiny/` is an original, synthetic 30 × 20 mm, two-net project authored for
this test suite. Four through-hole test points and two straight tracks put
SIGNAL across OTHER's pad, creating a deliberate short/clearance violation.
Moving SIGNAL above the pad fixes the defect without changing OTHER.
The schematic, project symbols and footprint are local; no network is needed.
It is a test board, not a fabrication design.

`native-route.ses` was captured on 2026-09-15 using native KiCad 10.0.5 DSN
export and Freerouting 2.4.1 (`-mp 5 -mt 1`). Trailing whitespace is removed.
The protected OTHER track is intentionally absent from the SES. Replaying it
checks that the native importer restores the original protected object and
keeps its UUID and geometry. Tests do not launch Freerouting or use a network.

The separate Desk Cube smoke script accepts a local release path, copies it
to a temporary directory and never commits or mutates that source design.
