import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Users } from "lucide-react";
import { cn } from "@/ui/utils";

const LOOK_EXCERPT_CHARS = 90;

function lookExcerpt(look) {
  const text = String(look || "").trim();
  if (text.length <= LOOK_EXCERPT_CHARS) return text;
  return `${text.slice(0, LOOK_EXCERPT_CHARS).trimEnd()}…`;
}

/**
 * Normalize whatever shape the series bible artifact carries into cast cards.
 * Accepts `{cast: [...]}` (the expected series.json shape), `{CAST: [...]}`
 * (a straight dump of the series.py constant), or a bare array. Exported for
 * tests.
 */
export function normalizeCast(data) {
  const list = Array.isArray(data)
    ? data
    : Array.isArray(data?.cast)
      ? data.cast
      : Array.isArray(data?.CAST)
        ? data.CAST
        : [];
  return list
    .map((member, index) => {
      const refImages = Array.isArray(member?.ref_images)
        ? member.ref_images
        : Array.isArray(member?.refImages)
          ? member.refImages
          : [];
      const refImage = [refImages[0], member?.refImage, member?.image]
        .map((v) => String(v || "").trim())
        .find(Boolean) || "";
      return {
        id: String(member?.id ?? `cast_${index}`),
        name: String(member?.name || member?.id || "").trim(),
        look: String(member?.look || "").trim(),
        voice: String(member?.voice || "").trim(),
        refImage,
      };
    })
    .filter((member) => member.name);
}

/**
 * Collapsible cast panel, right of the episode rail. When the catalog carries
 * a series bible artifact (series.json), fetch it (URL used verbatim — it
 * carries the ?v= cache-bust) and render one card per character: name, look
 * excerpt, voice tag, and the reference image when a URL is present. Without
 * a bible yet, an empty state explains what will appear.
 *
 * @param {{
 *   seriesEntry?: {url?: string}|null,
 *   open: boolean,
 *   onToggle: () => void,
 * }} props
 */
export default function CastPanel({ seriesEntry, open, onToggle }) {
  const url = String(seriesEntry?.url || "");
  const [cast, setCast] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!url) {
      setCast([]);
      setError("");
      return undefined;
    }
    let cancelled = false;
    // NEVER strip the query — ?v= is the cache-bust; a rewritten bible gets a
    // new URL and this effect re-fetches on it.
    fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`series bible read failed (${response.status})`);
        return response.json();
      })
      .then((data) => {
        if (cancelled) return;
        setCast(normalizeCast(data));
        setError("");
      })
      .catch((err) => {
        if (cancelled) return;
        setCast([]);
        setError(err instanceof Error ? err.message : "Could not read the series bible");
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  if (!open) {
    return (
      <button
        type="button"
        onClick={onToggle}
        title="Show cast"
        aria-label="Show cast"
        data-slot="cast-panel-toggle"
        className="flex w-9 shrink-0 flex-col items-center gap-2 border-r border-border/60 py-3 text-muted-foreground transition-colors hover:text-foreground"
      >
        <Users className="size-4" aria-hidden />
        <ChevronRight className="size-3.5" aria-hidden />
      </button>
    );
  }

  return (
    <aside
      data-slot="cast-panel"
      className="flex w-56 shrink-0 flex-col border-r border-border/60"
    >
      <header className="flex h-9 shrink-0 items-center gap-2 border-b border-border/60 px-3">
        <Users className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Cast
        </span>
        <button
          type="button"
          onClick={onToggle}
          title="Hide cast"
          aria-label="Hide cast"
          className="ml-auto grid size-6 place-items-center rounded text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        >
          <ChevronLeft className="size-3.5" aria-hidden />
        </button>
      </header>

      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto p-2">
        {!url || (!cast.length && !error) ? (
          <p
            data-slot="cast-panel-empty"
            className="px-2 py-6 text-center text-xs leading-5 text-muted-foreground"
          >
            Cast appears once your series bible is created.
          </p>
        ) : error ? (
          <p className="px-2 py-6 text-center text-xs text-destructive">{error}</p>
        ) : (
          <div className="flex flex-col gap-2">
            {cast.map((member) => (
              <div
                key={member.id}
                data-slot="cast-card"
                className="flex gap-2.5 rounded-lg border border-border/60 bg-card/60 p-2.5"
              >
                {member.refImage ? (
                  <img
                    src={member.refImage}
                    alt={member.name}
                    loading="lazy"
                    className="size-12 shrink-0 rounded-md border border-border/60 object-cover"
                  />
                ) : (
                  <div className="grid size-12 shrink-0 place-items-center rounded-md border border-border/60 bg-muted text-muted-foreground/60">
                    <Users className="size-5" aria-hidden />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground">{member.name}</p>
                  {member.look ? (
                    <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">
                      {lookExcerpt(member.look)}
                    </p>
                  ) : null}
                  {member.voice ? (
                    <span className="mt-1 inline-block rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
                      {member.voice}
                    </span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
