import { cn } from '@/ui/utils';
import { useDarkMode } from '@/ui/use-dark-mode.js';

/** The agent's overview cannot see the app theme from its sandbox; the hash tells it. */
export function themedOverviewUrl(url, dark) {
  if (!url) return '';
  return `${url.split('#')[0]}#theme=${dark ? 'dark' : 'light'}`;
}

export default function CaseOverviewTab({artifact, onOpenTab, className}) {
  const url = themedOverviewUrl(artifact?.case?.overviewUrl || '', useDarkMode());
  const drawings = Array.isArray(artifact?.case?.drawings) ? artifact.case.drawings : [];
  return <section data-slot="case-overview" className={cn('flex min-h-0 flex-1 flex-col',className)}>
    <div className="flex shrink-0 items-center gap-3 border-b border-border/60 px-3 py-2 text-xs">
      <span className="font-medium">Case overview</span>
      {url ? <a href={url} target="_blank" rel="noreferrer" className="text-primary underline">Open overview</a> : null}
      <button type="button" onClick={() => onOpenTab?.('case')} className="ml-auto text-primary underline">3D &amp; Print</button>
    </div>
    {drawings.length ? <nav data-slot="case-drawings" aria-label="Drawings" className="flex shrink-0 items-center gap-3 overflow-x-auto whitespace-nowrap border-b border-border/60 px-3 py-1.5 text-xs">
      <span className="font-medium">Drawings</span>
      {drawings.map(sheet => <a key={sheet.url} href={sheet.url} target="_blank" rel="noreferrer" title={sheet.title || sheet.name}
        className={cn('text-primary underline', sheet.kind === 'assembly' && 'font-medium')}>{sheet.kind === 'assembly' ? 'Assembly' : sheet.name}</a>)}
    </nav> : null}
    {url ? <iframe title="Case motion overview" src={url} sandbox="allow-scripts" referrerPolicy="no-referrer" className="min-h-0 flex-1 border-0 bg-background" /> :
      <div className="grid flex-1 place-items-center px-5 text-sm text-muted-foreground">No case overview yet. Open 3D &amp; Print to inspect the model and download its parts.</div>}
  </section>;
}
