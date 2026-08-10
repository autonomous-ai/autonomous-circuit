import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Camera, Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import { formatTimecode } from "@/lib/frameContext.js";

// Seek step for the arrow keys, in seconds.
const SEEK_STEP_S = 5;

/**
 * The vertical 9:16 episode player. A plain `<video controls>` letterboxed in
 * a dark stage (--ui-viewer-bg), with an episode title + duration overlay and
 * a "Send to AI" frame grab. The `src`/`poster` URLs are used verbatim — the
 * server's `?v=<mtime>-<size>` cache-bust token must never be stripped
 * (`<video>` caches harder than STL).
 *
 * Imperative handle: `{ seekTo(seconds), getVideo() }` — the storyboard strip
 * seeks through it.
 *
 * @param {{
 *   src: string,
 *   posterUrl?: string,
 *   title?: string,
 *   durationS?: number,           // sidecar duration; metadata overrides once loaded
 *   onTimeUpdate?: (seconds: number) => void,
 *   onFrameGrab?: (video: HTMLVideoElement, timeSeconds: number) => Promise<void>|void,
 *   className?: string,
 * }} props
 */
function EpisodePlayer(
  { src, posterUrl = "", title = "", durationS, onTimeUpdate, onFrameGrab, className },
  ref,
) {
  const videoRef = useRef(null);
  const [mediaDurationS, setMediaDurationS] = useState(null);
  const [grabbing, setGrabbing] = useState(false);
  const [grabError, setGrabError] = useState("");

  useImperativeHandle(
    ref,
    () => ({
      seekTo(seconds) {
        const video = videoRef.current;
        if (!video) return;
        const target = Math.max(0, Number(seconds) || 0);
        try {
          video.currentTime = target;
        } catch {
          /* metadata not ready yet — the user can retry once it loads */
        }
      },
      getVideo() {
        return videoRef.current;
      },
    }),
    [],
  );

  // Reset the transient per-episode state when the source changes.
  useEffect(() => {
    setMediaDurationS(null);
    setGrabError("");
  }, [src]);

  // Space / arrow keys on the stage wrapper. The native controls already
  // handle keys while the <video> itself is focused; this covers the common
  // case where the user clicked the stage or a storyboard chip last.
  const handleKeyDown = useCallback((event) => {
    const video = videoRef.current;
    if (!video) return;
    if (event.target === video) return; // native controls own it
    if (event.key === " " || event.key === "k") {
      event.preventDefault();
      if (video.paused) void video.play().catch(() => {});
      else video.pause();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      video.currentTime = Math.max(0, video.currentTime - SEEK_STEP_S);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      const max = Number.isFinite(video.duration) ? video.duration : Infinity;
      video.currentTime = Math.min(max, video.currentTime + SEEK_STEP_S);
    }
  }, []);

  const handleGrab = useCallback(async () => {
    const video = videoRef.current;
    if (!video || grabbing || typeof onFrameGrab !== "function") return;
    setGrabbing(true);
    setGrabError("");
    try {
      await onFrameGrab(video, video.currentTime || 0);
    } catch (error) {
      setGrabError(error instanceof Error ? error.message : "Could not grab the frame");
    } finally {
      setGrabbing(false);
    }
  }, [grabbing, onFrameGrab]);

  // Auto-dismiss the grab error so it doesn't linger over the stage.
  useEffect(() => {
    if (!grabError) return undefined;
    const timer = setTimeout(() => setGrabError(""), 4000);
    return () => clearTimeout(timer);
  }, [grabError]);

  const shownDuration = mediaDurationS ?? (Number.isFinite(Number(durationS)) ? Number(durationS) : null);

  return (
    <div
      data-slot="episode-player"
      tabIndex={0}
      onKeyDown={handleKeyDown}
      className={cn(
        "relative flex h-full w-full items-center justify-center overflow-hidden outline-none",
        className,
      )}
      style={{ backgroundColor: "var(--ui-viewer-bg)" }}
    >
      <video
        ref={videoRef}
        key={src /* remount per episode so poster + position reset */}
        src={src}
        poster={posterUrl || undefined}
        controls
        playsInline
        preload="metadata"
        data-slot="episode-video"
        className="mx-auto h-full max-h-full w-auto max-w-full bg-black object-contain"
        style={{ aspectRatio: "9 / 16" }}
        onLoadedMetadata={(event) => {
          const d = event.currentTarget.duration;
          if (Number.isFinite(d) && d > 0) setMediaDurationS(d);
        }}
        onTimeUpdate={(event) => onTimeUpdate?.(event.currentTarget.currentTime || 0)}
      />

      {/* Title + duration, top-left over the stage. */}
      {(title || shownDuration != null) && (
        <div
          data-slot="episode-player-overlay"
          className="pointer-events-none absolute left-3 top-3 z-10 flex items-center gap-2 rounded-full bg-black/55 px-3 py-1 text-xs text-white/90 backdrop-blur-sm"
        >
          {title ? <span className="font-medium">{title}</span> : null}
          {shownDuration != null ? (
            <span className="text-white/60">{formatTimecode(shownDuration)}</span>
          ) : null}
        </div>
      )}

      {/* Frame grab → chat attachment ("Send to AI"). */}
      {onFrameGrab ? (
        <button
          type="button"
          onClick={() => void handleGrab()}
          disabled={grabbing}
          title="Send this frame to the chat"
          data-slot="episode-frame-grab"
          className="absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-full bg-black/55 px-3 py-1.5 text-xs font-medium text-white/90 backdrop-blur-sm transition-colors hover:bg-black/75 hover:text-white disabled:opacity-60"
        >
          {grabbing ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Camera className="size-3.5" aria-hidden />
          )}
          Send to AI
        </button>
      ) : null}

      {grabError ? (
        <p
          data-slot="episode-frame-grab-error"
          className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-md bg-destructive/90 px-3 py-1 text-xs text-white"
        >
          {grabError}
        </p>
      ) : null}
    </div>
  );
}

export default forwardRef(EpisodePlayer);
