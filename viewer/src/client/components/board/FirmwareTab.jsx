import { useEffect, useState } from "react";
import { Cpu, ExternalLink, FileCode2, Loader2 } from "lucide-react";
import { cn } from "@/ui/utils";
import Markdown from "@/components/chat/Markdown.jsx";
import ChatCodeBlock from "@/components/chat/ChatCodeBlock";
import FirmwareFlashButton from "./FirmwareFlashButton.jsx";

/**
 * The Firmware tab: the code the agent writes for the board once the board
 * itself checks out, read straight from `firmware/` in the project.
 *
 * Two states, on purpose. Before there is a `firmware/` tree the tab says
 * when one appears and why not sooner (the pin map comes off finished copper,
 * not a draft). Once there is one, the README is rendered on top — that is
 * where the agent says how to build and flash — and every source file is a
 * click away, highlighted, with the raw file one more click away for anyone
 * who wants to open it in their own editor.
 *
 * Nothing here builds or flashes anything: the tab shows what was written.
 *
 * @param {{
 *   artifact?: object|null,   // catalog entry.artifact; reads .firmware
 *   sidecar?: object|null,    // parsed .board.json; reads fab.ready for the empty state
 *   boardName?: string,
 *   projectId?: string,      // for the Flash button's commands
 *   onOpenTab?: (id: string) => void,
 *   className?: string,
 * }} props
 */

const LANGUAGE_BY_EXTENSION = {
  c: "c",
  h: "c",
  cpp: "cpp",
  hpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  ino: "cpp",
  py: "python",
  rs: "rust",
  js: "javascript",
  ts: "typescript",
  lua: "lua",
  s: "armasm",
  asm: "armasm",
  ld: "text",
  md: "markdown",
  txt: "text",
  ini: "ini",
  toml: "ini",
  yaml: "yaml",
  yml: "yaml",
  json: "json",
  cfg: "ini",
  cmake: "cmake",
  mk: "makefile",
  sh: "bash",
  csv: "text",
};

/** The highlight language for a firmware path — by basename first, then extension. */
export function languageFor(file) {
  const base = String(file || "").split("/").pop() || "";
  if (/^(GNU)?makefile$/i.test(base)) return "makefile";
  if (/^CMakeLists\.txt$/i.test(base)) return "cmake";
  if (/^Dockerfile$/i.test(base)) return "dockerfile";
  const ext = base.includes(".") ? base.split(".").pop().toLowerCase() : "";
  return LANGUAGE_BY_EXTENSION[ext] || "text";
}

function isReadme(file) {
  return /^readme\.md$/i.test(String(file || ""));
}

/**
 * The file to open first: the entry point if there is one (`main.*`, at any
 * depth), else the first source that is not the README. The README is already
 * rendered above the code, so opening it again as text would be the one
 * choice that shows the user nothing new.
 */
export function pickFirstFile(files) {
  const list = Array.isArray(files) ? files : [];
  const entry = list.find((f) => /(^|\/)main\.[a-z0-9]+$/i.test(f.file) && !isReadme(f.file));
  if (entry) return entry;
  return list.find((f) => !isReadme(f.file)) || list[0] || null;
}

/** Fetch a text asset; `state` is idle | loading | ready | failed. */
function useText(url) {
  const [result, setResult] = useState({ url: "", state: "idle", text: "" });
  useEffect(() => {
    if (!url) {
      setResult({ url: "", state: "idle", text: "" });
      return undefined;
    }
    let cancelled = false;
    setResult({ url, state: "loading", text: "" });
    fetch(url)
      .then((response) => (response.ok ? response.text() : Promise.reject(new Error(String(response.status)))))
      .then((text) => {
        if (!cancelled) setResult({ url, state: "ready", text });
      })
      .catch(() => {
        if (!cancelled) setResult({ url, state: "failed", text: "" });
      });
    return () => {
      cancelled = true;
    };
  }, [url]);
  return result;
}

function formatBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} kB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function EmptyState({ ready, onOpenTab, className }) {
  return (
    <div data-slot="firmware-empty" className={cn("grid min-h-0 flex-1 place-items-center", className)}>
      <div className="flex max-w-sm flex-col items-center gap-3 px-6 text-center">
        <Cpu className="size-6 text-muted-foreground" aria-hidden />
        <p className="text-sm font-medium text-foreground">No firmware yet</p>
        <p className="text-sm leading-6 text-muted-foreground">
          {ready
            ? "The board checks out. Firmware is the next thing the agent writes — it lands in firmware/ inside this project and shows up here."
            : "The agent writes firmware after the board checks out, so the pin map is read off finished copper rather than a draft. Until then this tab stays empty."}
        </p>
        <button
          type="button"
          onClick={() => onOpenTab?.("function")}
          className="text-xs text-primary underline-offset-4 hover:underline"
        >
          See what is on the board
        </button>
      </div>
    </div>
  );
}

export default function FirmwareTab({ artifact = null, sidecar = null, boardName = "board", projectId = "", onOpenTab, className }) {
  const firmware = artifact?.firmware || null;
  const files = Array.isArray(firmware?.files) ? firmware.files : [];
  const [selectedFile, setSelectedFile] = useState("");
  const active = files.find((f) => f.file === selectedFile) || pickFirstFile(files);
  const readme = useText(firmware?.readmeUrl || "");
  const source = useText(active?.url || "");

  if (!firmware || !files.length) {
    return <EmptyState ready={sidecar?.fab?.ready === true} onOpenTab={onOpenTab} className={className} />;
  }

  return (
    <div data-slot="firmware" className={cn("scrollbar-thin min-h-0 flex-1 overflow-y-auto", className)}>
      <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4">
        <div className="flex items-center gap-2.5 rounded-xl border border-border/60 bg-card/30 px-4 py-3">
          <Cpu className="size-4 shrink-0 text-sky-400" aria-hidden />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-foreground">Firmware for {boardName}</p>
            <p className="text-xs text-muted-foreground">
              {files.length === 1 ? "1 file" : `${files.length} files`} in firmware/
              {firmware.truncated ? " — only the first are listed" : ""}
              {" · "}written by the agent, not yet run on hardware
            </p>
          </div>
          {/* The one way firmware reaches a board: a person, two clicks, the exact USB device named. */}
          <FirmwareFlashButton projectId={projectId} ready={sidecar?.fab?.ready === true} hasRecipe={Boolean(firmware.flashUrl)} />
        </div>

        {firmware.readmeUrl ? (
          <section data-slot="firmware-readme" className="rounded-xl border border-border/60 bg-card/30 p-4">
            {readme.state === "loading" ? (
              <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
            ) : readme.state === "ready" ? (
              <Markdown source={readme.text} className="text-sm" />
            ) : (
              <p className="text-xs text-muted-foreground">The README could not be read.</p>
            )}
          </section>
        ) : null}

        <div className="flex min-h-0 gap-3">
          <nav aria-label="Firmware files" className="flex w-56 shrink-0 flex-col gap-0.5">
            {files.map((f) => (
              <button
                key={f.file}
                type="button"
                data-slot="firmware-file"
                data-file={f.file}
                aria-current={active?.file === f.file ? "true" : undefined}
                onClick={() => setSelectedFile(f.file)}
                title={`${f.file} · ${formatBytes(f.bytes)}`}
                className={cn(
                  "flex items-center gap-1.5 truncate rounded-md px-2 py-1 text-left font-mono text-[11px] transition-colors",
                  active?.file === f.file ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <FileCode2 className="size-3 shrink-0" aria-hidden />
                <span className="truncate">{f.file}</span>
              </button>
            ))}
          </nav>

          <div className="flex min-w-0 flex-1 flex-col gap-1.5">
            {active ? (
              <>
                <div className="flex items-center gap-2">
                  <span className="truncate font-mono text-[11px] text-muted-foreground">firmware/{active.file}</span>
                  <a
                    href={active.url}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-auto inline-flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                    title="Open the raw file"
                  >
                    <ExternalLink className="size-3" aria-hidden />
                    raw
                  </a>
                </div>
                {source.state === "ready" ? (
                  <ChatCodeBlock
                    code={source.text}
                    lang={languageFor(active.file)}
                    label={languageFor(active.file)}
                    className="my-0"
                    maxHeightClassName="max-h-none"
                  />
                ) : source.state === "failed" ? (
                  <p data-slot="firmware-error" className="text-xs text-muted-foreground">
                    This file could not be read. It may have just been rewritten — pick it again.
                  </p>
                ) : (
                  <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden />
                )}
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
