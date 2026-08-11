import { cn } from "@/ui/utils";
import { copperColor } from "@/lib/boardPalette.js";
import { activeBoardSide, BOARD_SIDES, boardSideChange } from "./viewportTools.js";

/**
 * The board-side widget — our answer to Vibe's view cube, bottom-right of the
 * PCB canvas.
 *
 * Vibe's cube snaps a 3D camera to a face. Our PCB canvas is genuinely flat:
 * there is no camera to snap, and a spinning cube over a 2D drawing would be
 * decoration pretending to be a control. The real question a flat board
 * answers is *which side am I looking at*, so this is a two-face flip — TOP
 * and BOTTOM — wired to the layer state the LayerBar already owns.
 *
 * What is taken verbatim from `ViewPlaneControl.js`: the SVG-in-a-0-0-100-100
 * viewBox construction, the faces coloured by their own axis palette (here,
 * the copper colour of the layer they select, so the widget and the layer
 * chips agree), the centre hub as a reset, and the interaction contract —
 * `role="button"`, Enter/Space, and `onPointerDown` stopped so a click on the
 * widget never starts a canvas drag.
 */
export default function BoardOrientationCube({
  scheme = "studio",
  activeLayer = "top",
  singleLayerMode = "off",
  hasBottom = true,
  onChange,
  bottomInset = 0,
  rightInset = 0,
  className,
}) {
  const side = activeBoardSide({ activeLayer, singleLayerMode });
  const isolated = String(singleLayerMode || "off") !== "off";

  const pick = (id) => onChange?.(boardSideChange(id, { activeLayer, singleLayerMode }));
  const reset = () => onChange?.({ activeLayer: "top", singleLayerMode: "off" });

  const keyActivate = (event, run) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    event.stopPropagation();
    run();
  };

  const faces = hasBottom ? BOARD_SIDES : BOARD_SIDES.filter((entry) => entry.id === "top");

  return (
    <div
      data-slot="board-orientation"
      data-side={side}
      className={cn("pointer-events-none absolute z-20", className)}
      style={{ right: `${rightInset + 12}px`, bottom: `${bottomInset + 12}px` }}
    >
      <div className="pointer-events-auto rounded-lg border border-white/10 bg-black/70 shadow-lg backdrop-blur-md">
        <svg
          width="72"
          height="72"
          viewBox="0 0 100 100"
          aria-label="Board side"
          onPointerDown={(event) => event.stopPropagation()}
        >
          {/* The board seen edge-on: two copper planes with the substrate
              between them. Picking a plane isolates that copper. */}
          {faces.map((face, position) => {
            const active = side === face.id && isolated;
            const y = face.id === "top" ? 22 : 60;
            return (
              <g
                key={face.id}
                role="button"
                tabIndex={0}
                aria-label={`View the ${face.label.toLowerCase()} side`}
                aria-pressed={active}
                className="cursor-pointer focus:outline-none"
                onClick={(event) => {
                  event.stopPropagation();
                  pick(face.id);
                }}
                onKeyDown={(event) => keyActivate(event, () => pick(face.id))}
              >
                <rect
                  x={14}
                  y={y}
                  width={72}
                  height={18}
                  rx={2}
                  fill={copperColor(scheme, face.layer)}
                  opacity={active ? 1 : 0.42}
                  stroke={active ? "#ffffff" : "transparent"}
                  strokeWidth={active ? 1.4 : 0}
                />
                <text
                  x={50}
                  y={y + 12.5}
                  textAnchor="middle"
                  fontSize={9}
                  fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
                  fill={active ? "#0b0b0b" : "#ffffff"}
                  opacity={active ? 1 : 0.8}
                  style={{ pointerEvents: "none" }}
                >
                  {face.label}
                </text>
                {position === 0 && faces.length > 1 ? (
                  <rect x={14} y={40} width={72} height={20} fill="#2a6b3d" opacity={0.32} />
                ) : null}
              </g>
            );
          })}

          {/* Centre hub — back to the whole stack, exactly like the donor's
              yellow "reset to default isometric" dot. */}
          <g
            role="button"
            tabIndex={0}
            aria-label="Show all layers"
            className="cursor-pointer focus:outline-none"
            onClick={(event) => {
              event.stopPropagation();
              reset();
            }}
            onKeyDown={(event) => keyActivate(event, reset)}
          >
            <circle cx={50} cy={50} r={9} fill="transparent" />
            <circle
              cx={50}
              cy={50}
              r={6.4}
              fill={isolated ? "rgba(252,215,74,0.95)" : "rgba(255,255,255,0.22)"}
              stroke={isolated ? "rgba(255,235,153,0.75)" : "rgba(255,255,255,0.35)"}
              strokeWidth={1.05}
            />
          </g>
        </svg>
      </div>
    </div>
  );
}
