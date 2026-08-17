import { Plus } from "lucide-react";
import { cn } from "@/ui/utils";
import {
  createConversation,
  selectConversation,
  useChatStore,
} from "@/store/chat";

export default function ChatTabs() {
  const projectId = useChatStore((state) => state.currentProjectId);
  const sessionId = useChatStore((state) => state.currentSessionId);
  const summaries = useChatStore((state) => state.conversationSummaries);
  const turnInProgress = useChatStore((state) => state.turnInProgress);

  if (!projectId) return null;

  const tabs = summaries.length
    ? summaries
    : [{ sessionId: "", title: "New chat", updatedAt: 0, messageCount: 0 }];

  return (
    <div
      data-slot="project-chat-tabs"
      className="flex h-9 min-w-0 items-end gap-1 border-t border-border/40 px-2"
    >
      <div className="scrollbar-none flex min-w-0 flex-1 gap-1 overflow-x-auto">
        {tabs.map((tab) => {
          const active = tab.sessionId === sessionId || (!tab.sessionId && !sessionId);
          return (
            <button
              key={tab.sessionId || "new"}
              type="button"
              disabled={turnInProgress || !tab.sessionId}
              onClick={() => void selectConversation(tab.sessionId)}
              title={tab.title}
              aria-current={active ? "page" : undefined}
              className={cn(
                "mb-[-1px] max-w-40 shrink-0 truncate rounded-t-md border border-b-0 px-2.5 py-1.5 text-[11px] transition-colors",
                active
                  ? "border-border bg-background font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              {tab.title || "New chat"}
            </button>
          );
        })}
      </div>
      <button
        type="button"
        aria-label="New chat in this project"
        title="New chat in this project"
        disabled={turnInProgress}
        onClick={() => void createConversation()}
        className="mb-1 inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
      >
        <Plus className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}
