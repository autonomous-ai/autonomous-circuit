import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import ProjectMenu from "@/components/project/ProjectMenu.jsx";
import SidebarUserCard from "@/components/workbench/SidebarUserCard.jsx";
import { addPendingAttachment, setPendingViewContext } from "@/store/chat.js";
import { blobToAttachment } from "@/components/chat/attachments.js";
import {
  FOCUS_CHAT_INPUT_EVENT,
  prefillChatInput,
} from "@/components/chat/chatInputHelpers.js";
import { buildFrameContextNote, formatTimecode } from "@/lib/frameContext.js";
import { episodeLabel, episodeStatus, episodeStem } from "@/lib/episodeModel.js";
import EpisodePlayer from "./EpisodePlayer.jsx";
import StoryboardStrip from "./StoryboardStrip.jsx";
import CastPanel from "./CastPanel.jsx";
import { normalizeShots, shotOffsets, shotIndexAtTime } from "./storyboardModel.js";

const STATUS_DOT = Object.freeze({
  ready: "bg-emerald-500",
  rendering: "bg-amber-400 animate-pulse",
  pending: "bg-muted-foreground/40",
});

/** Draw the video's current frame into a PNG blob. */
async function grabFrameBlob(video) {
  const width = video.videoWidth || 0;
  const height = video.videoHeight || 0;
  if (!width || !height) {
    throw new Error("No frame to grab yet — play the episode first");
  }
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Frame capture is not supported here");
  ctx.drawImage(video, 0, 0, width, height);
  const blob = await new Promise((resolve) =>
    canvas.toBlob((b) => resolve(b), "image/png"),
  );
  if (!blob) throw new Error("Frame capture failed");
  return blob;
}

/**
 * The episode workspace — replaces the donor's CadWorkspace behind the same
 * seam from main.jsx. Layout: header (project name + settings) · left episode
 * rail (E01…E60 chips with status dots) · collapsible cast panel · center
 * vertical 9:16 player · bottom storyboard strip.
 *
 * The layout-coordination props (`onModelsSidebarChange`, `onToolsSheetChange`,
 * `closeLeftSidebarSignal`) are kept from the donor seam so AppRoot's
 * chat-width clamp math is unchanged: the episode rail is fixed-width and part
 * of the workspace floor, so both panels report closed/0; the close signal
 * collapses the cast panel (the only reclaimable width here).
 */
export default function EpisodeWorkspace({
  manifestRevision = 0,
  episodeEntries = [],
  seriesEntry = null,
  artifactActivity = {},
  catalogHydrated = false,
  catalogRefreshing = false,
  catalogError = "",
  onModelsSidebarChange,
  onToolsSheetChange,
  closeLeftSidebarSignal = 0,
  onOpenAccountScreen,
}) {
  const [selectedFile, setSelectedFile] = useState("");
  const [castOpen, setCastOpen] = useState(false);
  const [sidecar, setSidecar] = useState(null);
  const [currentTimeS, setCurrentTimeS] = useState(0);
  const playerRef = useRef(null);

  // The donor's collapsible panels don't exist here; report them closed once
  // so AppRoot's chat clamp treats the whole workspace as the viewer floor.
  useEffect(() => {
    onModelsSidebarChange?.(false, 0);
    onToolsSheetChange?.(false, 0);
  }, [onModelsSidebarChange, onToolsSheetChange]);

  // Chat dragging wide asks the workspace to give space back — collapse the
  // cast panel (the only optional width).
  const lastCloseSignal = useRef(closeLeftSidebarSignal);
  useEffect(() => {
    if (closeLeftSidebarSignal !== lastCloseSignal.current) {
      lastCloseSignal.current = closeLeftSidebarSignal;
      setCastOpen(false);
    }
  }, [closeLeftSidebarSignal]);

  // Keep a valid selection: follow the newest episode until the user picks
  // one; if the selected file leaves the catalog, fall back to the newest.
  const selectedEntry = useMemo(
    () => episodeEntries.find((entry) => entry.file === selectedFile) || null,
    [episodeEntries, selectedFile],
  );
  useEffect(() => {
    if (!selectedEntry && episodeEntries.length) {
      setSelectedFile(episodeEntries[episodeEntries.length - 1].file);
    }
  }, [selectedEntry, episodeEntries]);

  // Reset per-episode transient state when the selection moves.
  useEffect(() => {
    setCurrentTimeS(0);
    setSidecar(null);
  }, [selectedFile]);

  // Sidecar (.episode.json) fetch — keyed on metadataUrl, which carries the
  // ?v=<mtime>-<size> token, so a re-render of the episode refetches on its
  // own. NEVER strip the query.
  const metadataUrl = String(selectedEntry?.artifact?.metadataUrl || "");
  useEffect(() => {
    if (!metadataUrl) {
      setSidecar(null);
      return undefined;
    }
    let cancelled = false;
    fetch(metadataUrl)
      .then((response) => {
        if (!response.ok) throw new Error(`sidecar read failed (${response.status})`);
        return response.json();
      })
      .then((data) => {
        if (!cancelled) setSidecar(data);
      })
      .catch(() => {
        if (!cancelled) setSidecar(null);
      });
    return () => {
      cancelled = true;
    };
  }, [metadataUrl, manifestRevision]);

  const selectedStem = episodeStem(selectedEntry?.file);
  const episodeDurationS =
    Number(sidecar?.episode?.durationS ?? sidecar?.validation?.durationS) || undefined;
  const episodeTitle =
    String(sidecar?.episode?.title || "").trim() || episodeLabel(selectedStem);

  // Active shot (for the frame-context note) from the same offsets the strip uses.
  const shots = useMemo(
    () => normalizeShots(sidecar, selectedEntry?.artifact?.shots),
    [sidecar, selectedEntry],
  );
  const offsets = useMemo(() => shotOffsets(shots, episodeDurationS), [shots, episodeDurationS]);

  const handleSeek = useCallback((seconds) => {
    playerRef.current?.seekTo(seconds);
  }, []);

  const handlePrefillNote = useCallback((text) => {
    prefillChatInput(text);
  }, []);

  // "Send to AI": grab the current frame, attach it to the composer, and set
  // the model-facing timecode note (repurposed pendingViewContext plumbing).
  const handleFrameGrab = useCallback(
    async (video, timeSeconds) => {
      const blob = await grabFrameBlob(video);
      const timecode = formatTimecode(timeSeconds);
      const attachment = await blobToAttachment(blob, {
        name: `${selectedStem || "frame"}-${timecode.replace(/:/g, "m")}s.png`,
      });
      addPendingAttachment(attachment);
      const shotIndex = shotIndexAtTime(offsets, timeSeconds);
      setPendingViewContext(
        buildFrameContextNote({
          episode: selectedStem,
          timeSeconds,
          shotId: shotIndex >= 0 ? shots[shotIndex]?.id : "",
        }),
      );
      if (typeof window !== "undefined") {
        window.dispatchEvent(new Event(FOCUS_CHAT_INPUT_EVENT));
      }
    },
    [selectedStem, offsets, shots],
  );

  const hasEpisodes = episodeEntries.length > 0;

  return (
    <div data-slot="episode-workspace" className="flex h-full w-full flex-col bg-background">
      {/* Header: project name + settings. */}
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-2.5">
        <span className="px-1 text-sm font-semibold tracking-tight text-foreground">Autonomous Circuit</span>
        <ProjectMenu />
        {catalogRefreshing ? (
          <Loader2
            className="size-3.5 animate-spin text-muted-foreground"
            aria-label="Refreshing catalog"
          />
        ) : null}
        <div className="ml-auto flex items-center gap-1">
          {/* Local-only workspace card doubles as the settings affordance. */}
          <SidebarUserCard onOpenAccountScreen={onOpenAccountScreen} />
        </div>
      </header>

      {catalogError ? (
        <p
          data-slot="episode-catalog-error"
          className="border-b border-destructive/40 bg-destructive/10 px-3 py-1 text-xs text-destructive"
        >
          {catalogError}
        </p>
      ) : null}

      <div className="flex min-h-0 flex-1">
        {/* Episode rail. */}
        <nav
          data-slot="episode-rail"
          aria-label="Episodes"
          className="scrollbar-thin flex w-16 shrink-0 flex-col items-center gap-1.5 overflow-y-auto border-r border-border/60 py-3"
        >
          {episodeEntries.map((entry) => {
            const stem = episodeStem(entry.file);
            const status = episodeStatus(entry, { activity: artifactActivity });
            const active = entry.file === selectedEntry?.file;
            return (
              <button
                key={entry.file}
                type="button"
                onClick={() => setSelectedFile(entry.file)}
                title={`${stem} — ${status}`}
                data-slot="episode-chip"
                data-status={status}
                aria-current={active ? "true" : undefined}
                className={cn(
                  "relative flex h-9 w-12 shrink-0 items-center justify-center rounded-lg border font-mono text-[11px] transition-colors",
                  active
                    ? "border-primary/60 bg-primary/10 text-foreground"
                    : "border-border/60 bg-card/60 text-muted-foreground hover:bg-accent hover:text-foreground",
                )}
              >
                {episodeLabel(stem)}
                <span
                  aria-hidden
                  className={cn(
                    "absolute right-1 top-1 size-1.5 rounded-full",
                    STATUS_DOT[status] || STATUS_DOT.pending,
                  )}
                />
              </button>
            );
          })}
          {!hasEpisodes && catalogHydrated ? (
            <span className="px-1 text-center text-[10px] leading-4 text-muted-foreground/70">
              No episodes yet
            </span>
          ) : null}
        </nav>

        <CastPanel
          seriesEntry={seriesEntry}
          open={castOpen}
          onToggle={() => setCastOpen((value) => !value)}
        />

        {/* Stage + storyboard. */}
        <div className="flex min-w-0 flex-1 flex-col">
          {selectedEntry ? (
            <>
              <EpisodePlayer
                ref={playerRef}
                src={selectedEntry.url}
                posterUrl={selectedEntry.artifact?.posterUrl || ""}
                title={episodeTitle}
                durationS={episodeDurationS}
                onTimeUpdate={setCurrentTimeS}
                onFrameGrab={handleFrameGrab}
                className="min-h-0 flex-1"
              />
              <StoryboardStrip
                sidecar={sidecar}
                artifactShots={selectedEntry.artifact?.shots}
                episodeDurationS={episodeDurationS}
                currentTimeS={currentTimeS}
                onSeek={handleSeek}
                onPrefillNote={handlePrefillNote}
              />
            </>
          ) : (
            <div
              data-slot="episode-empty-state"
              className="grid min-h-0 flex-1 place-items-center"
              style={{ backgroundColor: "var(--ui-viewer-bg)" }}
            >
              <div className="flex flex-col items-center gap-3 px-6 text-center">
                {catalogHydrated || catalogError ? (
                  <>
                    <span className="text-5xl font-semibold tracking-tight text-white">
                      Circuit
                    </span>
                    <span className="text-xs font-medium uppercase tracking-[0.2em] text-white/40">
                      Autonomous Circuit
                    </span>
                    <p className="max-w-xs text-sm leading-6 text-white/60">
                      Chat a story into short vertical episodes. Describe your
                      drama in the panel on the right — the first episode lands
                      here.
                    </p>
                  </>
                ) : (
                  <Loader2 className="size-5 animate-spin text-white/60" aria-hidden />
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
