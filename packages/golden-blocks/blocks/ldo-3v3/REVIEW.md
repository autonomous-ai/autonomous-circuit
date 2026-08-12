# Block sign-off — `ldo-3v3` v2 (AP7361C-33E-13)

**This block goes into every board a user generates that needs it**, unchanged —
the AI composes blocks, it never edits them. An error here would be repeated in
every product, so this independent datasheet review is required before release.

Source: [`ldo-3v3.tsx`](./ldo-3v3.tsx) · Contract: [`BLOCK.md`](./BLOCK.md)

## Check against primary evidence

Our source and block documentation can share the same error. Check these
against Diodes DS37274 Rev. 5-2, the exact C500795/C19702 records, and the
compiled top/bottom artifacts.

| # | Question | Verdict |
|---|---|---|
| 1 | Is `AP7361C-33E-13` the fixed-output E package, not the reversed ER variant? | pass / **fail** |
| 2 | Are physical contacts exactly VIN/GND1/VOUT/GND2, with broad tab/pin 4 on GND and no EN/NC/TAB-output alias? | pass / **fail** |
| 3 | Does copper exactly implement page-21 land: 1.20x1.60 leads, 3.30x1.60 tab, 2.30 pitch, 6.40 row spacing, 8.00 outer span? | pass / **fail** |
| 4 | Are C2/C3 exact C19702, 10uF ±10%, X5R, 10V, and same-face/authored within 2mm of VIN/VOUT? | pass / **fail** |
| 5 | Do both GND contacts make material contact with the solved face pour, without treating the tab as output? | pass / **fail** |
| 6 | Is the 150mA/60C/5.25V envelope backed by >=30C headroom under the audited 110 C/W land-conditioned model? | pass / **fail** |
| 7 | Is C500795 orderable and lifecycle-acceptable at the purchasing date, acknowledging its Extended status and ADVANCE INFORMATION datasheet? | pass / **fail** |
| 8 | Do top and bottom compiled/KiCad/DFM artifacts preserve this exact contract? | pass / **fail** |

## Anything you would have done differently

```
```

## Verdict

- [ ] **Approved** — safe to compose into user boards as-is
- [ ] **Approved with changes** — listed above, must land before release
- [ ] **Rejected** — do not release with this block in the catalog

Reviewer: ______________________  Date: ____________

---

## The block's own contract, for reference

See [`BLOCK.md`](./BLOCK.md). The review deliberately links to one source of
contract text instead of copying a second version that can go stale.
