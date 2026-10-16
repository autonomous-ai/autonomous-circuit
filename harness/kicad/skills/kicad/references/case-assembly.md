# Case assembly and print handoff

Read before designing or repairing a case. Apply alongside AGENTS.md's Case contract and the
tile's checks. These rules came from a printed case with tight module mounts, inaccessible
fasteners, difficult joint insertion and coupons mistaken for assembly parts. A passing CAD
report did not prevent those failures. Do not turn that run's part counts or dimensions into
universal design requirements.

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
- Put coupons in a separately named test project/plate when delivering slicer projects. Identify
  them separately in any generic Print all export, which may include all printable manifest
  entries. Never claim that button automatically removes coupons. Exclude coupons from the
  assembled model, assembly part count and product assembly steps.

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
  motors or flash firmware yourself. Record the person's result separately from CAD/slicer
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
  gluing parts, especially moving joints. Keep firmware flashing and printing under the user's
  control; never auto-flash, start a print or order hardware.
