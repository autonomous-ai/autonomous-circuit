# Case assembly and print handoff

Read before designing or repairing a case. Apply alongside AGENTS.md's Case contract and the
tile's checks. These rules came from a printed case with tight module mounts, inaccessible
fasteners, difficult joint insertion and coupons mistaken for assembly parts. A passing CAD
report did not prevent those failures. Do not turn that run's part counts or dimensions into
universal design requirements.

## Moving designs: mechanism first, shell second

- Establish the structural layout and how devices are mounted before detailing cosmetic covers.
  Identify the fixed base, each moving group, its axis/pivot, the actual actuator-to-horn-to-load
  connection and its support. Prefer one actuator per requested axis and direct stock interfaces
  when compatible with the owned hardware and required control. Added linkages need the existing
  function-based justification; do not make a three-piece kit a target in itself.
- Keep stationary electronics/connectors accessible and moving electronics on their own carrier
  where the product calls for it. Route the real flexible harness between groups with slack and
  restraint at both ends; check combined motion. Do not run a board, carrier plate or taut bundle
  through a joint's travel. Preserve the native board contract; a reference image does not
  authorize moving footprints, changing GPIOs or silently replacing a working firmware variant.
- Work in this order: structural layout and device mounts → accessible subassembly insertion,
  retention and motion checks → attach shells → repeat complete-assembly motion/wire checks.
  A structural shell/base can serve as the mount; do not invent a separate frame for a static
  product or one that mounts devices directly. The guide must show how to inspect the mechanism
  before closing removable covers, then how those covers fasten to its structure.
- For a repair, preserve the physically working baseline and test one changed subassembly first.
  Supply a small test kit containing the real mating features, fasteners, device/horn seats and
  relevant cable passage in the intended print orientation. A hole-only gauge cannot establish
  assembly or rotation. Clearly identify test-only pieces and unchanged parts to reuse; do not
  request a complete reprint for an untried joint. Record CAD checks separately from the person's
  unpowered dry-fit result; an unavailable physical test stays pending without inventing a pass
  or changing native fabrication/export gates.
- Treat reference photos as evidence of visible arrangement and assembly access only. Separate
  observations from assumptions about hidden bearings, fastening, dimensions, motor variant,
  feedback or full travel. Verify parts from supplied labels/measurements and authoritative
  references, applying the existing GIẢ ĐỊNH policy when unknown. A continuous-rotation servo
  does not gain position feedback from a simple direct coupling or a timed command. Do not add
  unrequested features from the image. If the person asks only to inspect/discuss and is sending
  more references, record conclusions and wait for a design request before modifying their run.

## 1. Resolve hardware and choose a mechanism

- **Default to simple, rugged and serviceable.** The person should be able to print, assemble,
  understand and repair it with ordinary tools. Start with a direct coupling, open bracket or
  simple pivot using stock hardware; choose the fewest practical parts and fastener types.
  Design around the declared print and measurement uncertainty, not perfect dimensions or
  repeated fine adjustment. Keep fasteners visible/reachable and replacement parts easy to print.
- Do not invent cams, gear trains, stacked linkages, custom springs or delicate catches merely
  to make the mechanism clever. Before adding one, name the required function, explain why the
  simpler option cannot meet it, and account for the extra parts, fit sensitivity and assembly
  work. If it adds no necessary function, remove it. Simplification must preserve the user's
  requirements; low part count alone does not excuse unreliable or unsupported operation.
- Start with supplied photos, owned parts and authoritative references. Record module envelope,
  connectors, shaft/horn interface, screw diameter, available length, head shape and nut/tool
  space. A screw of the right thread can still have a head that will not seat. Preserve usable
  factory horns, screws and mounts when that reduces assembly work.
- For unknown clones, mark the reference and dimensions **GIẢ ĐỊNH**. Use open/adjustable mounts
  and a small fit coupon instead of a tight nominal pocket. Proceed on the reference; do not
  ask a nontechnical person to identify an engineering detail they cannot know.
- Compare mechanisms by required function, printed pieces, purchased hardware, assembly steps
  and maintenance. Prefer fewer, accessible parts; justify added cams/linkages. Preserve the
  requested motion and control behavior when simplifying. Check actuator/control compatibility;
  a displayed CAD angle or a timed motor pulse does not establish actual angle feedback.
- Declare power-off assembly, initial centering and any required sensing/limits in the guide and
  firmware. Do not silently add unowned hardware or claim control the installed actuator cannot
  provide. Follow the existing assumption policy and keep untested functions explicit.

## 2. Prove a person can assemble and service it

- **Secure the entire attachment chain: device → mount/frame → shell.** In the assembly guide,
  name each connected pair, its seat, restraint/fastener, installation tool and removal method.
  Check that fixed parts cannot unintentionally slide, pull out or twist; identify the axes
  deliberately left free at moving joints. A separate frame may be unnecessary when the shell
  carries a device directly, but every device still needs a complete, explicit attachment.
- Verify shell-to-frame retention separately from front-to-back shell closure. A smooth locating
  peg is not a pullout lock. A shell sandwich can retain a frame only when its seating faces,
  clearances and closure restraint are actually checked; do not assume the frame stays put
  because the shell snaps shut. Show how parts are held while the person assembles/closes it.
- Preserve the device mounts that already worked in a real print. Do not replace TFT screw
  seats, edge clamps or rigid servo retention with unspecified ties/friction merely to lower
  the STL count. Use a supported pocket and removable clamp/lid where appropriate; account for
  device-body clearance, connectors and tool reach. Unknown dimensions remain assumptions, not
  fabricated measurements. Ties/foam may supplement retention; do not claim untested grip.
- **Eliminate reported fragile features.** Repeatedly broken small tabs, thin prongs, narrow
  necks and long unsupported arms are repair findings. Prefer broad supported seats, short
  load paths, braced roots and ordinary accessible fasteners. Evaluate material, print direction,
  layer bonding and assembly deflection before relying on a feature; no universal minimum wall
  or material swap proves strength. Validate the changed joint with a representative test print
  before replacing the complete kit, and preserve usable printed parts.
- Write the numbered assembly sequence before finalizing geometry. Give every physical part a
  stable ID matching the manifest, file names, print list and overview labels; place a small ID
  on printed parts where it will not affect a mating surface. Describe orientation from the
  person's view, with a per-step camera, highlighted part and insertion arrow.
- Check the path from outside into the assembled position, against everything already installed
  at that step. Check removal/service too. State the sampled path and its coverage; samples do
  not prove unchecked motion between them. Do not teleport parts into closed forks or assume
  rigid plastic can spread without checking the material and deflection.
- Check tool access to the real screw head, insertion length and nut counterhold, with the
  actual head/driver envelope. Account for fingers and wire placement where relevant; record
  access not modeled. A clear screw axis alone is not proof of tool access. If a later part
  blocks a screw, change the order and show that order explicitly, or redesign the joint.
- A snap needs its mating male/female features, lead-in, deflection space and a reachable release
  path. Test the mating pair in the intended material and print direction before relying on it.
  A cosmetic bump is not a validated latch; do not use glue as an undocumented repair.
- Route real wire bundles and connectors during the sequence, including strain relief and the
  checked motion envelope. Keep the current assembly guide linked to `firmware/WIRING.md`.

## 3. Make coupons impossible to confuse with assembly parts

- Use manifest role `coupon`. Physically mark each coupon `TEST ONLY` or an equally clear label
  in the user's language, away from measured/mating surfaces. Derive its mating geometry from
  the final model, including the matching half for a latch or joint.
- Explain what it tests, which real part goes where, insertion direction, what a pass looks
  like, what to change after failure, and that it is set aside after the test. Say explicitly
  **do not assemble or glue this into the product**. A file name alone is insufficient.
- Put coupons in a separately named TEST ONLY slicer project. The prepared production project
  excludes them; the legacy generic Print all export may still include all printable manifest
  entries. Exclude coupons from the assembled model, assembly count and product assembly steps.

### Ready-arranged slicer projects

- After **every build and final repair**, create/update a native slicer 3MF. Automatically orient
  to the declared print intent, ground each object and arrange on the owner's actual bed-sized
  plates; add plates on overflow. Include edge, brim and inter-part clearance, excluded bed areas
  and any needed support clearance. Do not scale a part to make it fit or use a huge virtual bed.
  Preserve the user's printer/nozzle/material/process choices. Use a named reference profile and
  label GIẢ ĐỊNH if unknown; do not ask the owner technical setup questions.
- Keep fixed/moving subassemblies identifiable, account for quantities with distinct manifest
  instances, and deliver a separate TEST ONLY coupon project. Keep a repair/subset project for
  already-printed parts when useful; the full production project must cover all current printable
  non-coupon manifest instances, including identical copies. Do not silently omit items.
- Use the slicer's native arrange/export path when available (for Bambu, an installed CLI may
  arrange and export without sending a print). Reuse project-local generators and verify against
  current STEP/STL and `printRotateDeg`. Check each plate for overlap, out-of-bed parts, floating
  geometry and unexpected supports; inspect thumbnails when available. Retain real slicer
  warnings and state CAD/slicer checks versus physical tests honestly.
- Publish `case/out/parts.json` with `printProject: {"file": "../print/production.3mf",
  "report": "../print/production-check.json"}`. Both paths resolve from `case/out/`, must remain
  inside `case/`, and the project must be a native 3MF preserving plates and printer settings.
  The independent project-local slicer verifier writes a report with `passed: true`, `file`
  (workspace-relative path to that 3MF), `sha256` (final project bytes), `meshes` and `plates`.
  Each mesh record has `name: "<manifest-part-name>.stl"`, `canonicalSTLSha256` (current assembly
  STL bytes) and `printRotateDeg` (the manifest rotation, or `[0,0,0]` when absent). Each plate has
  a unique positive integer `index` and `parts` listing those mesh names exactly once overall.
  Retain settings, warnings, surface comparison and clearance evidence in the report too.
  Hashes bind a real slicer verification; writing a passing JSON by hand is not verification.
- Publish the binding only after the verifier passes, then rerun the normal case/print checker
  because the manifest changed. Print all copies that checked 3MF **unchanged**, preserving its
  plates/profile. An invalid or stale declared project is an error, never a quiet fallback to
  the flat mesh export. Without `printProject`, legacy projects retain their generic export.
  Check the actual saved/opened file, not just a similarly named file elsewhere. Link the full,
  repair and TEST ONLY files in the current guide and report the plate counts. No physical print.

## 4. Publish one current revision and preserve printed parts

- Make `case/README.md` the entry point to the active variant, numbered assembly guide, print
  list, coupon procedure, current overview and wiring guide. Existing linked documents can
  satisfy these roles; do not create competing instructions for the same variant.
- Mark historical case guides and print projects as historical at their entry points. Keep
  native-PCB and handwired evidence explicitly scoped; do not rewrite PCB manufacturing inputs
  merely to describe a handwired variant. Firmware, wiring, guide and parts must name compatible
  variants. No stale nested manifest references: closure/coupon/mate names must resolve to
  current parts, even if the viewer does not use those fields.
- List **reuse**, **print again**, **new hardware** and **test only**, with quantity and reason.
  Compare the last printed revision to the new part geometry. A rigid placement change alone
  does not require a reprint. Establish reuse by geometry comparison with a stated tolerance,
  allowing the known rigid transform; equal bounding boxes are insufficient evidence.
- Regenerate the overview and checks after the last repair. Bind reports to final part bytes,
  manifest, applicable contract and other inputs they actually checked. An old passing report
  for another variant is not current evidence. Keep the tile's native PCB checks intact.

## 5. Verify the deliverable and report the remaining uncertainty

- **Recheck rubbing/binding over the full intended motion range after every mechanical change.**
  Include each axis in both directions, intermediate angles and combined-axis positions, with
  the final devices, screw heads/nuts, horns, mounts, closed shells, connectors and wire bundle.
  Check clearance and wire slack/pinch throughout travel. Neutral/end poses or an animation
  alone are insufficient. Use swept geometry or refined sampling, state its coverage/resolution
  and minimum measured clearance; do not present discrete samples as continuous proof.
- Distinguish intentional bearing/stop contacts from unwanted collisions. Name intended mates
  and their allowed motion; do not hide rubbing with blanket collision exclusions, omit parts,
  silently reduce the requested travel or disable checks. Fix the geometry/attachment, rebuild,
  and rerun the applicable checks from the final exported STEP/STL revision.
- Include a plain-language unpowered dry-fit check for the person: secure devices/frame/shells,
  gently move only back-drivable joints through their intended travel, and watch for unwanted
  looseness, scraping, binding, pinched wires or stressed mounts. If an actuator cannot be
  back-driven safely, specify a disconnected-drive check rather than forcing it. Do not run
  motors as part of this dry fit. Firmware writes follow the user's project-scoped permission
  and [recovery policy](firmware-recovery.md). Record the person's result separately from CAD/slicer
  evidence; real breakage, slipping or rubbing reopens that finding even after a CAD pass.
- Keep canonical STEP/STL assembled in the contract frame; apply print rotation only at export.
  Check the final printable surface, supported orientation and bed placement. For a requested
  slicer project, inspect the delivered project with the stated printer/material/profile and
  verify its meshes match checked parts up to their declared print transforms. Preserve slicer
  warnings and identify unresolved warnings; estimates do not prove printability or fit.
- Open the final overview through Case → Overview. Verify stage controls, phone layout, labels,
  moving groups and wire paths. Mocked WebGL/DOM tests are useful but do not count as that live
  browser check. Record missing verification honestly rather than upgrading its scope.
- Distinguish final-pose clearance, insertion/tool checks, sampled motion, slicing, physical dry
  fit and powered operation. Report only coverage actually exercised. `hardwareTested` stays
  false until physical evidence exists; a visually plausible animation is not such evidence.
- If the person reports a tight fit, broken insertion or inaccessible screw, treat it as design
  evidence. Revisit the reference dimensions and sequence; favor a serviceable opening or a
  simpler joint and minimize replacement prints. Do not tell them to keep forcing, cutting or
  gluing parts, especially moving joints. Follow the authorized firmware recovery workflow;
  automatic firmware writes do not authorize running motors, starting a print or ordering hardware.
