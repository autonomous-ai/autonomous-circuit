// Video transport — the bridge between the React client and the Video Node
// server (viewer/src/server/video/).
//
// Source of truth: `docs/video-interfaces.md` §2.
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
// Shared interfaces (donor types kept verbatim; Video additions are marked)
// ---------------------------------------------------------------------------

export interface AppInfo {
  rootPath: string;
  appVersion: string;
  pid: number;
}

// Video widening: the donor CAD kinds are kept for not-yet-excised client
// code; Video's catalog emits `mp4 | png | srt | py | json` (contract §2).
export type CatalogKind =
  | "step"
  | "stl"
  | "gcode"
  | "py"
  | "json"
  | "png"
  | "implicit"
  | "mp4"
  | "srt";
export type SourceKindValue = "python" | "static";

export interface CatalogPart {
  /** Part name (e.g. `chassis`), used as the display label. */
  name: string;
  /** Workspace-relative path of the part `.stl` (the catalog/entry key). */
  file: string;
  /** Cache-busted asset URL the viewer loads to render this part. */
  url: string;
}

/** One rendered shot clip grouped under its episode entry (Video, §2). */
export interface CatalogShot {
  id: string;
  file: string;
  url: string;
}

export interface CatalogArtifact {
  /** URL of the sibling `.stl` the viewer renders as a `.step` entry's preview. */
  stlUrl?: string;
  metadataUrl?: string;
  /**
   * For assemblies: one printable `.stl` per named part (at build origin). The
   * viewer groups these under the integrated model. Empty for single-solid projects.
   */
  parts?: CatalogPart[];
  /** Video: the episode's subtitles, when any dialogue exists. */
  srtUrl?: string;
  /** Video: the `_review/_poster.png` cover frame. */
  posterUrl?: string;
  /** Video: per-shot clips from `<stem>_shots/`, grouped under the episode. */
  shots?: CatalogShot[];
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

export interface StepSourceStatus {
  hasSource: boolean;
  sourcePath?: string;
  sourceKind?: "python";
}

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

// Slicer ---------------------------------------------------------------------
// (Donor types kept verbatim for not-yet-excised client code; the slicer /
// printer / cloud / step / social / update / snapshot COMMAND families are
// deleted from the transport surface.)

export type FilamentKind = "PLA" | "PETG" | "TPU";

export interface SliceRequest {
  meshFile: string;
  printerId: string;
  filament: FilamentKind;
}

export interface SliceStats {
  durationSeconds: number;
  filamentGrams: number;
  filamentMeters: number;
  layerCount: number;
  supportsUsed: boolean;
  gcodeFile: string;
  /** Sliced project `.3mf` (gcode embedded) for the cloud print path. */
  gcode3mfFile?: string;
  /** Static analysis of the produced G-code; absent if it couldn't be read. */
  validation?: SliceValidation;
  /**
   * Actionable warnings OrcaSlicer reported about the model itself during a
   * successful slice (floating regions, unsupported overhangs, …) — the same
   * "re-orient or enable supports" notices its GUI shows. Empty/absent when none.
   */
  slicerWarnings?: string[];
}

export interface SliceValidation {
  /** Structural integrity only (non-empty + has movement + has extrusion). */
  ok: boolean;
  errors: string[];
  /** Non-fatal findings: bed bounds, missing temps, unrecognized commands. */
  warnings: string[];
  movementCommands: number;
  extrusionMoves: number;
  temperatureCommands: number;
}

export interface SliceStatus {
  inFlight: boolean;
  stage?: "preparing" | "slicing" | "writing";
  progress?: number;
}

export interface SliceProgressEvent {
  stage: string;
  progress: number;
}

// Printer --------------------------------------------------------------------

// "bambustudio" is not a network printer — it hands the model off to the
// locally installed Bambu Studio app.
export type PrinterTransport = "lan" | "cloud" | "bambustudio";

export interface PrinterCard {
  id: string;
  model: string;
  transport: PrinterTransport;
  /** LAN IP — absent for cloud-only devices. */
  ipAddress?: string;
  hostName: string;
  /** Online flag from the cloud bind list — absent for LAN cards. */
  online?: boolean;
}

export interface AddPrinterRequest {
  ipAddress: string;
  accessCode: string;
  serial?: string;
}

export interface PrinterStatus {
  online: boolean;
  state: "idle" | "printing" | "paused" | "error";
  job?: { name: string; progress: number; etaSeconds: number };
}

export interface UploadGcodeRequest {
  printerId: string;
  gcodeFile: string;
  remoteName?: string;
}

export interface StartPrintRequest {
  printerId: string;
  remoteName: string;
  confirmed: true;
}

export interface PrintProgressEvent {
  printerId: string;
  state: string;
  progress: number;
}

export interface OpenInStudioRequest {
  /** Workspace-relative (catalog key) or absolute path to the model / gcode. */
  file: string;
}

/**
 * Which slicer app the open-in handoff would launch: Bambu Studio when
 * installed, else OrcaSlicer, else none.
 */
export type OpenTargetApp = "bambustudio" | "orcaslicer" | "none";

// Bambu cloud account --------------------------------------------------------

export type CloudRegion = "global" | "china";

export interface CloudLoginRequest {
  account: string;
  region?: CloudRegion;
}

export interface CloudLoginSubmit {
  account: string;
  code: string;
}

export interface CloudPasswordLogin {
  account: string;
  password: string;
  region?: CloudRegion;
}

export interface AddCloudPrinterRequest {
  serial: string;
  accessCode: string;
  name?: string;
}

export interface CloudLoginChallenge {
  /** "codeSent" | "success" | "needPassword" | "tfa" */
  kind: string;
  tfaKey?: string;
}

export interface CloudAccountStatus {
  signedIn: boolean;
  account?: string;
  region?: CloudRegion;
  expiresAt?: number;
  needsReauth: boolean;
}

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

// Result of publishing a project to panda-social (donor; command deleted).
export interface PublishResponse {
  designId: string;
  slug: string;
  title: string;
  status: string;
  projectUrl: string;
  alreadyPublished: boolean;
}

// Snapshots (git-tag-style model save states; donor; commands deleted) -------

export interface SnapshotSummary {
  id: string;
  label: string;
  createdAt: number;
}

export interface SnapshotRestore {
  summary: SnapshotSummary;
  chatRewound: boolean;
}

// App ------------------------------------------------------------------------

export interface PrereqCheck {
  claudeCli: { found: boolean; version?: string };
  python: { found: boolean; version?: string; healthy: boolean };
  slicer: { found: boolean; binaryPath: string };
  /** Video addition (§2): ffmpeg is required to stitch episodes. */
  ffmpeg?: { found: boolean; version?: string };
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

// Auto-update (donor types kept; commands deleted) ----------------------------

export interface UpdateInfo {
  version: string;
  currentVersion: string;
  notes?: string;
  date?: string;
}

export type UpdateEvent =
  | { status: "checking" }
  | { status: "up_to_date" }
  | ({ status: "available" } & UpdateInfo)
  | { status: "downloading"; downloadedBytes: number; totalBytes?: number }
  | { status: "ready"; version: string }
  | { status: "error"; message: string };

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

/** Result of `app_install_orcaslicer` (donor; command deleted). */
export interface InstalledSlicer {
  version: string;
  binaryPath: string;
}

export type SlicerInstallProgress =
  | { stage: "downloading"; receivedBytes?: number; totalBytes?: number }
  | { stage: "extracting" }
  | { stage: "installing" }
  | { stage: "verifying" }
  | { stage: "done"; version: string; binaryPath: string }
  | { stage: "error"; message: string };

// panda-social (donor types kept; commands deleted) ---------------------------

export interface SocialUser {
  id: string;
  username: string;
  displayName?: string;
}

export interface SocialLoginResult {
  user: SocialUser;
}

export interface SocialProfile {
  id: string;
  username: string;
  displayName?: string;
  email?: string;
  avatarUrl?: string;
  bio?: string;
  modelCount?: number;
  followerCount?: number;
  followingCount?: number;
  verified?: boolean;
  plan?: string;
  planStatus?: string;
}

export interface SocialDesign {
  id: string;
  slug?: string;
  title?: string;
  thumbnailUrl?: string;
  status?: string;
}

export type SocialLoginProgress =
  | { stage: "starting" }
  | { stage: "awaiting_browser"; url: string }
  | { stage: "verifying" }
  | { stage: "done"; user: SocialUser }
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
  // Video is a web app; this reads true only when a bridge was injected
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

// Base URL for the Video API. Empty (same-origin relative) in the browser;
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
// `__VIDEO_TRANSPORT_LOG__` to override at runtime from the console.
function transportLogEnabled(): boolean {
  const w = globalThis as unknown as { __VIDEO_TRANSPORT_LOG__?: boolean };
  if (typeof w.__VIDEO_TRANSPORT_LOG__ === "boolean") {
    return w.__VIDEO_TRANSPORT_LOG__;
  }
  return false;
}

const TRANSPORT_TAG = "[video:transport]";

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
