import { StrictMode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import AppErrorBoundary from "./components/AppErrorBoundary.jsx";
import EpisodeWorkspace from "./components/episode/EpisodeWorkspace.jsx";
import ChatSidebar, { readStoredChatSidebarWidth, persistChatSidebarWidth } from "./components/chat/ChatSidebar";
import { CHAT_MIN_WIDTH, maxChatWidth } from "./workbench/chatLayout.js";
import WindowMenuBar from "./components/WindowMenuBar.jsx";
import WelcomeScreen from "./components/onboarding/WelcomeScreen.jsx";
import { shouldOnboard } from "./components/onboarding/onboardingHelpers.js";
import AccountScreen from "./components/workbench/AccountScreen.jsx";
import faviconUrl from "./assets/favicon.ico";
import "./styles/globals.css";
import { isWindowsPlatform, transport } from "./lib/transport.ts";
import { attachCatalogStream, useCatalogStore } from "./store/catalog.ts";
import { selectEpisodeEntries, selectSeriesEntry } from "./lib/episodeModel.js";
import { setProject as setChatProject } from "./store/chat.js";
import { useProjectsStore } from "./store/projects.ts";

const ROOT_ID = "root";
const ROOT_CACHE_KEY = "__circuitViewerRoot";

function ensureFavicon() {
  if (typeof document === "undefined") {
    return;
  }

  let icon = document.querySelector('link[rel="icon"]');
  if (!icon) {
    icon = document.createElement("link");
    icon.rel = "icon";
    document.head.appendChild(icon);
  }
  icon.type = "image/x-icon";
  icon.href = `${faviconUrl}?v=circuit-workspace`;
}

// In the production desktop bundle the WKWebView's native right-click menu
// exposes "Reload"/"Back"/"Forward", which silently throws away in-flight chat
// and viewer state. Suppress the native context menu in production builds only;
// in dev we keep it so the inspector's "Inspect Element" stays reachable.
function suppressNativeContextMenuInProduction() {
  if (typeof document === "undefined" || !import.meta.env.PROD) {
    return;
  }
  document.addEventListener("contextmenu", (event) => {
    event.preventDefault();
  });
}

function bootstrap() {
  const rootElement = document.getElementById(ROOT_ID);
  if (!rootElement) {
    throw new Error(`Missing #${ROOT_ID} mount point.`);
  }
  ensureFavicon();
  suppressNativeContextMenuInProduction();
  document.title = "Autonomous Circuit";
  const cachedRoot = globalThis[ROOT_CACHE_KEY];
  const root = cachedRoot?.element === rootElement && cachedRoot?.root
    ? cachedRoot.root
    : createRoot(rootElement);
  globalThis[ROOT_CACHE_KEY] = {
    element: rootElement,
    root
  };
  root.render(
    <StrictMode>
      <AppErrorBoundary>
        <AppRoot />
      </AppErrorBoundary>
    </StrictMode>,
  );
}

function useOnboardingGate() {
  // Tri-state: null = still probing; true = wizard should show; false = run app.
  const [needsOnboarding, setNeedsOnboarding] = useState(null);

  useEffect(() => {
    let cancelled = false;
    transport
      .app_settings_read()
      .then((settings) => {
        if (cancelled) return;
        // Bring-your-own Claude Code is the primary path, so the gate keys
        // solely on hasOnboarded — an onboarded local-only user is fully valid
        // and is left alone. "At least one working method" is enforced inside
        // the wizard, not here. See shouldOnboard().
        setNeedsOnboarding(shouldOnboard(settings));
      })
      .catch(() => {
        if (cancelled) return;
        // If settings cannot be read (missing/corrupt), fail safe into the
        // wizard so first-run users are not dropped into the workspace.
        setNeedsOnboarding(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Stable callbacks so consumers' effects don't re-subscribe every render.
  const complete = useCallback(() => setNeedsOnboarding(false), []);
  const restart = useCallback(() => setNeedsOnboarding(true), []);
  return [needsOnboarding, complete, restart];
}

function AppRoot() {
  const catalog = useCatalogStore((state) => state.catalog);
  const revision = useCatalogStore((state) => state.revision);
  const catalogHydrated = useCatalogStore((state) => state.hydrated);
  const catalogRefreshing = useCatalogStore((state) => state.refreshing);
  const catalogError = useCatalogStore((state) => state.error);
  const artifactActivity = useCatalogStore((state) => state.artifactActivity);
  const [needsOnboarding, completeOnboarding, restartOnboarding] = useOnboardingGate();
  const onboarded = needsOnboarding === false;

  // Live catalog: `catalog_changed` SSE → refetch; `artifact_changed` chat
  // events → per-file activity for the episode rail's "rendering" dot.
  useEffect(() => attachCatalogStream(), []);

  // Native "Run Setup Again…" menu item emits `run_setup_again`. Clear the
  // persisted flag so the wizard sticks even if the user quits mid-setup,
  // then re-show it in place.
  useEffect(() => {
    const unsubscribe = transport.events.subscribe("run_setup_again", () => {
      transport
        .app_settings_read()
        .then((settings) =>
          transport.app_settings_write({ ...settings, hasOnboarded: false }),
        )
        .catch((err) => console.warn("Failed to reset onboarding flag", err))
        .finally(() => restartOnboarding());
    });
    return () => unsubscribe();
  }, [restartOnboarding]);

  // Live width of the resizable chat panel. Lifted here because it drives both
  // the panel itself and the workspace's right padding so neither overlaps.
  const [chatSidebarWidth, setChatSidebarWidth] = useState(readStoredChatSidebarWidth);

  // Full-screen account overlay. Lifted to AppRoot so the overlay covers the
  // whole viewport (including the chat sidebar) without being trapped inside
  // the workspace's positioning/overflow context.
  const [accountScreenOpen, setAccountScreenOpen] = useState(false);

  // Chat-vs-workspace layout coordination. AppRoot is the one place the chat
  // panel and the workspace meet, so it owns the math that keeps the episode
  // stage visible while the chat resizes (see workbench/chatLayout.js).
  // EpisodeWorkspace keeps owning its panel state and only *publishes* the
  // widths they occupy here; AppRoot commands a panel close via a nonce.
  const [viewportWidth, setViewportWidth] = useState(() =>
    typeof window !== "undefined" && window.innerWidth > 0 ? window.innerWidth : 1600,
  );
  const [modelsSidebar, setModelsSidebar] = useState({ open: false, width: 0 });
  const [toolsSheet, setToolsSheet] = useState({ open: false, width: 0 });
  const [closeLeftSidebarSignal, setCloseLeftSidebarSignal] = useState(0);

  const handleModelsSidebarChange = useCallback((open, width) => {
    setModelsSidebar((prev) =>
      prev.open === open && prev.width === width ? prev : { open, width },
    );
  }, []);
  const handleToolsSheetChange = useCallback((open, width) => {
    setToolsSheet((prev) =>
      prev.open === open && prev.width === width ? prev : { open, width },
    );
  }, []);
  const requestCloseLeftSidebar = useCallback(() => {
    setCloseLeftSidebarSignal((nonce) => nonce + 1);
  }, []);

  const chatLayout = useMemo(
    () => ({
      viewportWidth,
      leftSidebarOpen: modelsSidebar.open,
      leftSidebarWidth: modelsSidebar.width,
      toolsSheetOpen: toolsSheet.open,
      toolsSheetWidth: toolsSheet.width,
    }),
    [viewportWidth, modelsSidebar, toolsSheet],
  );

  // Latest chat width, read by the clamp effect below without making the width
  // a dependency — otherwise every drag frame would re-run the clamp and fight
  // the auto-close (which intentionally lets the chat exceed the sidebar-open
  // cap until the close round-trips back into `chatLayout`).
  const chatSidebarWidthRef = useRef(chatSidebarWidth);
  chatSidebarWidthRef.current = chatSidebarWidth;

  // Track the viewport width so the chat clamp follows window resizes.
  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onResize = () => setViewportWidth(window.innerWidth > 0 ? window.innerWidth : 1600);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Re-clamp the chat whenever the available space shrinks. Keyed on
  // `chatLayout` only (not the width) so it covers the "squeeze chat to keep
  // the stage visible" behavior without interfering with an in-progress drag.
  // Shrinks down to CHAT_MIN — below which the stage absorbs the overflow as a
  // last resort. Growing space never widens the chat on its own.
  useEffect(() => {
    const max = maxChatWidth(chatLayout);
    if (chatSidebarWidthRef.current > max) {
      const next = Math.max(CHAT_MIN_WIDTH, max);
      setChatSidebarWidth(next);
      persistChatSidebarWidth(next);
    }
  }, [chatLayout]);

  const projects = useProjectsStore((state) => state.projects);
  const currentProjectId = useProjectsStore((state) => state.currentProjectId);
  const projectsStatus = useProjectsStore((state) => state.status);
  const refreshProjects = useProjectsStore((state) => state.refresh);
  const openProject = useProjectsStore((state) => state.open);

  // Single-project focus: load the project list once onboarding is done.
  useEffect(() => {
    if (!onboarded) return;
    refreshProjects().catch((err) => console.warn("Failed to load projects", err));
  }, [onboarded, refreshProjects]);

  // Land the user directly in their most recent project (the store keeps
  // `projects` newest-first). When there are none, we deliberately do NOT
  // create one — the first chat message lazily creates a project named from
  // that message (see ChatInput), so we never leave an empty "Untitled
  // project" behind.
  useEffect(() => {
    if (!onboarded || currentProjectId || projectsStatus !== "ready") return;
    const latest = projects[0];
    if (!latest) return;
    openProject(latest.id)
      .then(() => setChatProject(latest.id))
      .catch((err) => console.warn("Failed to open project", err));
  }, [onboarded, currentProjectId, projectsStatus, projects, openProject]);

  // The catalog (episode rail) is scoped to the open project on the backend,
  // so re-read it whenever the active project changes. Project
  // open/create/switch all set the backend's active project before
  // `currentProjectId` updates, so by the time this runs the scan is scoped.
  useEffect(() => {
    if (!currentProjectId) return;
    useCatalogStore
      .getState()
      .refresh({ markRefreshing: true })
      .catch((err) => console.warn("Failed to refresh catalog after project change", err));
  }, [currentProjectId]);

  // Episode entries (episodes/epNNN.mp4, sorted) and the series bible artifact
  // — the two catalog views the workspace renders.
  const episodeEntries = useMemo(() => selectEpisodeEntries(catalog), [catalog]);
  const seriesEntry = useMemo(() => selectSeriesEntry(catalog), [catalog]);

  // The in-window menu bar duplicates the native macOS menu and only earns its
  // place on Windows, which has no native global menu bar; macOS and Linux
  // hide it. When hidden, the workspace + chat reclaim the full height.
  const showWindowMenuBar = isWindowsPlatform();

  // On a phone-width viewport the create/chat panel becomes full-screen (the
  // primary task) instead of a fixed sidebar squeezed next to the player — the
  // personas we build for are on phones. Desktop is untouched.
  const isMobile = viewportWidth > 0 && viewportWidth < 640;
  const effectiveChatWidth = isMobile ? viewportWidth : chatSidebarWidth;

  let content = null;
  if (needsOnboarding === null) {
    // Still probing onboarding state — render nothing but the update toast.
    content = null;
  } else if (needsOnboarding) {
    content = <WelcomeScreen onComplete={completeOnboarding} />;
  } else {
    content = (
      // Column: when shown, the in-window menu bar (h-7) sits above everything
      // and the row below fills the remaining height; the workspace and chat
      // sidebar anchor to that remaining space (see their top-7 offsets) so the
      // menu row isn't overlapped. When hidden, the row fills the whole viewport.
      <div className="flex h-screen w-screen flex-col overflow-hidden">
        {showWindowMenuBar && <WindowMenuBar />}
        <div className="relative flex min-h-0 w-full flex-1 overflow-hidden">
          <div
            className="flex-1 overflow-hidden"
            style={{ paddingRight: isMobile ? 0 : chatSidebarWidth }}
          >
            <EpisodeWorkspace
              manifestRevision={revision}
              episodeEntries={episodeEntries}
              seriesEntry={seriesEntry}
              artifactActivity={artifactActivity}
              catalogHydrated={catalogHydrated}
              catalogRefreshing={catalogRefreshing}
              catalogError={catalogError}
              onModelsSidebarChange={handleModelsSidebarChange}
              onToolsSheetChange={handleToolsSheetChange}
              closeLeftSidebarSignal={closeLeftSidebarSignal}
              onOpenAccountScreen={() => setAccountScreenOpen(true)}
            />
          </div>
          <ChatSidebar
            width={effectiveChatWidth}
            onWidthChange={setChatSidebarWidth}
            layout={chatLayout}
            onRequestCloseLeftSidebar={requestCloseLeftSidebar}
            menuBarVisible={showWindowMenuBar}
          />
        </div>
      </div>
    );
  }

  return (
    <>
      {content}
      {accountScreenOpen ? (
        // z-40 sits above all persistent workbench chrome (max z-30) but below
        // the modal/toast layer (z-50), so the account screen's own Settings
        // dialog and copy toast render *over* it instead of behind.
        <div className="pointer-events-auto fixed inset-0 z-40 bg-background/80 backdrop-blur-sm">
          <AccountScreen
            open={accountScreenOpen}
            onOpenChange={setAccountScreenOpen}
          />
        </div>
      ) : null}
    </>
  );
}

bootstrap();
