# The rules the tool enforces

Read live from `circuitpy.fab.get_profile('jlcpcb')`. These are the numbers every board is graded against — if one is wrong,
every board the tool produces is graded against a wrong rule, including boards
nobody has designed yet.

**Please check these against JLCPCB's published capability table**, not against
our documentation.

| Rule | Value |
|---|---|
| `drc_tolerance_mm` | 0.01 |
| `iou_error_below` | 0.5 |
| `iou_info_below` | 0.85 |
| `iou_warning_below` | 0.65 |
| `min_board_mm` | 3.0 |
| `min_clearance_mm` | 0.1 |
| `min_edge_clearance_mm` | 0.2 |
| `min_mask_sliver_mm` | 0.2 |
| `min_npth_to_copper_mm` | 0.2 |
| `min_pth_annular_mm` | 0.2 |
| `min_pth_drill_mm` | 0.3 |
| `min_pth_to_copper_mm` | 0.28 |
| `min_silk_line_mm` | 0.15 |
| `min_silk_text_mm` | 1.0 |
| `min_trace_mm` | 0.1 |
| `min_via_annular_mm` | 0.075 |
| `min_via_diameter_mm` | 0.3 |
| `min_via_drill_mm` | 0.15 |
| `min_via_to_copper_mm` | 0.2 |
| `standard_thickness_mm` | 1.6 |
| `warn_clearance_mm` | 0.127 |
| `warn_edge_clearance_mm` | 0.3 |
| `warn_power_trace_mm` | 0.5 |
| `warn_pth_to_copper_mm` | 0.35 |
| `warn_trace_mm` | 0.15 |
| `warn_via_annular_mm` | 0.1 |
| `warn_via_diameter_mm` | 0.45 |
| `warn_via_drill_mm` | 0.3 |

Two-band by design: `min_*` blocks a board from being called orderable, `warn_*`
is a preference we surface but do not enforce. If you think something currently
in the warn band should block — or the reverse — that is exactly the kind of
call we want from you.

One we already know is subtle: a copper pour cuts a 32-sided polygon around each
hole, so a nominal 0.2mm margin measures 0.1976mm at the chord midpoints and the
fab's own DRC calls it a violation. The tool now derives a safe default from the
segment count. Please sanity-check that reasoning.
