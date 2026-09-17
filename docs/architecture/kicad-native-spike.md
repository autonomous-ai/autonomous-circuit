# KiCad-native v2: repair spike

Status: implemented first slice, 2026-09-15. Base: main `8dbc95c` after PR #33.
This package runs beside circuitpy v1.8; the app still uses v1.

## Ownership

- KiCad PCB/schematic/project/rules and local libraries are the design source.
- product.json and parts.json, when present beside design/, enter the snapshot.
- kicadpy owns transactions and native tools. It does not import the v1 generator.
- Freerouting uses circuitpy's existing pinned subprocess launcher. Native DSN
  export and SES import belong to kicadpy; no Circuit JSON round trip.
- A versioned viewer adapter exposes UUIDs, geometry inventory and capabilities.
  It is not the current app renderer and cannot be written back as a board.

## Modules and transaction

project.py captures the input closure, hashes it, stores snapshots and serializes
cooperating writers with an OS file lock. worker.py runs only in KiCad's Python;
toolchain.py isolates it from the host Python. engine.py creates candidates,
sexp.py compares native objects, checks.py reads native ERC/DRC/parity reports.
cli.py exposes these operations as JSON tools.

`inspect/view → snapshot → apply/route → diff → check → commit → undo`

Snapshot directories are keyed by input checksum. Candidate IDs are separate
from revision hashes. Candidate files are never live inputs. Check refills a
candidate, saves it, then runs read-only DRC/parity and ERC against that revision.
Commit verifies report integrity, tool identity, current input revision and the
diff before atomically replacing the one mutable file: the PCB. Undo restores
its exact saved bytes, refusing changed dependencies. Undo does not claim a fresh
check. A crash before atomic replacement leaves the live PCB untouched; a crash
after replacement leaves the complete candidate PCB and the old snapshot.

The supported edits are replace_track (fixed endpoints/net/layer/width) and
set_width. Both require UUIDs, net names and a bounding region including copper
width. Locked copper cannot be edited. Native routing rips up selected copper,
protects the rest for export, imports the SES into the candidate, and restores
protected objects omitted from the session using the original native objects.
A router result outside the requested scope is refused.

Diff fingerprints complete native top-level objects, including footprint fields,
pads and outline; only serialization version/generator headers are excluded.
Zone fill polygons are separated from zone definitions. Fill changes require an
explicit list of zone UUIDs in scope and remain visible in the diff. This is
whole-zone permission, not a claim of polygon changes confined to the edit box.

## Acceptance evidence

The committed synthetic fixture deliberately shorts SIGNAL to OTHER's pad.
Tests detect and refuse it, then repair/check/commit/undo three times with exact
source checksums. They also cover stale revisions, candidate/report/snapshot
corruption, worker failure, dependency changes and native protected-SES replay.
KiCad-dependent tests skip when unavailable; skipped runs are not acceptance.

A separate local Desk Cube release smoke run on KiCad 10.0.5 inspected 85
footprints and 1,093 copper objects. Widening GPIO7 segment
`04d1cfdc-7fe7-4b76-a096-b318892e4dd5` from 0.15 to 3 mm produced five findings.
Restoring 0.15 mm produced zero ERC/DRC/parity findings, one changed segment,
zero other object/settings/dependency/fill changes and byte-exact undo. Existing
ignored-check lists were retained and recorded; zero findings does not mean all
possible rule categories were enabled. The source release was copied for edits.

Native Freerouting 2.4.1 also repaired the synthetic fixture with zero findings.
The captured native SES is replayed offline in tests; no routing network calls.

## Explicit limits and next slices

- macOS/Linux file locking; KiCad 10 CLI and pcbnew versions must match.
- Local project libraries/sheets only; missing/external table URIs are refused.
  External 3D models and environment-specific global library configuration are
  not certified by this spike. A complete portable dependency resolver is future work.
- Locks coordinate these tools, not an independently running KiCad GUI. Revision
  checks detect ordinary external edits; external writers must be closed during
  commit to exclude the narrow race between compare and atomic replacement.
- Snapshot/candidate retention is manual in this slice. No automatic pruning.
- All native findings, including warnings/exclusions, block commit in this spike.
  Ignored categories are exposed. Product engineering checks are not ported.
- Schematic writing, footprint moves, detailed zone/schematic rendering, app
  catalog/watcher/edit dispatch and full verifylib adapters are subsequent work.
- `kicadpy.publish --manufacturing` exports the revision-bound prototype packet
  (MANUFACTURING.md). The publisher writes `fab.ready` false; the app's native
  review loop sets it true only after a verified independent attestation for
  that exact source, and any republish resets it. `assemblyReady` and
  `hardwareTested` remain false: PCBA data completeness and hardware validation
  are independent statuses.
- Next: robust project dependency/tool provenance and concurrency, viewer adapter
  integration, engineering check coverage, then prompt-to-board and repeated v1/v2
  comparisons. The earlier proposals are historical inputs, not current contracts.
