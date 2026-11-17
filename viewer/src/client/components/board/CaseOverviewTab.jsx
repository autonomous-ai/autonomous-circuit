import { useEffect, useMemo, useState } from 'react';
import { cn } from '@/ui/utils';
import { useDarkMode } from '@/ui/use-dark-mode.js';
import CaseScene from './CaseScene.jsx';
import { defaultVisibility, drawingMessage, orderParts, resolveCase } from './caseView.js';

/** The agent's overview cannot see the app theme from its sandbox; the hash tells it. */
export function themedOverviewUrl(url, dark) {
  if (!url) return '';
  return `${url.split('#')[0]}#theme=${dark ? 'dark' : 'light'}`;
}

/**
 * A full-window view inside the app. A new browser tab has no way back when the
 * viewer runs in an embedded pane or a home-screen app, so overview and drawings
 * open here and close with the Back button or Escape.
 */
export function FullView({view, onClose}) {
  useEffect(() => {
    const onKey = e => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return <div data-slot="case-full-view" role="dialog" aria-modal="true" aria-label={view.title}
    className="fixed inset-0 z-50 flex flex-col bg-background">
    <div className="flex shrink-0 items-center gap-3 border-b border-border/60 px-3 py-2 text-xs">
      <button type="button" data-slot="case-full-view-back" onClick={onClose} className="font-medium text-primary underline">← Back</button>
      <span className="truncate font-medium">{view.title}</span>
    </div>
    <iframe title={view.title} src={view.url} sandbox="allow-scripts" referrerPolicy="no-referrer" className="min-h-0 flex-1 border-0 bg-background" />
  </div>;
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
  const [full, setFull] = useState(null);
  // The accepted case's overview wins; then the draft model; then what is happening.
  const body = url ? <iframe title="Case motion overview" src={url} sandbox="allow-scripts" referrerPolicy="no-referrer" className="min-h-0 flex-1 border-0 bg-background" />
    : draft ? <DraftModel enclosure={enclosure} badge={draft.badge} Scene={Scene} />
      : <div data-slot="case-overview-empty" className="grid flex-1 place-items-center px-5 text-sm text-muted-foreground">
        {drawing && !hasAccepted ? drawingMessage(run) : 'No case overview yet. Open 3D & Print to inspect the model and download its parts.'}
      </div>;
  return <section data-slot="case-overview" className={cn('flex min-h-0 flex-1 flex-col',className)}>
    <div className="flex shrink-0 items-center gap-3 border-b border-border/60 px-3 py-2 text-xs">
      <span className="font-medium">Case overview</span>
      {url ? <button type="button" data-slot="case-open-overview" onClick={() => setFull({url, title: 'Case overview'})} className="text-primary underline">Open overview</button> : null}
      <button type="button" onClick={() => onOpenTab?.('case')} className="ml-auto text-primary underline">3D &amp; Print</button>
    </div>
    {drawings.length ? <nav data-slot="case-drawings" aria-label="Drawings" className="flex shrink-0 items-center gap-3 overflow-x-auto whitespace-nowrap border-b border-border/60 px-3 py-1.5 text-xs">
      <span className="font-medium">Drawings</span>
      {drawings.map(sheet => <a key={sheet.url} href={sheet.url} title={sheet.title || sheet.name}
        onClick={e => { e.preventDefault(); setFull({url: sheet.url, title: sheet.title || sheet.name}); }}
        className={cn('text-primary underline', sheet.kind === 'assembly' && 'font-medium')}>{sheet.kind === 'assembly' ? 'Assembly' : sheet.name}</a>)}
    </nav> : null}
    {body}
    {full ? <FullView view={full} onClose={() => setFull(null)} /> : null}
  </section>;
}
