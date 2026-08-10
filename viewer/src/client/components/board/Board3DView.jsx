import { Box, Download } from "lucide-react";
import { cn } from "@/ui/utils";
import { palette } from "@/lib/boardPalette.js";

/**
 * The 3D seam.
 *
 * Altium binds `2` and `3` to 2D and 3D layout, and the pipeline already writes
 * a real `board.glb` into the fab packet — so the data is here and the keys are
 * wired. What is missing is a renderer.
 *
 * Why it is not rendered yet, stated plainly: `three` is not a declared
 * dependency of `viewer/package.json`. A copy is present in `node_modules`
 * today, but only as a transitive of `gcode-preview` (pinned by an `overrides`
 * entry) — a package this app does not use. Importing it would make the board
 * viewer depend on a hoist that nothing guarantees, and `viewer/package.json`
 * is not this component's to change. So the tab is honest instead of clever:
 * it names the artifact, hands it over, and leaves exactly one file to fill in.
 *
 * To finish it: add `three` to `viewer/package.json` dependencies, then replace
 * the body of this component with a canvas that loads `glbUrl` through
 * `GLTFLoader` (perspective camera, an OrbitControls-style drag, `Ctrl+F` to
 * flip the board, `9`/`0` for the standard rotations). Nothing else in the
 * workspace has to change — the tab, the keyboard bindings and the plumbing are
 * already here.
 */
export default function Board3DView({ glbUrl = "", stem = "board", scheme = "studio", className }) {
  const colors = palette(scheme);
  return (
    <div
      data-slot="board-3d-view"
      data-state={glbUrl ? "seam" : "empty"}
      className={cn("grid min-h-0 flex-1 place-items-center", className)}
      style={{ backgroundColor: colors.background }}
    >
      <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center">
        <Box className="size-8 text-white/25" aria-hidden />
        {glbUrl ? (
          <>
            <p className="text-sm leading-6 text-white/70">
              The board body is built — <span className="font-mono text-white/90">board.glb</span>, with every placed
              component. The in-app 3D renderer is not wired up yet.
            </p>
            <a
              href={glbUrl}
              download={`${stem}.glb`}
              className="inline-flex items-center gap-1.5 rounded-md border border-white/20 px-2.5 py-1.5 text-xs font-medium text-white/80 transition-colors hover:bg-white/10 hover:text-white"
            >
              <Download className="size-3" aria-hidden />
              Download the 3D board
            </a>
            <p className="font-mono text-[10px] leading-4 text-white/30">
              three.js is not a dependency of the viewer yet — see the note in Board3DView.jsx
            </p>
          </>
        ) : (
          <p className="text-sm leading-6 text-white/50">
            The 3D board lands with the fab packet — build the board first.
          </p>
        )}
      </div>
    </div>
  );
}
