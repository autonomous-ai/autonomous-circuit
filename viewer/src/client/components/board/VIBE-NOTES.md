# Vibe, read for the parts a board tool actually needs

Study notes for the Autonomous Circuit board workspace. Circuit's client is a fork of
Vibe's, so the diff between the two repos is a literal list of the depth we are missing.
Two surfaces were read:

- **`~/code/autonomous-vibe`** — the Tauri desktop app. Our direct ancestor;
  `viewer/src/client/` is the same tree we live in.
- **`~/code/panda-website`** — the Next.js web app. Where the screenshotted
  viewport chrome (thumbnail-adjacent rail, `1/9` stepper, dot pagination, glass tool
  rail, view cube, Model/Effort composer) actually lives.

Every entry names the real file. Where Vibe does something we should **not** copy, it
says so and why. Where the screenshot shows something neither repo contains, it says
that too rather than inventing a source.

---

## 0. What is honestly not in either repo

Worth stating first, because two of the things in the brief have no donor implementation
to copy and had to be designed rather than ported.

| In the screenshot | Reality |
| --- | --- |
| Vertical **project thumbnail rail** down the far left | Not in panda-website. Its far-left column is a 256px text nav (`src/components/layout/Sidebar.tsx`). The nearest real things are the **Sessions list** (`src/modules/sessions/pages/Sessions.tsx` + `components/SessionRow.tsx`: 52px thumbnail, status dot, "Generating…" spinner) and the **All-files grid** (`src/modules/model/components/AllFilesDialog.tsx` → `StlThumbnail` → `helpers/stlThumbnail.ts`, which rasterizes an STL offscreen into a PNG data URL). |
| Title bar with **Share** / **View public page** | Not present. The kebab is `src/modules/model/components/OwnerActionsMenu.tsx` and has exactly **one** item, Unpublish (`POST /designs/{slug}/unpublish`, `src/services/api/designs.ts:331`). No `navigator.share`, no copy-link anywhere in the repo. |
| **"Main" chip** floating top-left of the viewport | Web: only in the standalone public viewer, `src/modules/viewer/components/StlCarousel.tsx:71`. Not on the authenticated model page, where `absolute left-4 top-4` is already taken by the draw / section / explode panels. The label comes from `helpers/parseTree.ts` `titleize()` (`main.stl` → `"Main"`). Desktop: `viewer/src/client/components/workbench/CadWorkspaceAssemblyInspectPill.js` is exactly this affordance — glass pill, part name, `ChevronLeft` to go back or `X` to exit — but **nothing imports it** in this checkout. Either the shipped build is ahead of the repo, or the pill was cut. Same for `CadWorkspaceTopBar.js` and desktop's `project/ProjectMenu.jsx`: both dead. |

Also: `panda-website/next.config.ts` currently 307-redirects every route to
`autonomous.ai/vibe`, so the code we read is not what serves production. Treat it as the
design of record for the surface, not proof of what shipped.

---

## 1. The projects tree — Vibe's `buildSidebarDirectoryTree`

**File:** `~/code/autonomous-vibe/viewer/src/client/workbench/sidebar.js`
(consumed at `components/CadWorkspace.js:1022` and `:1026`).

The mechanism is small and worth copying exactly:

```js
buildSidebarDirectoryTree(entries, { rootName })   // catalog entries → nested tree
  → { id, name, entries: [...], directories: [...] }   // both arrays pre-sorted
listSidebarItems(directory)  // one level, flattened: directories first-class,
                             // then entries, each {type, key, label, value}
collectAncestorDirectoryIds(id)   // "a/b/c" → ["a","a/b","a/b/c"] — auto-expand path
findSidebarDirectoryById(root, id) / sidebarDirectoryPath(root, id)  // breadcrumb
```

The tree is derived **purely from the catalog's `file` paths** — `sidebarDirectoryIdForEntry`
just pops the basename off `a/b/c.step`. There is no separate tree model on disk.
Sorting is `localeCompare(…, {numeric: true, sensitivity: "base"})` everywhere, so
`part2` sorts before `part10`. Two trees are built: one over all entries, one over the
search-filtered set (`:1022` / `:1026`) — search filters the *entry list*, then the tree
is rebuilt, which is why an empty folder never survives a search.

Labels: `sidebarLabelForEntry` deliberately strips the extension ("users think in parts,
not file formats"); `filenameLabelForEntry` keeps the real filename for tooltips.

**Selection is URL-addressable.** `writeCadParam(path)` mirrors the selected file into
`?file=`, `readCadParam()` / `findEntryByUrlPath` resolve it back, and
`selectedEntryKeyFromUrl` runs the whole resolution chain (`?file=` → `?refs=` cad-ref
tokens → env default). `replaceUrl` dedupes no-op writes and swallows the WebKit
`history.replaceState` rate-limit error, which otherwise unmounts the React tree into a
white window. `firstSelectableEntry` picks the landing file, preferring one that can
actually render over one whose generation failed.

> **Circuit equivalent — shipped as `boardTree.js` + `BoardTreeSidebar.jsx`.**
> Vibe's tree bottoms out at a *file*. Ours cannot: one board file is 134 parts and 75
> nets, and the whole point of `boardIndex` is that every one of them is addressable.
> So the tree keeps Vibe's shape (nested, searchable, ancestor-auto-expand, sorted with
> the same numeric collator) and goes two levels deeper:
>
> ```
> Project (harness-puck)
> └── Board (main)                     ← status dot + build stage line
>     ├── Components  134
>     │   ├── U  Integrated circuits  3
>     │   │   └── U1  ESP32-S3-WROOM-1
>     │   ├── R  Resistors  41 …
>     ├── Nets  75
>     │   ├── Power  4  →  3V3, 5V, VBUS…
>     │   ├── Ground  1 →  GND
>     │   └── Signal  70
>     └── Findings  3                  ← errors/warnings, same rows as Messages
> ```
>
> Component grouping is by refdes prefix (`U/R/C/L/D/J/SW/Y/F/TP/H/…`), which is the
> IEEE-315 reference-designator letter an EE already reads as a category — no new
> taxonomy invented. Net grouping is Power / Ground / Signal, read from `source_net`'s
> own `is_power` / `is_ground` booleans (already surfaced by `buildBoardIndex`), with a
> name-shape fallback for unnamed nets. **Every leaf dispatches the existing
> `handleSelect({kind, key}, {jump})`** — the tree is a third way into the *same*
> selection store the canvases and the BOM already share, not a fork.
>
> Multi-project is real here in a way it is not in Vibe's sidebar: the transport already
> exposes `project_catalog_read(id)`, so a collapsed foreign project expands lazily
> against its own catalog and clicking a board inside it opens that project first.

---

## 2. Variants, the `1/9` counter, and dot pagination

**Files:** `panda-website/src/modules/model/components/ModelViewer.tsx` (arrows + counter,
lines 427–499) and `components/SlideDots.tsx` (the dots). Data assembled in
`modules/model/pages/ModelDetail.tsx:133–159`. The desktop app has the same slider without
the numeric label — arrows at `left-4 / right-4 top-1/2`, dots pill at `bottom-4 left-1/2`
— in `autonomous-vibe/viewer/src/client/components/workbench/CadRenderPane.js:279–319`,
where it is tab-aware (in the Image tab it steps images, in the 3D tab it steps models).

The thing to understand: **Vibe's `1/9` is not versions. It is meshes.** The catalog
`_tree.json` for a project is flattened into one slide per renderable mesh:

```ts
interface MeshSlide { key: string; label: string; stlUrl: string; stepUrl?; threemfUrl? }
// key = `${designIndex}:${design.id}` (+ `/${part.name}` for an assembly part)
```

The `designIndex` prefix exists because non-unique keys froze the slider — `findIndex`
collapsed two slides onto one. Stepping is a pure modulo with wrap:

```ts
const goToSlide = (i) => onSlideChange?.((i + slideCount) % slideCount);
const showSlider = wants3d && slideCount > 1 && Boolean(onSlideChange);
```

The parent maps index → key (`setSelectedId(slides[i]?.key ?? null)`), so the slider owns
no state of its own. URLs are cache-busted through `helpers/versionDesignAssets.ts`
because the CDN folder is overwritten in place under a 31-day cache — the same discipline
as our `?v=<mtime>-<size>`.

`SlideDots.tsx` is 50 lines and does one clever thing: `MAX_DOTS = 7`, and past that the
strip becomes a **sliding window centred on the active dot**, with the edge dot shrunk
from `h-2.5 w-2.5` to `h-1.5 w-1.5` when more slides exist beyond it. Active dot is a
pill (`h-2.5 w-7`), not a circle. So the control is a fixed width no matter how many
slides there are and never collides with the tool cluster.

**Vibe's real version control is elsewhere**, and it is the better precedent for us:
`autonomous-vibe/viewer/src/client/components/workbench/SavedStates.jsx`, a `History`-icon
popover on the active project's sidebar row. Items are `{id, label, createdAt}` with the
default label `Version ${count+1}`; the four IPC calls are `snapshot_list / snapshot_save /
snapshot_restore / snapshot_delete`. Two details worth stealing: save and restore are both
**blocked while `turnInProgress`**, and a restore that rewinds the chat drops a
"↩ Reverted to …" marker into the transcript rather than silently changing history.

> **Circuit equivalent — shipped as `boardRevisions.js` + `RevisionPager.jsx`.**
> Meshes-in-a-project is not our axis; **builds of one board** is. Every rebuild rewrites
> `<stem>.circuit.json` in place, and the interesting story — the repair loop converging —
> is exactly the thing that gets destroyed by that overwrite. So the pager steps
> *revisions*: `1/6` with `SlideDots`' windowed-dots behaviour ported verbatim (7 max,
> shrunk edge dot, pill for active), arrows wrapping by modulo, and each dot tinted by
> that build's worst severity so the convergence is visible at a glance —
> red · red · amber · amber · green reads as a repair loop from across the room.
>
> Where the ring lives is the one real design call. The obvious home,
> `.circuit/revisions/`, needs the *pipeline* to write it, and this workspace owns
> `viewer/src/client/**` only. So v1 keeps the ring **client-side in IndexedDB**
> (`lib/revisionStore.js`), snapshotting `circuit.json` + `.board.json` + both review PNGs
> whenever the artifact's `?v=` token changes. That is enough to actually re-render an old
> revision — schematic, PCB, warning count, all live, not a thumbnail. See §7 for the
> server-side version worth doing next.

---

## 3. The floating tool rail

**Files:** `panda-website/src/modules/model/components/ModelViewer.tsx:452–660` (web) and
`autonomous-vibe/viewer/src/client/components/viewer3d/ViewerTools.tsx` (desktop; the
comment says it was ported *from* the website).

Layout is one row, and the wrapper is the trick:

```jsx
<div className="pointer-events-none absolute inset-x-4 bottom-4 flex items-end justify-between">
```

Click-through wrapper, `pointer-events-auto` re-enabled on each pill, so the canvas stays
draggable *between* the buttons. Every pill shares one class string —
`flex h-11 w-11 items-center justify-center rounded-full bg-[var(--color-glass)] text-white backdrop-blur-md`
— where `--color-glass` is `rgba(0,0,0,0.7)`, always paired with `backdrop-blur-md`.

(The desktop app anchors the same cluster **bottom-right** instead —
`ViewerTools.tsx` `clusterStyle = {right: insets.right + 16, bottom: insets.bottom + 16}` —
and puts the 3D/Image pill bottom-left. Web's single justified row is the better layout
and the one to copy.)

Left cluster: **3D** / **Image** segmented toggle, then the slide indicator (cube glyph,
`{activeSlide+1}/{slideCount}` in `tabular-nums`, dots, and a `SquaresFourIcon` tail that
opens the all-files grid).

Right cluster (12 controls, all but the last two `hidden lg:block`):
Appearance menu · Draw-to-AI · Exploded view · Cross-section · Measure · Reflective floor ·
Orbit↔Trackball camera switch · Print bed + dimensions · Reset view.

Dispatch is **not** an event bus and **not** a command table. Each button either calls a
prop callback or writes a global zustand singleton store (`appearance.store.ts`,
`crossSection.store.ts`, `explode.store.ts`, `measure.store.ts`) that `<ModelCanvas>`
reads. Only *reset* is imperative: `resetRef.current?.()`, a function the canvas publishes
upward. Two consequences they handle explicitly and we should copy:

1. **Mutual exclusion is hand-rolled**, not derived (`ViewerTools.tsx:63–80`): draw
   excludes everything; explode excludes section + measure, because explode's CPU-side
   vertex displacement fights their world-space picks and clip planes.
2. **Every store is a global singleton**, so each tool gets an unmount cleanup
   `useEffect(() => () => setEnabled(false), [])` — otherwise a tool left on bleeds into
   the next model.

One more thing worth taking, from the desktop side: CadWorkspace threads a
`viewportFrameInsets = {top, right: activeSheetWidth, bottom, left: activeSidebarWidth}`
object into every overlay, and each floating cluster offsets itself by
`inset(...) + 16px`. So opening the right file sheet slides the tool rail left instead of
letting it disappear underneath. Our stage has the same problem — Properties on the right,
Messages along the bottom — and the same fix.

> **Circuit equivalent — shipped as `viewportTools.js` + `ViewportToolRail.jsx`.**
> Same geometry (bottom, click-through wrapper, glass pills, `pointer-events-auto` per
> button), and the tools are the 2D-EDA ones rather than the mesh ones:
> **fit** · **zoom out** / **zoom in** · **measure** · **crosshair/HUD** · **layer flip** ·
> **single-layer cycle** · **highlight method** · **units mm/mil** · **grid** ·
> **screenshot** · **reset**. Measure moves here off the top bar, where it never belonged.
>
> We take Vibe's dispatch shape but not its store sprawl: `viewportTools.js` exports one
> pure descriptor list and a `dispatchViewportTool(id, ctx)` that switches over the
> callbacks BoardWorkspace already owns. Our tools are cheap and orthogonal (a layer
> filter does not fight a measure line), so there is **no mutual-exclusion table** — an
> honest simplification, not an oversight. Every tool reports `active` so the pressed
> state is real, and every one has a keyboard binding already documented in
> ALTIUM-NOTES; the rail's tooltip names it (`Fit (F)`, `Measure (⌘M)`), which turns the
> rail into the discovery surface for bindings that were previously invisible.

---

## 4. The view cube

**Web:** `panda-website/src/modules/viewer/components/ModelCanvas.tsx:433–448` — not
custom at all, just drei:

```jsx
{isDesktop && (
  <GizmoHelper alignment="bottom-right" margin={[60, 120]}>
    <GizmoViewcube color="#f2f2f2" hoverColor="#1174DC" textColor="#222222" strokeColor="#9c9c9c" />
  </GizmoHelper>
)}
```

FRONT/LEFT/TOP labelling is drei's; `margin={[60,120]}` lifts it clear of the bottom tool
cluster; desktop-gated by `useMediaQuery`. Faces, edges and corners all snap the camera
through the shared `controlsRef`.

**Desktop:** `autonomous-vibe/viewer/src/client/components/viewer3d/ViewPlaneControl.js`
is the hand-rolled one, and it is much more interesting. It is a **2D SVG projection of a
3D gizmo**, no three.js in the component at all:

- A 3×3 orientation matrix (`viewPlaneOrientation.{x,y,z}`, each normalized) is applied to
  each face's unit `direction` by `projectDirection` — a plain 3×3 × vec3 multiply.
- The projected `x,y` become screen coords (`50 + x*28`, `50 - y*28`) in a `0 0 100 100`
  viewBox; the projected `z` becomes **depth**, `clamp((z+1)/2, 0, 1)`.
- Depth then drives everything visual: node radius `4.95 + depth*1.05`, fill colour
  `mixRgb(axis.back, axis.front, depth)`, stem opacity `0.32 + depth*0.48`.
- Nodes are sorted by `z` and split into `backNodes` / `frontNodes`, drawn on either side
  of the centre hub, which is the painter's algorithm done in two array filters.
- The centre hub is its own button: **reset to the default isometric view**.
- Axis colours are theme-overridable but default to X red `[250,88,79]`, Y green
  `[92,233,123]`, Z blue `[84,131,255]` — the CAD convention.
- Every node is `role="button" tabIndex={0}` with Enter/Space handling and
  `onPointerDown` stopped so a click on the gizmo never starts a canvas drag.

> **Circuit equivalent — shipped as `BoardOrientationCube.jsx`.**
> Our PCB canvas is genuinely 2D: there is no camera to orbit, and a spinning cube over a
> flat board would be a lie. The honest analogue of "which face am I looking at" for a
> PCB is **which side of the board am I looking at**, so the widget is a two-face flip
> control — **TOP** / **BOTTOM** — sitting bottom-right where the cube sits, wired to the
> layer state the LayerBar already owns (`activeLayer` + `singleLayerMode`), plus a centre
> hub that resets to the default all-layers top view exactly like Vibe's does.
>
> What we *did* take from `ViewPlaneControl`: the SVG-in-a-`0 0 100 100`-viewBox
> construction, depth-as-a-scalar driving fill and radius together, the centre-hub reset,
> the `role="button"` + Enter/Space + `stopPropagation` interaction contract, and the
> theme-token colour resolution. The 3D tab keeps a real orientation job for later — when
> `Board3DView` grows a camera, this widget can grow the other four faces without changing
> its contract.

---

## 5. Top action bar — Slice plate / Publish / Open in OrcaSlicer

**File:** `autonomous-vibe/viewer/src/client/components/CadWorkspace.js:7492–7558`, all
handed to a `FloatingToolBar` as props.

The pattern worth copying is that **each action is a triple of `can…` / `…ing` / `label`**,
so the button computes its own enabled state and its own live label rather than the parent
branching on render:

```jsx
canSlice={selectedEntrySourceFormat === RENDER_FORMAT.STL}
slicing={slicing}
sliceLabel={slicing ? sliceProgressLabel(sliceProgress, slicerInstall)
                    : slicerReady === false ? "Set up & slice" : "Slice plate"}
canOpenInStudio={selectedEntrySourceFormat === RENDER_FORMAT.STL}
openInStudioLabel={openingInStudio ? "Opening…" : `Open in ${openTargetAppLabel(openTarget)}`}
```

`handleOpenInStudio` (`:7131`) resolves the STL, then calls one transport command —
`getTransport().printer_open_in_studio({ file })` — which shells out natively. The
resolution chain behind it (`sidebar.js`: `entryStlFile` → `gcodeSourceStl` →
`soleCatalogStl`) is careful: it will fall back to a project's *sole* model, but returns
`""` the moment the choice is ambiguous, because opening the wrong file is worse than
opening none.

**Do not copy:** `Slice plate`, the OrcaSlicer/Bambu Studio target switching, the printer
picker, the whole `components/printer/` tree and the slicer half of onboarding. Those are
FDM-3D-printing concepts with no PCB counterpart — a board is not sliced and there is no
"plate". The generalisable idea is only *"hand the artifact to the professional tool the
user already has installed"*, and for a board that tool is KiCad.

> **Circuit equivalent — shipped in the board top bar.**
> **Open in KiCad** is the exact analogue and the packet already contains what it needs:
> `<stem>_fab/kicad-project.zip` (schematic + board + project), surfaced by the catalog as
> `artifact.kicadProjectUrl`. **Download packet** (gerbers + BOM + CPL as one) and
> **Order at JLCPCB** (opens the rendered `ORDER.md` walkthrough, gated on `fab.ready`)
> sit beside it. We copy the `can…/…ing/label` triple shape verbatim.
>
> One honest difference: Circuit is a **web** app, and the server implements no
> shell-open command (`viewer/src/server/circuit/http.mjs` has no `file_reveal` — the
> whole slicer/printer command family was deleted in the port). So "Open in KiCad"
> downloads the project zip and states the two steps. The one-line server change that
> would make it a true native open is in §7.

---

## 6. Composer — Model and Effort

**Files:** `panda-website/src/components/GenerationModelPicker.tsx` +
`GenerationEffortPicker.tsx`, values in `src/constants/generationModels.ts`, persisted in
`src/modules/model/stores/generationModel.store.ts` (zustand `persist` with
`skipHydration`, rehydrated on mount to dodge a hydration mismatch).

```ts
export const GENERATION_EFFORTS = ['low','medium','high','xhigh','max'] as const;
export const DEFAULT_GENERATION_EFFORT = 'high';
// "Mirrors the Claude Code CLI's --effort choices and the backend's AllowedEffortLevels
//  — keep all three in sync."
```

Both sit on one row under the textarea, each with a muted caption label ("Model",
"Effort") to the left of the trigger. Effort renders **only while the user's own Claude
subscription is connected** — it is a bring-your-own-subscription control, meaningless on
the platform tiers. Submission (`PromptPanel.tsx:165`):

```ts
onGenerate(trimmed, images, byosConnected ? model : undefined, byosConnected ? effort : undefined);
```

and on the wire (`src/services/api/designs.ts:220–296`) it is `effort_level`, snake_case,
JSON when there are no images and `FormData` with the same field names when there are.
Image attach is a `ImageSquareIcon` button at `absolute right-2 bottom-2` inside the
textarea, backed by `hooks/useReferenceImages.ts`: max 4, each resized to ≤1 MB, deduped
by `name:size:lastModified`, object URLs revoked on change.

The desktop app has the model switcher
(`autonomous-vibe/viewer/src/client/components/chat/ModelControl.jsx`) but **no effort
control** — effort is web-only. On desktop it is hard-pinned:
`autonomous-vibe/desktop/src-tauri/src/commands/claude_driver.rs:486` pushes
`--effort low` for every phase, with a test asserting it. That is a defensible call for
3D-print geometry and the wrong one for a board: our review loop is three phases of
electrical reasoning where being cheap is how a wrong pinout ships. Exposing the control
is the point.

> **Circuit equivalent.** Our composer already has the model pill
> (`components/chat/ModelControl.jsx` → `transport.app_set_model` → `AppSettings.model` →
> the driver's `--model`) and the attach button. Effort is new: shipped as
> `components/chat/effortChoices.js` + `EffortControl.jsx`, same five levels, same `high`
> default, same placement — a pill immediately right of the model pill.
>
> Where it goes is constrained. `viewer/src/server/circuit/settings.mjs` normalizes the
> settings file down to exactly `{hasOnboarded, autoBuild, model}` and **drops unknown
> keys**, so an `effort` field cannot survive `app_settings_write` without a server
> change. So the pick persists in `localStorage`, and the level rides the turn the same
> way the existing view-context note does — appended, model-facing only, never shown in
> the echoed bubble (`store/chat.js` `startTurn`, alongside `pendingViewContext`). That
> is a real mechanism, not a placeholder: Claude Code's escalating thinking triggers are
> what `--effort` sets anyway. The two-line server change that makes it the real flag is
> in §7.

---

## 7. Server changes worth making (not ours to write)

This workspace owns `viewer/src/client/**`. Three things would be materially better with
a small server change, in priority order:

1. **`--effort` on the driver.** `settings.mjs` `normalize()` keeps an `effort` string
   from the closed set `low|medium|high|xhigh|max`; `driver.mjs` `buildCommandArgs` adds
   `if (effort) args.push("--effort", String(effort))` right beside the existing
   `--model` push (`:316`). Then `EffortControl` writes through `app_settings_write` and
   the directive fallback can be deleted.
2. **A revision ring the pipeline owns.** Before `build_board` moves
   `<stem>.circuit.json` into place (contract §1 stage 6), copy the outgoing
   `.circuit.json` + `.board.json` + both `_review/` PNGs into
   `.circuit/revisions/<stem>/<epoch>/`, keeping the newest 8. Nothing else changes:
   `.circuit/` is excluded from the catalog and the snapshotter, and the asset middleware
   already serves anything under the project root, so the client can read
   `/projects/<id>/.circuit/revisions/…` with no new endpoint. That makes the ring
   survive a browser-storage clear and makes it shareable, which the IndexedDB version
   cannot be. Contract §1 would need one line and a CHANGES entry.
3. **A shell-open command.** `file_reveal(file, asset)` already exists in the client
   transport's TypeScript surface but has no server handler. Implementing it as a plain
   `open`/`xdg-open`/`start` on a path resolved inside the project root turns
   "Open in KiCad" from a download into a real hand-off.

**Already landed: live build progress.** The pipeline writes
`.circuit/build-status.json` on each of its 7 stage transitions, and the server now
exposes it as `POST /api/build_status {id}` → `{state, stage, stageLabel, stageIndex,
stageCount, board, startedAt, updatedAt}` (+ `elapsedS` and `detail` on `done`).
`state` is `running | done | failed | stale`, where **`stale`** is a record untouched for
two minutes — a killed build otherwise spins forever, and saying "stale" beats lying about
progress. The client polls it at 1.5s **only while a chat turn is in progress**, stops on
any terminal state, and renders `stageLabel` + `stageIndex/stageCount` under the active
board in the tree. A board build takes 45–90s; this is the difference between "working"
and "possibly hung", and it is the one piece of Vibe-grade depth the spinner was hiding.
(The asset middleware would also have served the raw file — its allow-list is by
extension and `.json` is on it, `http.mjs:34` — but the endpoint is better: it does the
staleness arithmetic server-side, next to the clock that wrote the record.)

---

## 8. Style notes worth carrying over

- **Glass is one token.** `--color-glass: rgba(0,0,0,0.7)`, always with `backdrop-blur-md`,
  used by every floating control in the viewport. The desktop app's equivalent is the
  `cad-glass-surface` class. One token, no per-component alpha guessing.
- **Square corners.** panda-website sets `--radius-control/lg/xl: 0px`, `--radius-sm: 4px`,
  and the class convention is `rounded-[var(--radius-sm)]`, never `rounded-md` — which
  matches `knowledge/diagram-style.md`'s square-corner rule.
- **`tabular-nums` on every counter.** `1/9` must not jitter when it becomes `10/12`.
- **Comment the why, not the what.** Both repos comment every non-obvious branch with the
  bug it prevents (the `designIndex` key prefix, the WebKit `replaceState` throttle, the
  drei-`<Html>`-crashes-under-React-19 note). That is the register the rest of this
  codebase is already written in.
- Vibe web uses `@base-ui/react` + `@phosphor-icons/react`; we stay on `radix-ui` +
  `lucide-react`, which is what our fork already ships. Geometry and behaviour port;
  the primitive library does not.
