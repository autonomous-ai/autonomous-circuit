import { Eye, EyeOff, Layers, Palette } from "lucide-react";
import { cn } from "@/ui/utils";
import {
  HIGHLIGHT_METHOD_LABEL,
  LAYER_LABELS,
  OBJECT_CLASSES,
  copperColor,
  palette,
} from "@/lib/boardPalette.js";

const SINGLE_LAYER_LABEL = { off: "All layers", grey: "Grey others", hide: "Hide others" };

/**
 * The layer bar along the bottom of the PCB canvas — Altium's layer tabs, with
 * KiCad's Objects tab folded in.
 *
 * A layer chip does two things at once, which is the part that makes it feel
 * fast: the coloured swatch is the visibility toggle, and clicking the name
 * makes that layer active (what single-layer mode isolates, what the HUD
 * reports). Object classes sit to the right on the same row so silkscreen,
 * paste and courtyard can be dropped without opening a dialog.
 */
export default function LayerBar({
  layers = [],
  activeLayer = "top",
  visibleLayers,
  onToggleLayer,
  onActivateLayer,
  visibleClasses,
  onToggleClass,
  singleLayerMode = "off",
  onCycleSingleLayer,
  scheme = "studio",
  onCycleScheme,
  highlightMethod = "dim",
  onCycleHighlightMethod,
  maskLevel = 3,
  units = "mm",
  onToggleUnits,
  className,
}) {
  const colors = palette(scheme);
  return (
    <div
      data-slot="layer-bar"
      className={cn(
        "scrollbar-thin flex h-9 shrink-0 items-center gap-1 overflow-x-auto border-t border-border/60 bg-card/40 px-2",
        className,
      )}
    >
      {layers.map((layer) => {
        const visible = !visibleLayers || visibleLayers.has(layer);
        const active = layer === activeLayer;
        return (
          <div
            key={layer}
            data-slot="layer-chip"
            data-layer={layer}
            data-active={active ? "true" : "false"}
            data-visible={visible ? "true" : "false"}
            className={cn(
              "flex shrink-0 items-center gap-1 rounded border px-1 py-0.5 text-[11px] transition-colors",
              active ? "border-primary/60 bg-primary/10" : "border-border/50 bg-background/40",
            )}
          >
            <button
              type="button"
              title={`${visible ? "Hide" : "Show"} ${LAYER_LABELS[layer] || layer}`}
              aria-label={`${visible ? "Hide" : "Show"} ${LAYER_LABELS[layer] || layer}`}
              onClick={() => onToggleLayer?.(layer)}
              className="size-3 shrink-0 rounded-[2px] border border-black/40 transition-opacity"
              style={{ backgroundColor: copperColor(scheme, layer), opacity: visible ? 1 : 0.22 }}
            />
            <button
              type="button"
              onClick={() => onActivateLayer?.(layer)}
              className={cn(
                "font-mono tracking-tight transition-colors",
                active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
                visible ? "" : "line-through opacity-50",
              )}
            >
              {LAYER_LABELS[layer] || layer}
            </button>
          </div>
        );
      })}

      <span className="mx-1 h-4 w-px shrink-0 bg-border/60" aria-hidden />

      {OBJECT_CLASSES.map((entry) => {
        const on = !visibleClasses || visibleClasses.has(entry.id);
        return (
          <button
            key={entry.id}
            type="button"
            onClick={() => onToggleClass?.(entry.id)}
            data-slot="object-class-toggle"
            data-class={entry.id}
            aria-pressed={on}
            title={`${on ? "Hide" : "Show"} ${entry.label.toLowerCase()}`}
            className={cn(
              "shrink-0 rounded px-1.5 py-0.5 text-[11px] transition-colors",
              on ? "bg-accent/60 text-foreground" : "text-muted-foreground/50 line-through hover:text-muted-foreground",
            )}
          >
            {entry.label}
          </button>
        );
      })}

      <div className="ml-auto flex shrink-0 items-center gap-1 pl-2">
        <button
          type="button"
          onClick={onCycleSingleLayer}
          data-slot="single-layer-toggle"
          data-mode={singleLayerMode}
          title="Single layer mode (Shift+S)"
          className={cn(
            "flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] transition-colors",
            singleLayerMode === "off"
              ? "text-muted-foreground hover:text-foreground"
              : "bg-primary/15 text-foreground",
          )}
        >
          {singleLayerMode === "off" ? <Eye className="size-3" aria-hidden /> : <EyeOff className="size-3" aria-hidden />}
          {SINGLE_LAYER_LABEL[singleLayerMode]}
        </button>
        <button
          type="button"
          onClick={onCycleHighlightMethod}
          data-slot="highlight-method-toggle"
          data-method={highlightMethod}
          title="Highlight method — Normal / Dim / Mask (M). [ and ] change mask depth"
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <Layers className="size-3" aria-hidden />
          {HIGHLIGHT_METHOD_LABEL[highlightMethod]}
          {highlightMethod === "normal" ? null : (
            <span className="font-mono text-[10px] opacity-60">{maskLevel}</span>
          )}
        </button>
        <button
          type="button"
          onClick={onToggleUnits}
          data-slot="units-toggle"
          title="Toggle units (Q)"
          className="rounded px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground transition-colors hover:text-foreground"
        >
          {units}
        </button>
        <button
          type="button"
          onClick={onCycleScheme}
          data-slot="palette-toggle"
          title="Colour scheme"
          className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
        >
          <Palette className="size-3" aria-hidden />
          {colors.label}
        </button>
      </div>
    </div>
  );
}
