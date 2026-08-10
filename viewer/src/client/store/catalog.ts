// Catalog store for the Circuit episode workspace.
//
// Replaces the donor's cadjs `cadManifestStore` for the app: one zustand store
// holding the workspace catalog, refreshed via `transport.catalog_read()` and
// kept live by the `catalog_changed` SSE event (plus `artifact_changed` chat
// events, which we track per-file so the episode rail can show a "rendering"
// dot while a generation is writing files).
//
// The pure state transitions are exported as standalone functions so node:test
// can exercise them without zustand or a transport (see
// store/__tests__/catalogStore.test.js).

import { create } from "zustand";
import { getTransport } from "../lib/transport.ts";
import type { Catalog } from "../lib/transport.ts";

export const EMPTY_CATALOG: Catalog = Object.freeze({
  entries: [],
  rootPath: "",
  revision: 0,
}) as Catalog;

// Files touched by an `artifact_changed` chat event older than this are pruned
// — they no longer count as "generation in progress" for the episode rail.
export const ARTIFACT_ACTIVITY_TTL_MS = 45_000;

// ---------------------------------------------------------------------------
// Pure reducer logic (unit-tested without zustand)
// ---------------------------------------------------------------------------

export interface CatalogSnapshotSlice {
  catalog: Catalog;
  revision: number;
  hydrated: boolean;
  refreshing: boolean;
  error: string;
}

/** Fold a freshly read catalog into the store slice. */
export function applyCatalogSnapshot(catalog: Catalog | null | undefined): CatalogSnapshotSlice {
  const next =
    catalog && Array.isArray(catalog.entries) ? catalog : EMPTY_CATALOG;
  return {
    catalog: next,
    revision: Number(next.revision) || 0,
    hydrated: true,
    refreshing: false,
    error: "",
  };
}

/**
 * Whether a `catalog_changed {revision}` event warrants a refetch. Equal or
 * older revisions are echoes of a read we already hold; a missing/invalid
 * revision is treated as "unknown — refetch" so a lossy event never strands a
 * stale catalog.
 */
export function shouldRefetchForRevision(
  currentRevision: number,
  eventRevision: unknown,
): boolean {
  const incoming = Number(eventRevision);
  if (!Number.isFinite(incoming)) return true;
  return incoming > (Number(currentRevision) || 0);
}

/**
 * Record an `artifact_changed` file into the activity map and prune stale
 * entries. Value-stable enough for React: always returns a new object (the
 * caller only invokes it when something actually changed).
 */
export function noteArtifactActivity(
  activity: Record<string, number>,
  file: string,
  at: number,
  ttlMs: number = ARTIFACT_ACTIVITY_TTL_MS,
): Record<string, number> {
  const next: Record<string, number> = {};
  for (const [key, stamp] of Object.entries(activity || {})) {
    if (at - stamp <= ttlMs) next[key] = stamp;
  }
  const name = String(file || "").trim();
  if (name) next[name] = at;
  return next;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export interface CatalogState extends CatalogSnapshotSlice {
  /** file → epoch ms of its most recent artifact_changed event. */
  artifactActivity: Record<string, number>;
  refresh(options?: { markRefreshing?: boolean }): Promise<void>;
  noteArtifactChanged(file: string, at?: number): void;
  reset(): void;
}

// Monotonic guard so an out-of-order catalog_read response can't clobber a
// newer one (two refreshes can be in flight across a project switch).
let readSeq = 0;

export const useCatalogStore = create<CatalogState>((set, get) => ({
  catalog: EMPTY_CATALOG,
  revision: 0,
  hydrated: false,
  refreshing: false,
  error: "",
  artifactActivity: {},

  async refresh(options = {}) {
    const seq = ++readSeq;
    if (options.markRefreshing) set({ refreshing: true });
    try {
      const catalog = await getTransport().catalog_read();
      if (seq !== readSeq) return; // a newer read superseded this one
      set(applyCatalogSnapshot(catalog));
    } catch (err) {
      if (seq !== readSeq) return;
      const message =
        err && typeof err === "object" && "message" in err
          ? String((err as { message?: unknown }).message || "Failed to read catalog")
          : String(err || "Failed to read catalog");
      set({ refreshing: false, hydrated: true, error: message });
    }
  },

  noteArtifactChanged(file, at = Date.now()) {
    set((state) => ({
      artifactActivity: noteArtifactActivity(state.artifactActivity, file, at),
    }));
  },

  reset() {
    readSeq += 1;
    set({
      catalog: EMPTY_CATALOG,
      revision: 0,
      hydrated: false,
      refreshing: false,
      error: "",
      artifactActivity: {},
    });
  },
}));

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

let streamUnsubscribe: (() => void) | null = null;

/**
 * Subscribe the store to the transport's live events:
 *   - `catalog_changed {revision}` → refetch when the revision moved forward.
 *   - `chat_event {kind:"artifact_changed", file}` → stamp per-file activity so
 *     the episode rail can mark an episode "rendering" while its files churn.
 *
 * Idempotent — only the latest subscription is retained. Returns unsubscribe.
 */
export function attachCatalogStream(transport = getTransport()): () => void {
  if (streamUnsubscribe) {
    streamUnsubscribe();
    streamUnsubscribe = null;
  }
  const offChanged = transport.events.subscribe("catalog_changed", (payload: unknown) => {
    const revision = (payload as { revision?: unknown } | null)?.revision;
    const store = useCatalogStore.getState();
    if (shouldRefetchForRevision(store.revision, revision)) {
      void store.refresh();
    }
  });
  const offChat = transport.events.subscribe("chat_event", (payload: unknown) => {
    const event = payload as { kind?: string; file?: string } | null;
    if (event?.kind === "artifact_changed" && event.file) {
      useCatalogStore.getState().noteArtifactChanged(event.file);
    }
  });
  streamUnsubscribe = () => {
    offChanged();
    offChat();
  };
  return () => {
    if (streamUnsubscribe) {
      streamUnsubscribe();
      streamUnsubscribe = null;
    }
  };
}

export function detachCatalogStream(): void {
  if (streamUnsubscribe) {
    streamUnsubscribe();
    streamUnsubscribe = null;
  }
}
