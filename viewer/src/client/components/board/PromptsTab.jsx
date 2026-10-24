import { useEffect, useState } from 'react';
import { Copy, RefreshCw } from 'lucide-react';
import { cn } from '@/ui/utils';
import { transport } from '@/lib/transport.ts';

export default function PromptsTab({projectId, original = false, fallbackText = '', fallbackCurrentText = '', className}) {
  const [state,setState] = useState({projectId:'',loading:true,data:null,error:''});
  const [revision,setRevision] = useState(0);
  const [copyState,setCopyState] = useState('');
  useEffect(() => {
    let cancelled=false;
    setState({projectId,loading:true,data:null,error:''}); setCopyState('');
    if (!projectId) {setState({projectId,loading:false,data:null,error:''}); return undefined;}
    transport.workspace_prompts(projectId).then(data => {if(!cancelled) setState({projectId,loading:false,data,error:''});})
      .catch(error => {if(!cancelled) setState({projectId,loading:false,data:null,error:error.message || String(error)});});
    return () => {cancelled=true;};
  },[projectId,revision]);
  const active = state.projectId === projectId;
  const prompt = original ? (active && state.data?.original) : (active && state.data?.current);
  const text = prompt?.text || (original ? fallbackText : fallbackCurrentText || fallbackText);
  const copy = async () => {
    try {await navigator.clipboard.writeText(text);setCopyState('Copied');}
    catch {setCopyState('Select the text below to copy it.');}
  };
  return <section data-slot="prompts-tab" className={cn('scrollbar-thin min-h-0 flex-1 overflow-y-auto p-4',className)}>
    <div className="mx-auto flex max-w-3xl flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">{original ? 'Original prompt' : 'Current prompt'}</h2>
        <button type="button" onClick={() => setRevision(value => value+1)} aria-label="Refresh prompts" className="ml-auto rounded p-1 text-muted-foreground"><RefreshCw className="size-4" /></button>
        <button type="button" onClick={copy} disabled={!text || !active || state.loading} className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-xs"><Copy className="size-3" /> Copy</button>
      </div>
      <p className="text-xs leading-5 text-muted-foreground">{original ? 'The original request saved for this project.' : prompt?.source === 'file' ? `Current brief from ${prompt.file}.` : 'Current request for this project.'}</p>
      {!active || state.loading ? <p className="text-sm text-muted-foreground">Loading prompts…</p> : state.error ? <p role="alert" className="text-sm text-red-300">{state.error}</p> : text ? <pre data-slot="prompt-text" className="whitespace-pre-wrap break-words rounded-xl border border-border/60 bg-card/30 p-4 font-sans text-sm leading-6">{text}</pre> : <p className="text-sm text-muted-foreground">No prompt found for this project.</p>}
      {copyState ? <p role="status" className="text-xs text-muted-foreground">{copyState}</p> : null}
    </div>
  </section>;
}
