import { useEffect, useRef, useState } from "react";
import { Box, Download } from "lucide-react";
import { cn } from "@/ui/utils";
import { palette } from "@/lib/boardPalette.js";
import {
  boxCenterAndRadius,
  fitDistance,
  thinnestAxis,
  flipOffset,
  rotateOffset90,
} from "@/lib/threeFit.js";

/**
 * The 3D board, rendered in the app.
 *
 * The pipeline writes a real `board.glb` into every fab packet; this loads it
 * with three.js and gives it Altium's 3D manners: drag to orbit, scroll to
 * zoom, `F` to refit, `9` to spin the board 90°, `Ctrl+F` to see the other
 * side. Flip and spin are defined around the board's *thin* axis (measured
 * from the model, not assumed), so they mean "the far side of the board"
 * whatever up-axis the exporter chose.
 *
 * three.js loads lazily, inside the effect — the 3D tab is the only consumer,
 * and a dynamic import keeps ~600KB out of the main bundle for everyone who
 * never opens it. The camera arithmetic lives in `lib/threeFit.js`, tested in
 * node; this file owns only the WebGL plumbing.
 *
 * Honesty rules carried over from the placeholder this replaces: if WebGL or
 * the GLB fails, say what failed and keep the download button — a working
 * download beside a truthful error is worth more than a black canvas.
 */

// Local, because this view resolves its keys in a closure rather than through
// a pure arbiter (see docs/architecture/ide-shortcut-sheet.md). It has to agree
// with `boardKeymap.isTypingTarget` + `isOverlayTarget` or the two window
// listeners disagree about who owns a keystroke: without the overlay half,
// opening the shortcut sheet and pressing `F` moved the 3D camera behind the
// modal.
const isTypingTarget = (el) =>
  Boolean(
    el &&
      (/^(input|textarea|select)$/i.test(el.tagName) ||
        el.isContentEditable ||
        el.closest?.('[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"],[aria-modal="true"]')),
  );

export default function Board3DView({ glbUrl = "", stem = "board", scheme = "studio", className }) {
  const colors = palette(scheme);
  const hostRef = useRef(null);
  // phase: empty (no packet yet) | loading | ready | error
  const [phase, setPhase] = useState(glbUrl ? "loading" : "empty");
  const [failure, setFailure] = useState("");

  useEffect(() => {
    if (!glbUrl) {
      setPhase("empty");
      return undefined;
    }
    const host = hostRef.current;
    if (!host) return undefined;

    let dead = false;
    let frameId = 0;
    let renderer = null;
    let controls = null;
    let scene = null;
    let observer = null;
    let removeKeys = null;

    setPhase("loading");
    setFailure("");

    (async () => {
      let THREE, GLTFLoader, OrbitControls;
      try {
        THREE = await import("three");
        ({ GLTFLoader } = await import("three/examples/jsm/loaders/GLTFLoader.js"));
        ({ OrbitControls } = await import("three/examples/jsm/controls/OrbitControls.js"));
      } catch (error) {
        if (!dead) {
          setPhase("error");
          setFailure(`three.js failed to load — ${error?.message || error}`);
        }
        return;
      }
      if (dead) return;

      try {
        renderer = new THREE.WebGLRenderer({ antialias: true });
      } catch (error) {
        setPhase("error");
        setFailure(`WebGL is not available in this browser — ${error?.message || error}`);
        return;
      }
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(host.clientWidth || 1, host.clientHeight || 1);
      host.appendChild(renderer.domElement);

      scene = new THREE.Scene();
      scene.background = new THREE.Color(colors.background);

      const camera = new THREE.PerspectiveCamera(
        45,
        (host.clientWidth || 1) / (host.clientHeight || 1),
        0.1,
        10_000,
      );

      // A board is matte solder mask, shiny pads and dark silicon — one key
      // light, one fill from the far side, and a hemisphere so the underside
      // is dim but never black.
      scene.add(new THREE.HemisphereLight(0xffffff, 0x30343a, 0.9));
      const key = new THREE.DirectionalLight(0xffffff, 1.6);
      key.position.set(1, 2, 1.5);
      scene.add(key);
      const fill = new THREE.DirectionalLight(0xffffff, 0.5);
      fill.position.set(-1.5, -1, -1);
      scene.add(fill);

      controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;

      const gltf = await new Promise((resolve, reject) => {
        new GLTFLoader().load(glbUrl, resolve, undefined, reject);
      }).catch((error) => {
        if (!dead) {
          setPhase("error");
          setFailure(`could not read board.glb — ${error?.message || error}`);
        }
        return null;
      });
      if (!gltf) return;
      if (dead) {
        // Unmounted while the GLB streamed in: free what we just parsed.
        gltf.scene?.traverse?.((node) => {
          node.geometry?.dispose?.();
        });
        return;
      }
      scene.add(gltf.scene);

      // Frame the board from a 3/4 view, spin/flip around its measured
      // thin axis. All plain-array maths, tested in threeFit.test.js.
      const bounds = new THREE.Box3().setFromObject(gltf.scene);
      const min = bounds.min.toArray();
      const max = bounds.max.toArray();
      const { center, radius } = boxCenterAndRadius(min, max);
      const axis = thinnestAxis(min, max);
      const target = new THREE.Vector3(...center);

      const applyOffset = (offset) => {
        camera.position.set(
          target.x + offset[0],
          target.y + offset[1],
          target.z + offset[2],
        );
        controls.target.copy(target);
        controls.update();
      };

      const homeOffset = () => {
        const aspect = camera.aspect;
        const distance = fitDistance(radius, camera.fov, aspect);
        // 3/4 view: mostly along the board normal, a bit across the plane.
        const dir = [0.45, 0.45, 0.45];
        dir[axis] = 1;
        const len = Math.hypot(...dir);
        return dir.map((v) => (v / len) * distance);
      };

      let offset = homeOffset();
      applyOffset(offset);
      camera.near = Math.max(0.01, radius / 100);
      camera.far = Math.max(1000, radius * 20);
      camera.updateProjectionMatrix();
      setPhase("ready");

      const onKey = (event) => {
        if (isTypingTarget(event.target)) return;
        const k = event.key.toLowerCase();
        if ((event.ctrlKey || event.metaKey) && k === "f") {
          event.preventDefault(); // the browser's find bar loses this one
          offset = flipOffset(currentOffset(), axis);
          applyOffset(offset);
          return;
        }
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        if (k === "f") {
          offset = homeOffset();
          applyOffset(offset);
        } else if (event.key === "9") {
          offset = rotateOffset90(currentOffset(), axis);
          applyOffset(offset);
        }
      };
      const currentOffset = () => [
        camera.position.x - target.x,
        camera.position.y - target.y,
        camera.position.z - target.z,
      ];
      window.addEventListener("keydown", onKey);
      removeKeys = () => window.removeEventListener("keydown", onKey);

      observer = new ResizeObserver(() => {
        const w = host.clientWidth || 1;
        const h = host.clientHeight || 1;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h);
      });
      observer.observe(host);

      const loop = () => {
        if (dead) return;
        frameId = requestAnimationFrame(loop);
        controls.update();
        renderer.render(scene, camera);
      };
      loop();
    })();

    return () => {
      dead = true;
      cancelAnimationFrame(frameId);
      removeKeys?.();
      observer?.disconnect();
      controls?.dispose();
      if (scene) {
        scene.traverse((node) => {
          node.geometry?.dispose?.();
          const mats = Array.isArray(node.material) ? node.material : [node.material];
          for (const m of mats) {
            if (!m) continue;
            for (const value of Object.values(m)) value?.isTexture && value.dispose?.();
            m.dispose?.();
          }
        });
      }
      if (renderer) {
        renderer.dispose();
        renderer.domElement?.remove();
      }
    };
  }, [glbUrl, colors.background]);

  return (
    <div
      data-slot="board-3d-view"
      data-state={phase}
      className={cn("relative min-h-0 flex-1 overflow-hidden", className)}
      style={{ backgroundColor: colors.background }}
    >
      {/* the canvas mounts here */}
      <div ref={hostRef} className="absolute inset-0" />

      {phase === "ready" ? (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between p-2">
          <p className="font-mono text-[10px] leading-4 text-white/40">
            drag orbit · scroll zoom · F fit · 9 spin · Ctrl+F far side
          </p>
          <a
            href={glbUrl}
            download={`${stem}.glb`}
            className="pointer-events-auto inline-flex items-center gap-1.5 rounded-md border border-white/15 bg-black/30 px-2 py-1 text-[11px] font-medium text-white/70 transition-colors hover:bg-white/10 hover:text-white"
          >
            <Download className="size-3" aria-hidden />
            .glb
          </a>
        </div>
      ) : (
        <div className="absolute inset-0 grid place-items-center">
          <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center">
            <Box className="size-8 text-white/25" aria-hidden />
            {phase === "empty" ? (
              <p className="text-sm leading-6 text-white/50">
                The 3D board lands with the fab packet — build the board first.
              </p>
            ) : phase === "loading" ? (
              <p className="text-sm leading-6 text-white/50">Loading the 3D board…</p>
            ) : (
              <>
                <p className="text-sm leading-6 text-white/70">
                  The 3D view could not start: <span className="text-white/90">{failure}</span>
                </p>
                <a
                  href={glbUrl}
                  download={`${stem}.glb`}
                  className="inline-flex items-center gap-1.5 rounded-md border border-white/20 px-2.5 py-1.5 text-xs font-medium text-white/80 transition-colors hover:bg-white/10 hover:text-white"
                >
                  <Download className="size-3" aria-hidden />
                  Download the 3D board
                </a>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
