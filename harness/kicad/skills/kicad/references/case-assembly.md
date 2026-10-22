# Case assembly and print handoff

Read before designing or repairing a case. Apply alongside AGENTS.md's Case contract and the
tile's checks. These rules came from a printed case with tight module mounts, inaccessible
fasteners, difficult joint insertion and coupons mistaken for assembly parts. A passing CAD
report did not prevent those failures. Do not turn that run's part counts or dimensions into
universal design requirements.

## Repair mode: the already-printed assembly is the starting constraint

- **Preserve the established design by default.** Unless the person changes the requirement,
  keep working parts, printed assemblies and their interfaces; a bug report is not permission
  to discard the whole mechanism or redesign the robot. Prefer a compatible local repair or
  functional add-on. If that cannot solve the defect, explain the incompatibility and smallest
  necessary replacement; do not expand beyond an explicit keep-part constraint on your own.
- Optimize the repair for **minimum new print
  time/material and disturbance of working parts**. Establish which revision is physically
  printed from their chat/photos and project history; the latest CAD is not automatically that
  revision. List the printed body, wheels, mounts and hardware to keep, and freeze their mating
  geometry. State uncertain identification as an assumption rather than pretending it is known.
- Change the failed feature and only the interfaces that must change with it. First evaluate
  a local replacement, removable cap, sleeve, clamp or adapter using existing holes/fasteners.
  An extra small functional part may save a long body reprint; minimizing STL count or making a
  cleaner integrated design is secondary to reusing the person's successful physical parts.
- Do not redesign the body/base or propagate changed holes, axes, seats and lids just to suit a
  new repair mechanism. If no workable repair preserves an affected printed part, explain the
  concrete incompatibility and smallest necessary replacement before presenting it as required.
  Explicit "keep this body" instructions remain a constraint until the person changes them.
  Do not assume permission to drill/cut working prints or rely on glue to make them compatible.
- Build and check the repaired assembly **against the frozen printed geometry**, including
  insertion, nut/tool access, retention/play and motion. A repair that passes only with a newly
  redesigned body does not preserve the printed body. Compare the supposedly unchanged mating
  shapes to the printed baseline, not just their file names or bounding boxes.
- Lead repair instructions with the **new/updated plates to select** and a separate **keep
  using these printed parts** list. Print all still opens one complete project containing both
  groups on separate named plates; opening a complete kit is not an instruction to print every
  plate again. Preserve useful real production test prints and assign them to already-printed
  plates, so nothing is printed twice. A separately requested repair subset is an extra export,
  never a replacement or mislabeled subset for the complete Print all project.

### Persistent inventory of the person's actual prints

- Read `case/PRINTED-INVENTORY.md` before each repair, redesign or plate export; create it from
  available project/chat/photo evidence when absent. Keep this project-local file across turns,
  context resets and assembly variants. Update it when the person says they printed, reused,
  broke, discarded or modified something, or supplies a photo that identifies an existing print.
  Do not make them repeat information already available. No extra approval checkpoint is added.
- For each physical part/instance record stable ID, quantity, likely assembly/revision and mesh
  hash when actually known, material if known, fit/breakage/modification status, evidence source
  (dated message or image path) and identification confidence. Preserve earlier evidence instead
  of overwriting the physical history with the latest CAD. A photo may establish a visible
  printed part; it does not identify hidden parts, exact dimensions or an indistinguishable STL
  revision. Record those as uncertain. Never invent measurements or an exact photo-to-mesh match.
- Exporting, slicing, downloading or opening a 3MF does not establish that it was printed. A
  successful CAD/slicer check does not establish fit, strength, traction or a successful physical
  assembly. Keep **printed**, **fits/works**, **failed/modified**, and **identity unknown** distinct.
  Preserve reported sanding/tape/breakage as open findings until there is evidence of a repair.
- Compare current requirements with the actual inventory before adding anything to a print
  queue. A renamed part, rigid placement change or new assembly label alone is not a reprint.
  Use the geometry comparison below to establish compatibility; unchanged file names alone do
  not establish it either. Record replacements with the old→new revision and concrete reason.
  For partial quantities, only the shortage goes to the new/update plates; the compatible
  already-printed quantity stays on its own plates in the same complete 3MF. A printed part with
  fit still pending stays recorded as printed; do not silently count it as missing and print it
  again. For uncertain identities, state the unresolved assumption and smallest representative
  test; do not declare the part absent or schedule a whole replacement kit as a certainty.
- On handoff, reconcile current required instances = compatible printed instances + new/update
  instances, with pending/obsolete/test-only items identified separately. Keep usable real
  production parts from test kits in the inventory; decorative coupons remain TEST ONLY.

## Build order: frame → mounted devices and joints → covers

- **Design the functional frame first.** For a new robot/moving device, establish the load-bearing
  base and moving frames before the outer case. The uncovered frame must locate and retain the
  devices and joints; cosmetic covers must not be the missing support that makes a loose frame
  or device stay in place. A simple structural base may itself serve as the frame; do not add
  redundant parts to a static device or rebuild a working printed base to follow this sequence.
- Place the actual device/module envelopes on that frame: board, screen, actuators/horns and
  owned peripherals. Define their seats, fixing points, fastener/nut capture, insertion/removal
  order and tool access while the structure is exposed. Route connectors and real wire bundles
  with service access and moving slack. Keep the native board placement/contract unchanged;
  record unmeasured hardware as assumptions under the existing policy.
- Identify the fixed base, each moving group, its axis/pivot, the actual actuator-to-horn-to-load
  connection and its support. Prefer one actuator per requested axis and direct stock interfaces
  when compatible with the owned hardware and required control. Added linkages need the existing
  function-based justification; do not make a three-piece kit a target in itself.
- Keep stationary electronics/connectors accessible and moving electronics on their own carrier
  where the product calls for it. Route the real flexible harness between groups with slack and
  restraint at both ends; check combined motion. Do not run a board, carrier plate or taut bundle
  through a joint's travel. Preserve the native board contract; a reference image does not
  authorize moving footprints, changing GPIOs or silently replacing a working firmware variant.
- Complete the frame/device/fastener/joint subassembly in CAD first. Check insertion and service,
  retention/play, the dimension stack, tool reach and full motion/wire clearance with the covers
  removed. Make that exposed assembly visible in the guide/overview. Supply representative frame
  fit tests when needed; keep physical results pending if unavailable, without inventing a new
  automatic gate or blocking the CAD workflow on an unperformed bench test.
- Only after those structural CAD checks pass, design the case **as covers attached to that
  frame**, using defined locating surfaces and sturdy removable screw/latch connections. Keep
  ports, moving envelopes and service access clear. Do not relocate proven mounts/joints merely
  to fit a decorative shape, or add a ridge/boss that becomes an accidental motion stop.
- Attach the covers in the model and repeat the full assembly/motion/wire/tool checks, including
  every rib, latch and screw. If a new cover interferes with the checked mechanism, repair the
  cover/attachment locally before considering changes to the working frame. Show the assembly
  sequence as frame → devices and fasteners/joints → covers, with a way to remove covers and
  inspect/service the functional subassembly.
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

## Before a long structural repair: make the joint intent explicit

- Before detailed CAD and a full slicer run for a changed structural joint, record a short
  plain-language repair brief in the existing project plan/guide, with a section view or simple
  sketch. Restate the person's requested shape and fastening: what is one continuous piece,
  what passes through what, printed versus stock parts, where the nuts sit and from which side
  screws enter. Mark visible evidence, owned hardware and assumptions separately. A phrase such
  as "through axle" does not choose between a printed member and a purchased threaded rod.
- Resolve routine choices autonomously within that stated intent; this is not a mandatory
  approval checkpoint. Do not silently replace the requested structure with newly purchased
  stock or a different fastening scheme. If the shape is uncertain, state the assumption and
  develop the smallest representative joint first, rather than a complete kit around a guess.
  Incorporate a later user correction into the brief before continuing; preserve compatible work.
- Distinguish **reuse this actual printed part**, **preserve its mating interface**, and
  **keep its appearance while changing geometry**. Identify "the old tread/rail" against the
  named/photo baseline before exporting another kit. Keeping a shape does not mean an old print
  still fits: state any required replacement and the exact changed interface/dimension.
- Trace how forces reach the body/support through actual mating seats and restraints. Identify
  which interfaces must remain fixed, which may rotate and how both ends are retained. Reuse
  ordinary accessible hardware where appropriate, but do not treat a screw through an unsupported
  printed sleeve, glue or a collision-free pose as evidence that the joint carries its load.

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
- Before finalizing a full kit, total the printed instances, fastener types/installed quantities,
  repeated clamps and assembly operations. Separate installed hardware from spares. Review the
  most repeated mounts for a simpler supported connection, rather than merely calling the kit
  "simple". Do not impose an arbitrary screw limit, substitute weak latches or discard usable
  prints to meet a number; necessary structural/service fasteners remain allowed.
- Start with supplied photos, owned parts and authoritative references. Record module envelope,
  connectors, shaft/horn interface, screw diameter, available length, head shape and nut/tool
  space. A screw of the right thread can still have a head that will not seat. Preserve usable
  factory horns, screws and mounts when that reduces assembly work.
- Persist confirmed fastener sizes/head types/tools in the existing project hardware list or
  inventory, with the user's statement/photo and uncertainties. Reuse that information on later
  turns; distinguish owned, reused factory hardware and new purchases. Before export, reconcile
  every selected screw with that list. A familiar nominal length is not evidence the person has
  it. Prefer a small compatible seat/boss/clamp repair using confirmed hardware over another
  purchase, cutting screws or redesigning working parts; recheck engagement, tip and motion.
  If a different size remains necessary, state why and list it as new hardware, never as owned.
- For unknown clones, mark the reference and dimensions **GIẢ ĐỊNH**. Use open/adjustable mounts
  and a small fit coupon instead of a tight nominal pocket. Proceed on the reference; do not
  ask a nontechnical person to identify an engineering detail they cannot know.
- Compare mechanisms by required function, printed pieces, purchased hardware, assembly steps
  and maintenance. Prefer fewer, accessible parts; justify added cams/linkages. Preserve the
  requested motion and control behavior when simplifying. Check actuator/control compatibility;
  a displayed CAD angle or a timed motor pulse does not establish actual angle feedback.
- When changing architecture (for example tracks to wheels), review every inherited guide,
  spacer, retaining ring and bracket against the new function. Preserve useful printed parts,
  but do not keep a stack merely because it existed in the old design. State the purpose of
  every retained loose piece and compare a simpler supported joint; do not delete a necessary
  support or discard a working print solely to reduce the part count. In repair mode, keep a
  harmless already-printed piece when deleting it would force an otherwise unnecessary reprint.
- Declare power-off assembly, initial centering and any required sensing/limits in the guide and
  firmware. Do not silently add unowned hardware or claim control the installed actuator cannot
  provide. Follow the existing assumption policy and keep untested functions explicit.
- When actuator count/type, drive arrangement or module location changes, reconcile the
  mechanical plan with channel/GPIO mapping, supply/wire budget, neutral/centering and limits.
  Link each named joint/wheel to the current firmware/WIRING variant; record missing calibration
  or unimplemented behavior honestly. A new CAD assembly does not establish compatibility with
  live hardware or authorize flashing, motor tests or replacing a known working configuration.

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
- **Deliver an instance-level fastener map.** Every screw position gets a stable ID matched to
  the assembly guide, overview and hardware list, including screws in retained head/device
  assemblies. Show thread/diameter × length, head/driver, nut/washer, entry side and mating part
  IDs at the relevant step. Draw/show the actual screw and insertion arrow; a global BOM or an
  unlabeled cylinder is not an assembly instruction. Identical positions may share a clear view
  but retain distinct IDs/quantities. Derive the list from current hardware instances and check
  that every instance is covered, without counting spare screws as installed positions.
- Check stack thickness, engagement, tip protrusion and tool/nut access for that exact screw.
  Distinguish a servo's factory centre/spline screw from screws clamping a horn to printed parts;
  never relabel an unknown factory screw as M3. Label unknown dimensions/factory hardware
  honestly and retain its known original; do not fabricate thread/length to fill the map.

### One assembly navigation format for every project

- Use the same labels, order and layout for existing dog/pet and future overviews. The primary
  selector starts with **Đủ bộ**, followed by **Bước 1 — <việc ráp>** through **Bước N — <việc
  ráp>** in actual assembly order; previous/next buttons use the same selected state. Do not
  expose separate stage, assembly-step and fastener-group workflows that the person must align.
- Drive this navigation from one canonical ordered step list shared with the current assembly
  guide: each step references its actual part instances, hardware IDs, view/visibility and short
  action. Avoid a second independently numbered screw tutorial. A step may contain multiple
  hardware types/groups; show them all with their own quantities. Do not claim all screws in a
  reusable hardware family are installed at that step if only some instances are used there.
- Each step automatically shows the relevant 3D parts and screws/nuts and one **Vít/tán bước
  này** card: ID/location, size/length or original factory-screw label, count, matching nuts,
  entry direction and a brief action/tool note. Retained hardware and wiring-only/no-screw steps
  remain explicit. This card and the 3D view update together; no extra fastener-mode selection is
  required. Clicking an ID focuses that instance while retaining the selected assembly step.
- **Đủ bộ** restores the complete seated assembly and a concise total hardware summary; it is
  the same navigation's starting view, not a duplicate final assembly step. Camera shortcuts
  stay secondary. Put optional per-instance selectors, screw explosion/cutaways and motion-axis
  controls under a clearly labeled **Chi tiết** disclosure, initially closed. An ID click may
  open its local detail. Keep **Vít/tán** visible by default. Leaving a step or returning to Đủ
  bộ resets illustrative offsets and focus; changing the camera must not change the step.
- Keep common shell/control markup in a reusable generator/template and vary the step data,
  geometry and confirmed hardware by project. Within the shipped skill, it must be self-contained;
  project generators must not import code from another workspace or depend on a live repo checkout.
  Simplifying the UI does not authorize CAD changes, omitted hardware, merged mechanical actions
  with incompatible assembly order, or expanding firmware/hardware permissions.
- Verify the first view, Đủ bộ → each step → previous/next → Đủ bộ, mixed hardware and no-screw
  steps, per-ID focus, optional detail open/close and reset at phone width. Compare guide/step
  order, instance quantities and 3D IDs against the checked final assembly. Preserve independent
  hardware visibility and real checked motion without a second mandatory menu. Record actual
  visual checks separately from source checks when a permitted viewer is unavailable.

### Fasteners belong in the 3D overview

- Include the actual bought-hardware instances in the assembled overview, with a recognizable
  head and shank, nut and washer where used. Simplified low-poly geometry is enough; decorative
  helical threads are unnecessary. Use the same definitions, dimensions, seats and coordinates
  as the mechanical checks and hardware map, not an independently positioned illustration.
- Provide a clearly named **Vít/tán** visibility toggle, enabled by default and separate from
  electronics. Opening a frame or hiding devices must not accidentally hide the screws needed
  to understand that assembly. Normal shell occlusion is handled by opening, transparency or
  cutaway controls, not by pretending a hidden screw is on the visible surface.
- Let the person select a group or individual hardware ID to focus/zoom and highlight its 3D
  screw, size/head/length or factory label, matching nut/washer and mating parts. Show the entry
  side and insertion arrow at that exact joint. Dim unrelated parts and avoid overlapping labels
  on a phone; keep a route back to the seated full assembly.
- Supply a local exploded/cutaway view for nested joints: show how shaft, horn, driven part and
  clamp/retainer actually mate, and distinguish fixed from rotating parts. Explain which face
  seats against which and which screw fixes each connection. A whole-robot explosion, 2D screw
  icon, colored hole or arrow alone does not resolve a confusing local stack.
- Apply the same transform chain to the joint's hardware, labels and arrows as to its moving
  parts. Explode along the real insertion axis; reset returns to the checked seated position.
  Label exploded offsets as assembly illustration, not a physical motion or changed geometry.
- Bought screws/nuts/washers are nonprintable reference objects; keep them out of printable
  meshes/Print all. This does not authorize omitting retained production prints from the complete
  3MF. Coverage must account separately for printed parts, bought hardware and test-only pieces.
- After regeneration, verify the saved overview's default hardware visibility, independent
  toggle, group/individual selection, local stack, exploded reset and moving-group alignment.
  Compare rendered hardware IDs/counts with the actual instance map, including factory screws
  and retained assemblies. Record final overview/source hashes and the controls really tested.
  If an approved viewer is unavailable, state the unverified visual checks; source inspection
  or a previous revision's report is not a fresh visual pass. No physical print/fit claim follows.

### Insertion and service access

- Check the path from outside into the assembled position, against everything already installed
  at that step. Check removal/service too. State the sampled path and its coverage; samples do
  not prove unchecked motion between them. Do not teleport parts into closed forks or assume
  rigid plastic can spread without checking the material and deflection.
- Check tool access to the real screw head, insertion length and nut counterhold, with the
  actual head/driver envelope. Account for fingers and wire placement where relevant; record
  access not modeled. A clear screw axis alone is not proof of tool access. If a later part
  blocks a screw, change the order and show that order explicitly, or redesign the joint.
- A captive nut pocket needs an accessible insertion/removal path, anti-rotation faces and
  retention while the screw starts, with adequate surrounding material. Check the actual nut,
  screw engagement and assembly order; a nominal M3 hole or a clear driver axis is insufficient.
  Do not bury a nut at the bottom of a narrow deep well. Prefer the owner's already successful
  drop-in pocket pattern when applicable, with dimensions still subject to the fit assumption.
- A snap needs its mating male/female features, lead-in, deflection space and a reachable release
  path. Test the mating pair in the intended material and print direction before relying on it.
  A cosmetic bump is not a validated latch; do not use glue as an undocumented repair.
- Prefer fewer fasteners and sturdy serviceable keyed/latching joints where suitable. Support
  the male feature instead of making a long fragile hook; give the female opening a lead-in and
  verified insertion clearance while retaining a real locking/locating face. Easy insertion
  must still resist unwanted play/pullout. Keep screws where structure or service requires them.
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
  instances, and deliver a separate TEST ONLY coupon project. **Print all must remain one complete
  native 3MF**, containing every current printable non-coupon production instance, including
  already-printed parts and identical copies. Do not silently omit items. A separate repair
  subset may be exported when requested, but never replaces the complete Print all binding.
  Reconcile the manifest against the assembled production-part list, including retained head,
  body and other reused prints; do not leave them out of that list merely because they already
  exist physically. Bought hardware/electronics reference models remain nonprintable as before.
- Use the printed inventory to partition plates **within that same complete project** into
  **ĐÃ IN — GIỮ LẠI** (already printed) and **MỚI/SỬA — CẦN IN** (new/updated). Never mix the groups
  on a plate; add numbered plates within each group on overflow. Give plates actual native names
  visible in Bambu, including current variant/revision; a project filename or guide label alone
  is insufficient. Open/reinspect the saved 3MF and verify names, grouping and exactly-once total
  current-manifest coverage. List plate names and part IDs/quantities in the guide, telling the
  person which new plates to select without asking them to reprint the old plates.
  Printed status changes plate assignment, **never object inclusion**: do not drop printed
  instances, delete their meshes or mark them unprintable. For example, four required identical
  instances with two already printed still means four objects in one file: two on already-printed
  plates and two on new plates. Retired geometry belongs in a separate historical reference
  project, not as an extra current-production instance; TEST ONLY remains separate. Preserve the
  existing `printProject` full-coverage validator. Update assignments when inventory, quantity or
  geometry changes, and never mark a print completed without evidence.
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
- Maintain one current decision/assembly description. When the person changes tread, rail,
  spacing or mechanism, replace the superseded active instructions and reconcile plan, guide,
  overview and print projects before handoff. Move/link old decisions under explicit history;
  do not leave both "no outer rail" and "fit the outer rail" in the active path. Preserve printed
  baselines and useful links; a later paragraph must not silently contradict an earlier current one.
- List **reuse**, **print again**, **new hardware** and **test only**, with quantity and reason.
  Compare the last printed revision to the new part geometry. A rigid placement change alone
  does not require a reprint. Establish reuse by geometry comparison with a stated tolerance,
  allowing the known rigid transform; equal bounding boxes are insufficient evidence.
- Regenerate the overview and checks after the last repair. Bind reports to final part bytes,
  manifest, applicable contract and other inputs they actually checked. An old passing report
  for another variant is not current evidence. Keep the tile's native PCB checks intact.

## 5. Verify the deliverable and report the remaining uncertainty

- Check the **assembled dimension stack**, not only individual nominal fits: body/rail spacing,
  wheel widths, sleeves, spacers, seats, screw heads and nuts in their final seated positions.
  Account for the declared tolerance range and show intended axial/radial running gaps. An
  unexplained multi-millimetre end gap, tightening that binds the wheel, or a need for coarse
  sanding/forced assembly is a repair finding; do not make field compensation the pass condition.
  Validate a proposed simpler passive-wheel mechanism on a representative small assembly before
  expanding the repair. Repeatedly stacking tiny compensating pieces is not simplification.
- Preserve the functional contact geometry of shafts/bearings/seats when choosing print-friendly
  shapes. Explain orientation in terms of that part's function, support and expected loads;
  slicer success alone does not establish strength, wear or smoothness. Verify the final printed
  joint with the stated fit/play/motion procedure; tape on a broken joint is temporary, not a pass.
- Check unwanted play and retention separately from collision-free movement. For each repaired
  joint, name possible axial/radial movement, twisting or lifting of the support, intended running
  clearance and the faces/fasteners that restrain unwanted motion. Include fastener preload and
  its effect on free rotation as assumptions to verify, not a claim of simulated strength.
  The small unpowered test assembly must include the real support span, both end restraints and
  mating frame/body seats; an isolated hole coupon cannot demonstrate whole-joint wobble or
  rigidity. Provide a gentle plain-language play/binding check and keep physical results pending
  until the person reports them. Do not prescribe powered tests or force/back-drive an actuator.
- **Recheck rubbing/binding over the full intended motion range after every mechanical change.**
  Include each axis in both directions, intermediate angles and combined-axis positions, with
  the final devices, screw heads/nuts, horns, mounts, closed shells, connectors and wire bundle.
  Check clearance and wire slack/pinch throughout travel. Neutral/end poses or an animation
  alone are insufficient. Use swept geometry or refined sampling, state its coverage/resolution
  and minimum measured clearance; do not present discrete samples as continuous proof.
- Include every added lid rib, boss, latch and decorative ridge in that motion check, using
  the actual installed revisions (including a retained older head). A fixed lid feature still
  must clear the moving head/neck throughout travel; do not add an unrequested motion stop.
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
