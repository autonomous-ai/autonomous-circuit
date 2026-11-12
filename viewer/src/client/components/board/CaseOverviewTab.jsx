import { useMemo } from 'react';
import { cn } from '@/ui/utils';
import { useDarkMode } from '@/ui/use-dark-mode.js';
import CaseScene from './CaseScene.jsx';
import { defaultVisibility, drawingMessage, orderParts, resolveCase } from './caseView.js';

/** The agent's overview cannot see the app theme from its sandbox; the hash tells it. */
export function themedOverviewUrl(url, dark) {
  if (!url) return '';
  return `${url.split('#')[0]}#theme=${dark ? 'dark' : 'light'}`;
}

/** The case team's unchecked parts, drawn as they are, under a draft badge. */
function DraftModel({enclosure, badge, Scene}) {
  const parts = useMemo(() => orderParts(enclosure.parts), [enclosure.parts]);
  const visible = useMemo(() => defaultVisibility(parts), [parts]);
  return <div data-slot="case-overview-draft" className="relative flex min-h-0 flex-1">
    <Scene contractUrl={enclosure.contractUrl || ''} parts={parts} visible={visible}
      exploded={false} seeThrough={false} showBoard={enclosure.nativePCBCheckerApplies !== false} className="min-h-0 flex-1" />
    <p data-slot="case-draft-badge" role="status"
      className="pointer-events-none absolute left-2 top-2 rounded-full border border-amber-500/60 bg-amber-50/90 px-2.5 py-0.5 text-[11px] font-medium text-amber-800 backdrop-blur dark:bg-amber-500/15 dark:text-amber-200">
      {badge}
    </p>
  </div>;
}

export default function CaseOverviewTab({artifact, teamPreview = null, teamRuns = null, onOpenTab, Scene = CaseScene, className}) {
  const url = themedOverviewUrl(artifact?.case?.overviewUrl || '', useDarkMode());
  const drawings = Array.isArray(artifact?.case?.drawings) ? artifact.case.drawings : [];
  const {enclosure, draft, drawing, run} = resolveCase(artifact?.case || null, teamPreview, teamRuns);
  const hasAccepted = Boolean(artifact?.case?.parts?.length);
  // The accepted case's overview wins; then the draft model; then what is happening.
  const body = url ? <iframe title="Case motion overview" src={url} sandbox="allow-scripts" referrerPolicy="no-referrer" className="min-h-0 flex-1 border-0 bg-background" />
    : draft ? <DraftModel enclosure={enclosure} badge={draft.badge} Scene={Scene} />
      : <div data-slot="case-overview-empty" className="grid flex-1 place-items-center px-5 text-sm text-muted-foreground">
        {drawing && !hasAccepted ? drawingMessage(run) : 'No case overview yet. Open 3D & Print to inspect the model and download its parts.'}
      </div>;
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
    {body}
  </section>;
}
