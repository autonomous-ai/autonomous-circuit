// Circuit transport — the bridge between the React client and the Circuit Node
// server (viewer/src/server/circuit/).
//
// Source of truth: `docs/circuit-interfaces.md` §2.
//
// At runtime:
//   - Every command is `fetch POST /api/<command>` with a JSON body; errors
//     arrive as `IpcError {code, message, detail?}` bodies on 4xx/5xx and are
//     thrown as-is.
//   - Events arrive over ONE shared `EventSource` on `GET /api/events`; every
//     event is `{ …ChatEvent, projectId }` (SSE event name `chat_event`),
//     plus `catalog_changed {revision}`.
//
// Test seams (kept from the donor): `setTransport` / `__setTransportForTesting`
// override individual commands through the exported Proxy; `setTauriBridge`
// injects a fake invoke/listen pair that takes precedence over fetch/SSE
// (used by the chat-event flow tests).

// ---------------------------------------------------------------------------
// Shared interfaces (donor types kept verbatim; Circuit additions are marked)
// ---------------------------------------------------------------------------

export interface AppInfo {
  rootPath: string;
  appVersion: string;
  pid: number;
}

// Circuit's catalog kinds (contract §2): `tsx | json | svg | png | zip | csv | md`.
export type CatalogKind =
  | "tsx"
  | "json"
  | "svg"
  | "png"
  | "zip"
  | "csv"
  | "md";
export type SourceKindValue = "python" | "static";

/**
 * The board entry's grouped artifact (contract §2). Every URL carries
 * `?v=<mtime_nanos>-<size>` — use them verbatim, never strip the query.
 * Absent members are omitted (e.g. no fab packet yet, no bottom-side parts).
 */
export interface CatalogArtifact {
  /** `<stem>_review/_schematic.png` — the schematic render. */
  schematicUrl?: string;
  /** `<stem>_review/_pcb.png` — the board render (top). */
  pcbUrl?: string;
  /** `<stem>_review/_pcb_bottom.png` when parts sit on both sides. */
  pcbBottomUrl?: string;
  /** `<stem>.board.json` — the sidecar (camelCase machine contract). */
  metadataUrl?: string;
  /** `<stem>.circuit.json` — the compiled IR of record. */
  circuitJsonUrl?: string;
  /** `<stem>_fab/gerbers.zip` when fab-ready. */
  gerbersUrl?: string;
  /** `<stem>_fab/bom.csv` when fab-ready. */
  bomUrl?: string;
  /** `<stem>_fab/cpl.csv` when assembly. */
  cplUrl?: string;
  /** `<stem>_fab/ORDER.md` when fab-ready. */
  orderUrl?: string;
  /** `<stem>_fab/board.glb` best-effort 3D body (viewer tab is post-v1). */
  glbUrl?: string;
}

export interface CatalogEntry {
  file: string;
  kind: CatalogKind;
  sourceKind: SourceKindValue | null;
  url: string;
  artifact?: CatalogArtifact;
  relations?: Record<string, string>;
}

export interface Catalog {
  entries: CatalogEntry[];
  rootPath: string;
  revision: number;
}

export interface GenerationQueueItem {
  file: string;
  startedAt: number;
  kind: "step";
}

export interface GenerationStatus {
  queue: GenerationQueueItem[];
  pythonAvailable: boolean;
  lastError?: { file: string; message: string; at: number };
}

export type AssetKind = "output" | "source" | "artifact";

// Chat -----------------------------------------------------------------------

export interface ImageAttachment {
  /** Original filename, for display only (never used as a path server-side). */
  name?: string;
  /** MIME type, e.g. `image/png`. */
  mediaType: string;
  /** Raw file bytes, base64-encoded (no `data:` prefix). */
  dataBase64: string;
}

export interface StartTurnRequest {
  projectId: string;
  userMessage: string;
  /**
   * Optional reference images. The backend persists each into the project's
   * `inputs/` dir and points the model at them (it views them with Read).
   */
  images?: ImageAttachment[];
}

export interface StartTurnResponse {
  turnId: string;
}

export interface ApprovePlanRequest {
  projectId: string;
  planText: string;
}

export interface RequestPlanChangesRequest {
  projectId: string;
  feedback: string;
}

// A rehydrated assistant turn carries structured `blocks` so a reloaded turn
// rebuilds the same inline trace (reasoning + tool groups + per-segment timers)
// the live stream produced. Absent/empty for user turns and text-only turns.
export type ChatHistoryBlock =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string; at: number }
  | {
      kind: "tool_use";
      tool: string;
      toolUseId: string;
      input: unknown;
      status: "ok" | "error";
      resultSummary?: string;
      at: number;
      endedAt: number;
    };

export interface ChatSessionState {
  sessionId: string;
  turnInProgress: boolean;
  history: Array<{
    role: "user" | "assistant";
    content: string;
    at: number;
    blocks?: ChatHistoryBlock[];
  }>;
}

export type TurnPhase = "plan" | "implement";

// Every event carries `projectId` (the owning project) so the chat store routes
// a turn's events to the right project's conversation regardless of which one is
// on screen. Mirrors the server's SSE envelope.
export type ChatEvent = (
  | { kind: "turn_start"; turnId: string; phase: TurnPhase }
  | { kind: "plan_proposed"; turnId: string; plan: string }
  | { kind: "text_delta"; turnId: string; text: string }
  | { kind: "thinking_delta"; turnId: string; text: string }
  | { kind: "tool_use_start"; turnId: string; tool: string; toolUseId: string; input: unknown }
  | { kind: "tool_use_end"; turnId: string; tool: string; toolUseId: string; ok: boolean; resultSummary?: string }
  | { kind: "artifact_changed"; turnId: string; file: string; reason: "new" | "modified" }
  | { kind: "turn_end"; turnId: string }
  | { kind: "error"; turnId: string; message: string }
) & { projectId: string };

// Settings wire-shape padding ------------------------------------------------
// (The donor AppSettings shape rides the settings wire; FilamentKind survives
// only because AppSettings.defaultFilament names it. The slicer / printer /
// cloud / step / social / update / snapshot COMMAND families and their type
// blocks are deleted from the transport surface.)

export type FilamentKind = "PLA" | "PETG" | "TPU";

// Projects -------------------------------------------------------------------

export interface ProjectSummary {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  hasModel: boolean;
}

export interface CreateProjectRequest {
  name: string;
}

// App ------------------------------------------------------------------------

/**
 * Contract §2: claude on PATH · node ≥22.12 · toolchain installed
 * (`toolchain/node_modules` present) · python ≥3.10 · kicad-cli (reported,
 * not required). `healthy` = found AND the version clears the floor. The
 * client treats absent members as ok-but-unknown, so field drift never bricks
 * onboarding.
 */
export interface PrereqCheck {
  claudeCli: { found: boolean; version?: string };
  node?: { found: boolean; version?: string; healthy?: boolean };
  toolchain?: { found: boolean };
  python?: { found: boolean; version?: string; healthy?: boolean };
  kicadCli?: { found: boolean; version?: string };
}

export interface AppSettings {
  defaultFilament: FilamentKind;
  slicerBinaryPath: string;
  // OrcaSlicer machine+process config for `--load-settings` — `;`-joined
  // absolute JSON path(s). Empty = use OrcaSlicer's own default.
  slicerSettingsProfile?: string;
  // OrcaSlicer filament config for `--load-filaments`. Empty = none.
  slicerFilamentProfile?: string;
  // Preferred print device — a PrinterCard.id.
  defaultPrinterId?: string;
  // Captured by app_login_claude (`claude setup-token`).
  claudeOauthToken?: string;
  // Gates the first-run wizard with a single app_settings_read() call.
  hasOnboarded: boolean;
  // Update behavior. false (default) = prompt before downloading.
  autoUpdate: boolean;
  // Autopilot. true (default) = no plan-approval gate: after the model asks
  // its preference questions it builds + reviews unattended. false = manual
  // plan → Approve & build.
  autoBuild?: boolean;
  // Claude model passed to `claude --model`, set from the composer's model
  // switcher (app_set_model). undefined = the CLI's own default.
  model?: string;
}

// Claude Code install / sign-in ----------------------------------------------

/** Result of `app_install_claude_code`. */
export interface InstalledClaude {
  version: string;
  binaryPath: string;
}

export type ClaudeInstallProgress =
  | { stage: "downloading"; receivedBytes?: number; totalBytes?: number }
  | { stage: "running" }
  | { stage: "verifying" }
  | { stage: "done"; version: string; binaryPath: string }
  | { stage: "error"; message: string };

/**
 * Result of `app_auth_check` and `app_login_claude`. `authenticated` is the
 * single bit onboarding gates on; `source` (when present) is `"oauth_token"`
 * or `"credentials_file"`.
 */
export interface ClaudeAuthStatus {
  authenticated: boolean;
  source?: "oauth_token" | "credentials_file";
}

export type ClaudeLoginProgress =
  | { stage: "starting" }
  | { stage: "awaiting_browser"; url: string }
  | { stage: "verifying" }
  | { stage: "done" }
  | { stage: "error"; message: string };

export interface CatalogChangedEvent {
  revision: number;
}

export interface IpcError {
  code: string;
  message: string;
  detail?: unknown;
}

// ---------------------------------------------------------------------------
// Transport implementation: fetch POST /api/<cmd> + one shared EventSource on
// /api/events. An injected bridge (setTauriBridge) takes precedence — kept as
// a test seam for the chat-event flow tests.
// ---------------------------------------------------------------------------

type InvokeFn = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;
type ListenFn = <T>(
  event: string,
  handler: (payload: T) => void,
) => Promise<() => void>;

interface TauriBridge {
  invoke: InvokeFn;
  listen: ListenFn;
}

let cachedBridge: TauriBridge | null | undefined;

export function setTauriBridge(bridge: TauriBridge | null): void {
  cachedBridge = bridge;
}

/**
 * Adapt a Tauri-shaped `listen` (callback receives a full `Event<T>` object —
 * `{event, id, payload}`) to our {@link ListenFn} contract (callback receives
 * the payload `T` directly). Kept for the bridge test seam.
 */
export function adaptTauriListen(
  rawListen: (
    event: string,
    cb: (tauriEvent: { payload: unknown }) => void,
  ) => Promise<() => void>,
): ListenFn {
  return (<T>(event: string, handler: (payload: T) => void) =>
    rawListen(event, (tauriEvent) =>
      handler((tauriEvent as { payload: T }).payload),
    )) as ListenFn;
}

function activeBridge(): TauriBridge | null {
  return cachedBridge ?? null;
}

export function isTauriRuntime(): boolean {
  // Circuit is a web app; this reads true only when a bridge was injected
  // (tests). Kept because donor client code branches on it.
  return activeBridge() !== null;
}

/**
 * True only when running on Windows. Used to gate the in-window menu bar.
 */
export function isWindowsPlatform(): boolean {
  if (typeof navigator === "undefined") {
    return false;
  }
  const uaPlatform = (
    navigator as unknown as { userAgentData?: { platform?: string } }
  ).userAgentData?.platform;
  if (uaPlatform) {
    return uaPlatform.toLowerCase().includes("win");
  }
  return /windows|win32|win64/i.test(navigator.userAgent || "");
}

// Base URL for the Circuit API. Empty (same-origin relative) in the browser;
// tests point it at an ephemeral server via `setApiBase`.
let apiBaseOverride = "";

export function setApiBase(base: string): void {
  apiBaseOverride = String(base || "").replace(/\/+$/, "");
}

export function getApiBase(): string {
  return apiBaseOverride;
}

/**
 * Reset transport runtime state — bridge, API base, and the shared
 * EventSource. Primarily for tests.
 */
export function _resetTransportForTests(): void {
  cachedBridge = undefined;
  apiBaseOverride = "";
  closeSharedEventSource();
}

// Toggle transport call logging. On by default in dev; flip the global
// `__CIRCUIT_TRANSPORT_LOG__` to override at runtime from the console.
function transportLogEnabled(): boolean {
  const w = globalThis as unknown as { __CIRCUIT_TRANSPORT_LOG__?: boolean };
  if (typeof w.__CIRCUIT_TRANSPORT_LOG__ === "boolean") {
    return w.__CIRCUIT_TRANSPORT_LOG__;
  }
  return false;
}

const TRANSPORT_TAG = "[circuit:transport]";

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const bridge = activeBridge();
  if (transportLogEnabled()) {
    // eslint-disable-next-line no-console
    console.log(`${TRANSPORT_TAG} → ${cmd}`, args ?? {});
  }
  try {
    let result: T;
    if (bridge) {
      result = (await bridge.invoke(cmd, args)) as T;
    } else {
      const response = await fetch(`${apiBaseOverride}/api/${cmd}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(args ?? {}),
      });
      const text = await response.text();
      let payload: unknown = null;
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch {
          payload = null;
        }
      }
      if (!response.ok) {
        const ipc = payload as IpcError | null;
        throw (ipc && ipc.code
          ? ipc
          : {
              code: `HTTP_${response.status}`,
              message: text || response.statusText,
            }) as IpcError;
      }
      result = payload as T;
    }
    if (transportLogEnabled()) {
      // eslint-disable-next-line no-console
      console.log(`${TRANSPORT_TAG} ← ${cmd}`, result);
    }
    return result;
  } catch (err) {
    if (transportLogEnabled()) {
      // eslint-disable-next-line no-console
      console.error(`${TRANSPORT_TAG} ✕ ${cmd}`, err);
    }
    throw err;
  }
}

// --- shared EventSource ------------------------------------------------------

type EventSourceLike = {
  addEventListener(type: string, listener: (event: { data: string }) => void): void;
  removeEventListener(type: string, listener: (event: { data: string }) => void): void;
  close(): void;
};

type EventSourceCtor = new (url: string) => EventSourceLike;

let sharedEventSource: EventSourceLike | null = null;

function eventSourceCtor(): EventSourceCtor | null {
  const ctor = (globalThis as unknown as { EventSource?: EventSourceCtor }).EventSource;
  return typeof ctor === "function" ? ctor : null;
}

function ensureEventSource(): EventSourceLike | null {
  if (sharedEventSource) {
    return sharedEventSource;
  }
  const Ctor = eventSourceCtor();
  if (!Ctor) {
    return null;
  }
  // One EventSource per client (§2); the browser auto-reconnects.
  sharedEventSource = new Ctor(`${apiBaseOverride}/api/events`);
  return sharedEventSource;
}

function closeSharedEventSource(): void {
  if (sharedEventSource) {
    try {
      sharedEventSource.close();
    } catch {
      // already closed
    }
    sharedEventSource = null;
  }
}

export async function listenEvent<T>(
  event: string,
  handler: (payload: T) => void,
): Promise<() => void> {
  const bridge = activeBridge();
  if (bridge) {
    return bridge.listen<T>(event, handler);
  }
  const source = ensureEventSource();
  if (!source) {
    // No EventSource in this environment (e.g. bare Node test without a
    // polyfill) — a no-op unsubscribe keeps callers safe.
    return () => {};
  }
  const listener = (sseEvent: { data: string }) => {
    try {
      handler(JSON.parse(sseEvent.data) as T);
    } catch {
      // malformed frame — skip
    }
  };
  source.addEventListener(event, listener);
  return () => {
    source.removeEventListener(event, listener);
  };
}

// ---------------------------------------------------------------------------
// Public command surface (one function per contract §2 endpoint). Deleted
// families: slicer, printer, cloud, step, social, update, snapshots.
// ---------------------------------------------------------------------------

const transportBase = {
  // app
  app_info: () => invoke<AppInfo>("app_info"),
  app_prereq_check: () => invoke<PrereqCheck>("app_prereq_check"),
  app_settings_read: () => invoke<AppSettings>("app_settings_read"),
  app_settings_write: (settings: AppSettings) =>
    invoke<void>("app_settings_write", { settings }),
  app_install_claude_code: () =>
    invoke<InstalledClaude>("app_install_claude_code"),
  app_auth_check: () => invoke<ClaudeAuthStatus>("app_auth_check"),
  app_login_claude: () => invoke<ClaudeAuthStatus>("app_login_claude"),
  app_submit_login_code: (code: string) =>
    invoke<void>("app_submit_login_code", { code }),
  app_set_model: (model: string) =>
    invoke<AppSettings>("app_set_model", { model }),

  // catalog
  catalog_read: () => invoke<Catalog>("catalog_read"),
  project_catalog_read: (id: string) =>
    invoke<Catalog>("project_catalog_read", { id }),
  generation_status_read: () => invoke<GenerationStatus>("generation_status_read"),

  // files
  file_read_bytes: (file: string, asset: AssetKind) =>
    invoke<Uint8Array>("file_read_bytes", { file, asset }),
  file_save: (file: string, asset: AssetKind) =>
    invoke<string | null>("file_save", { file, asset }),
  file_reveal: (file: string, asset: AssetKind) =>
    invoke<void>("file_reveal", { file, asset }),
  file_import: () => invoke<string[]>("file_import"),

  // chat
  chat_start_turn: (req: StartTurnRequest) =>
    invoke<StartTurnResponse>("chat_start_turn", { req }),
  chat_approve_plan: (req: ApprovePlanRequest) =>
    invoke<StartTurnResponse>("chat_approve_plan", { req }),
  chat_request_plan_changes: (req: RequestPlanChangesRequest) =>
    invoke<StartTurnResponse>("chat_request_plan_changes", { req }),
  chat_cancel_turn: (turnId: string) =>
    invoke<void>("chat_cancel_turn", { turnId }),
  chat_session_state: (projectId: string) =>
    invoke<ChatSessionState>("chat_session_state", { projectId }),

  // project
  project_list: () => invoke<ProjectSummary[]>("project_list"),
  project_create: (req: CreateProjectRequest) =>
    invoke<ProjectSummary>("project_create", { req }),
  project_open: (id: string) =>
    invoke<{ workspaceRoot: string }>("project_open", { id }),
  project_rename: (id: string, name: string) =>
    invoke<ProjectSummary>("project_rename", { id, name }),
  project_delete: (id: string) => invoke<void>("project_delete", { id }),

  // events
  //
  // Generic event bus used by the chat store (`attachChatEventStream` →
  // `transport.events.subscribe("chat_event", …)`). `listenEvent` resolves
  // its unlisten asynchronously, but callers expect a *synchronous*
  // unsubscribe, so we hand back a thunk that cancels whether or not the
  // underlying listener has finished attaching yet.
  events: {
    subscribe(kind: string, handler: (payload: unknown) => void): () => void {
      let unlisten: (() => void) | null = null;
      let cancelled = false;
      listenEvent<unknown>(kind, handler)
        .then((un) => {
          if (cancelled) un();
          else unlisten = un;
        })
        .catch(() => {
          /* no event channel in this environment — nothing to unlisten */
        });
      return () => {
        cancelled = true;
        if (unlisten) {
          unlisten();
          unlisten = null;
        }
      };
    },
  },

  onChatEvent: (handler: (event: ChatEvent) => void) =>
    listenEvent<ChatEvent>("chat_event", handler),
  onCatalogChanged: (handler: (event: CatalogChangedEvent) => void) =>
    listenEvent<CatalogChangedEvent>("catalog_changed", handler),
  onClaudeInstallProgress: (handler: (event: ClaudeInstallProgress) => void) =>
    listenEvent<ClaudeInstallProgress>("claude_install_progress", handler),
  onClaudeLoginProgress: (handler: (event: ClaudeLoginProgress) => void) =>
    listenEvent<ClaudeLoginProgress>("claude_login_progress", handler),
};

export type Transport = typeof transportBase;

// ---------------------------------------------------------------------------
// Test seams (kept verbatim from the donor): all routes resolve to the same
// Proxy so `setTransport(mock)` is visible to callers that imported the
// `transport` object directly.
// ---------------------------------------------------------------------------

let transportOverride: Partial<Transport> | null = null;

// Proxy lets `import { transport }` callers see the override on every read,
// not just at module-init time. Important for tests that mock per-case.
export const transport = new Proxy(transportBase, {
  get(target, prop, receiver) {
    if (transportOverride && prop in transportOverride) {
      return (transportOverride as Record<PropertyKey, unknown>)[prop as string];
    }
    return Reflect.get(target, prop, receiver);
  },
}) as Transport;

export function getTransport(): Transport {
  return transport;
}

export function __setTransportForTesting(mock: Partial<Transport> | null): () => void {
  const previous = transportOverride;
  transportOverride = mock;
  return () => {
    transportOverride = previous;
  };
}

export function setTransport(mock: Partial<Transport>): void {
  transportOverride = mock;
}

export function resetTransport(): void {
  transportOverride = null;
  _resetTransportForTests();
}
