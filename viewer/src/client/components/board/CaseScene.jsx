import { useEffect, useRef, useState } from "react";
import { Box } from "lucide-react";
import { cn } from "@/ui/utils";
import { useDarkMode } from "@/ui/use-dark-mode.js";
import { boxCenterAndRadius, fitDistance } from "@/lib/threeFit.js";
import { explodeOffset, isPrintedPart, partColors, viewDirection } from "./caseView.js";

/**
 * The case in 3D, around the board it was built for.
 *
 * The board is drawn from its contract (boards/<stem>.enclosure.json), not from
 * board.glb: the contract is the frame the case was modelled in, so the two line
 * up by construction. A GLB from the KiCad exporter sits in its own frame, and
 * an offset between "the board" and "the case" on screen is exactly the 9 mm
 * mistake the contract exists to prevent.
 *
 * three.js loads lazily, like the 3D board. Rebuilding the scene when the parts
 * or the contract change is the simple thing and fast enough (a case is a few
 * hundred kB of STL); visibility, see-through and explode only move or hide
 * meshes already loaded.
 *
 * @param {{
 *   contractUrl?: string,
 *   parts: Array<{name: string, role: string, stlUrl: string}>,
 *   visible: Record<string, boolean>,
 *   exploded?: boolean,
 *   seeThrough?: boolean,
 *   showBoard?: boolean,
 *   onSize?: (sizeMm: number[]|null) => void, // assembled printed parts, once loaded
 *   background?: string,      // defaults to the app theme's scene colour
 *   className?: string,
 * }} props
 */

// Each part has its own colour (caseView.partColors), the same one the parts
// list shows as a swatch, so "which piece is that" is answered by looking.

const VIEWS = [
  ["iso", "Iso"],
  ["top", "Top"],
  ["front", "Front"],
];

// The floor follows the app theme like the CAD viewer's (--cad-viewer-*); the
// parts keep their print colours in both.
const SCENE_LOOK = {
  light: { background: "#eaf0f2", ground: 0xb9c7cc, gridMajor: 0xb6c6ca, gridMinor: 0xd4e0e2 },
  dark: { background: "#0e0f13", ground: 0x1d2229, gridMajor: 0x4b5563, gridMinor: 0x262b33 },
};

export default function CaseScene({
  contractUrl = "",
  parts = [],
  visible = {},
  exploded = false,
  seeThrough = false,
  showBoard = true,
  onSize,
  background: backgroundProp,
  className,
}) {
  const look = SCENE_LOOK[useDarkMode() ? "dark" : "light"];
  const background = backgroundProp || look.background;
  const hostRef = useRef(null);
  const sceneRef = useRef(null); // { meshes: Map<name, {mesh, base, offset}>, board, applyLook }
  const [phase, setPhase] = useState(parts.length ? "loading" : "empty");
  const [failure, setFailure] = useState("");
  const [view, setView] = useState("iso");
  const onSizeRef = useRef(onSize);
  onSizeRef.current = onSize;
  const partsKey = parts.map((p) => `${p.name}|${p.role}|${p.stlUrl}`).join("\n");

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !parts.length) {
      setPhase("empty");
      return undefined;
    }
    let dead = false;
    let frameId = 0;
    let renderer = null;
    let controls = null;
    let scene = null;
    let observer = null;
    setPhase("loading");
    setFailure("");
    setView("iso");
    onSizeRef.current?.(null);
    const colors = partColors(parts);

    (async () => {
      let THREE, STLLoader, OrbitControls;
      try {
        THREE = await import("three");
        ({ STLLoader } = await import("three/examples/jsm/loaders/STLLoader.js"));
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
      scene.background = new THREE.Color(background);
      const camera = new THREE.PerspectiveCamera(40, (host.clientWidth || 1) / (host.clientHeight || 1), 0.1, 10_000);
      camera.up.set(0, 0, 1); // the contract's z is "out of the board's top face"
      scene.add(new THREE.HemisphereLight(0xffffff, look.ground, 1.15));
      const key = new THREE.DirectionalLight(0xffffff, 1.25);
      key.position.set(1, -1.5, 2);
      scene.add(key);
      const fill = new THREE.DirectionalLight(0xffffff, 0.5);
      fill.position.set(-1.5, 1, -1);
      scene.add(fill);
      controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;

      // The board, from the contract: a slab, every part's box, every module's outline.
      const boardGroup = new THREE.Group();
      let boardSpan = 60;
      try {
        const contract = contractUrl ? await fetch(contractUrl).then((r) => (r.ok ? r.json() : null)) : null;
        if (contract?.board) {
          const { widthMm: w, heightMm: h, thicknessMm: t } = contract.board;
          boardSpan = Math.max(w, h);
          const slab = new THREE.Mesh(new THREE.BoxGeometry(w, h, t), new THREE.MeshStandardMaterial({ color: 0x468a69, roughness: 0.8 }));
          slab.position.set(0, 0, -t / 2);
          boardGroup.add(slab);
          const partMat = new THREE.MeshStandardMaterial({ color: 0x5d6c72, roughness: 0.7 });
          for (const p of contract.parts || []) {
            const [x0, y0, x1, y1] = p.boxMm || [];
            if (!(p.heightMm > 0) || ![x0, y0, x1, y1].every(Number.isFinite)) continue;
            const box = new THREE.Mesh(new THREE.BoxGeometry(Math.max(x1 - x0, 0.2), Math.max(y1 - y0, 0.2), p.heightMm), partMat);
            box.position.set((x0 + x1) / 2, (y0 + y1) / 2, p.side === "bottom" ? -t - p.heightMm / 2 : p.heightMm / 2);
            boardGroup.add(box);
          }
          const moduleMat = new THREE.MeshStandardMaterial({ color: 0x5f99ba, transparent: true, opacity: 0.65, roughness: 0.6 });
          for (const m of contract.modules || []) {
            const corners = m.outlineCornersMm || [];
            if (m.offboard || corners.length < 3) continue;
            const shape = new THREE.Shape(corners.map(([x, y]) => new THREE.Vector2(x, y)));
            const z0 = m.side === "bottom" ? m.frontZMm : 0;
            const z1 = m.side === "bottom" ? -t : m.frontZMm;
            const depth = Math.max(Math.abs(z1 - z0), 0.2);
            const solid = new THREE.Mesh(new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false }), moduleMat);
            solid.position.z = Math.min(z0, z1);
            boardGroup.add(solid);
          }
        }
      } catch {
        // No contract: the case is still worth seeing on its own.
      }
      if (dead) return;
      scene.add(boardGroup);

      const loader = new STLLoader();
      const meshes = new Map();
      for (const part of parts) {
        let geometry;
        try {
          geometry = await new Promise((resolve, reject) => loader.load(part.stlUrl, resolve, undefined, reject));
        } catch (error) {
          if (!dead) {
            setPhase("error");
            setFailure(`could not read ${part.name}.stl — ${error?.message || error}`);
          }
          return;
        }
        if (dead) {
          geometry.dispose();
          return;
        }
        geometry.computeVertexNormals();
        geometry.computeBoundingBox();
        const material = new THREE.MeshStandardMaterial({
          color: colors[part.name] ?? 0xb3c1c5,
          roughness: 0.78,
          metalness: 0.0,
          side: THREE.DoubleSide,
          polygonOffset: true,
          polygonOffsetFactor: 1,
          polygonOffsetUnits: 1,
        });
        const mesh = new THREE.Mesh(geometry, material);
        mesh.name = part.name;
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(geometry, 35),
          new THREE.LineBasicMaterial({ color: 0x60747c, transparent: true, opacity: 0.42, depthWrite: false }),
        );
        mesh.add(edges);
        scene.add(mesh);
        const bb = geometry.boundingBox;
        meshes.set(part.name, { mesh, edges, part, bounds: { min: bb.min.toArray(), max: bb.max.toArray() } });
      }

      const assembled = new THREE.Box3();
      const printed = new THREE.Box3();
      for (const { mesh, part } of meshes.values()) {
        if (part.role !== "coupon") assembled.expandByObject(mesh);
        if (isPrintedPart(part)) printed.expandByObject(mesh);
      }
      if (!printed.isEmpty()) onSizeRef.current?.(printed.getSize(new THREE.Vector3()).toArray());
      assembled.expandByObject(boardGroup);
      const footprint = assembled.getSize(new THREE.Vector3());
      const gridSize = Math.max(80, boardSpan * 2, footprint.x * 1.7, footprint.y * 1.7);
      const grid = new THREE.GridHelper(gridSize, 16, look.gridMajor, look.gridMinor);
      grid.rotation.x = Math.PI / 2;
      grid.position.set((assembled.min.x + assembled.max.x) / 2, (assembled.min.y + assembled.max.y) / 2, assembled.min.z - Math.max(1.5, boardSpan * 0.025));
      grid.material.transparent = true;
      grid.material.opacity = 0.55;
      scene.add(grid);
      const caseMaxX = assembled.max.x;
      const target = new THREE.Vector3();
      let radius = 1;
      // Frame what is on screen: the assembly, plus the coupon when it is shown beside it.
      let lastView = "iso";
      const home = (preset = lastView) => {
        lastView = preset;
        const all = assembled.clone();
        for (const { mesh, part } of meshes.values()) if (part.role === "coupon" && mesh.visible) all.expandByObject(mesh);
        const fit = boxCenterAndRadius(all.min.toArray(), all.max.toArray());
        target.set(...fit.center);
        radius = fit.radius;
        // A small margin: the model should fill the view, not float in it.
        const distance = fitDistance(radius * 1.05, camera.fov, camera.aspect);
        const dir = new THREE.Vector3(...viewDirection(preset)).multiplyScalar(distance);
        camera.position.copy(target).add(dir);
        controls.target.copy(target);
        camera.near = Math.max(0.05, radius / 200);
        camera.far = Math.max(1000, radius * 40);
        camera.updateProjectionMatrix();
        controls.update();
      };
      let framedCoupon = null;
      sceneRef.current = {
        meshes,
        board: boardGroup,
        home,
        apply({ visible: vis, exploded: ex, seeThrough: clear, showBoard: withBoard }) {
          const gap = Math.max(boardSpan * 0.45, 12);
          boardGroup.visible = withBoard;
          for (const { mesh, edges, part, bounds } of meshes.values()) {
            mesh.visible = vis[part.name] !== false;
            const offset = ex || part.role === "coupon" ? explodeOffset(part, bounds, caseMaxX, gap) : [0, 0, 0];
            mesh.position.set(...offset);
            const shell = part.role === "shell" || part.role === "lid";
            mesh.material.transparent = clear && shell;
            mesh.material.opacity = clear && shell ? 0.38 : 1;
            mesh.material.depthWrite = !(clear && shell);
            mesh.material.needsUpdate = true;
            edges.material.opacity = clear && shell ? 0.2 : 0.42;
          }
          const couponShown = [...meshes.values()].some(({ mesh, part }) => part.role === "coupon" && mesh.visible);
          if (couponShown !== framedCoupon) {
            framedCoupon = couponShown;
            home();
          }
        },
      };
      sceneRef.current.apply({ visible, exploded, seeThrough, showBoard });
      setPhase("ready");

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
      sceneRef.current = null;
      observer?.disconnect();
      controls?.dispose();
      if (scene) {
        scene.traverse((node) => {
          node.geometry?.dispose?.();
          const mats = Array.isArray(node.material) ? node.material : [node.material];
          for (const m of mats) m?.dispose?.();
        });
      }
      if (renderer) {
        renderer.dispose();
        renderer.domElement?.remove();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partsKey, contractUrl, background, look]);

  useEffect(() => {
    sceneRef.current?.apply({ visible, exploded, seeThrough, showBoard });
  }, [visible, exploded, seeThrough, showBoard]);

  const goTo = (preset) => {
    setView(preset);
    sceneRef.current?.home(preset);
  };

  return (
    <div data-slot="case-scene" data-state={phase} className={cn("relative min-h-0 flex-1 overflow-hidden", className)} style={{ backgroundColor: background }}>
      <div ref={hostRef} className="absolute inset-0" />
      {phase === "ready" ? (
        <>
          <div data-slot="case-views" className="absolute bottom-2 right-2 flex gap-1">
            {VIEWS.map(([id, label]) => (
              <button
                key={id}
                type="button"
                data-slot="case-view"
                data-view={id}
                aria-pressed={view === id ? "true" : "false"}
                onClick={() => goTo(id)}
                className={cn(
                  "rounded-md border px-2 py-1 text-[11px] font-medium backdrop-blur transition-colors",
                  view === id ? "border-primary/50 bg-primary/20 text-foreground" : "border-border bg-background/70 text-muted-foreground hover:text-foreground",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          <p
            className="pointer-events-none absolute bottom-2 left-2 rounded bg-background/70 px-2 py-1 font-mono text-[10px] leading-4 text-muted-foreground"
            title="drag to orbit · scroll to zoom · right-drag to pan"
          >
            drag · scroll · right-drag
          </p>
        </>
      ) : (
        <div className="absolute inset-0 grid place-items-center">
          <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center">
            <Box className="size-8 text-muted-foreground" aria-hidden />
            <p className="text-sm leading-6 text-muted-foreground">
              {phase === "loading" ? "Loading the case…" : phase === "error" ? `The 3D view could not start: ${failure}` : "No parts to show."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
