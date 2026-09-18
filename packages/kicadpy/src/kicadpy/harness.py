"""The Harness verdict for a KiCad-native workspace — `.harness/verdict.json` (DSH spec 1).

A host such as Harness (the KiCad tile, `harness/kicad/`) reads one small file to fill its pane header:
`ready` or not, one summary line, the findings, and a phase strip saying where the work is. The
`.board.json` sidecars that `kicadpy.publish` writes under `boards/` stay the machine contract for
the app; this is the same fact folded into the shape every domain harness shares, so the pane can
say "Prototype-ready" or "3 errors, 2 warnings" without knowing what a gerber is.

`ready` is the sidecar's `fab.ready` and nothing weaker — the publisher sets that from the
prototype packet (zero error findings across native ERC/DRC/parity, the factory floor DRC, the
independent gerber read, part identities, assembly and the engineering areas). The definition of
done does not change because the reader did.

The verdict is a feed, not a gate: the publisher writes it at every sidecar write (running →
complete | failed), and the agent may mark a phase active before the first publish
(`python3 -m kicadpy.harness --active build`). Mirrors `circuitpy.generation.harness_verdict`
for the v1 (tscircuit) path; the two must keep the same shape.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

#: Where the host reads the verdict, relative to the workspace root.
VERDICT_PATH = Path('.harness') / 'verdict.json'
#: Sidecars written by `kicadpy.publish`, relative to the workspace root.
BOARDS_DIR = 'boards'
NATIVE_ENGINE = 'kicad-native'
SEVERITIES = ('error', 'warning', 'info')
PHASES = ('build', 'checks', 'fab')
PHASE_NAMES = {'build': 'Build', 'checks': 'Checks', 'fab': 'Fab'}
#: The pane header is one line; the spec caps it.
SUMMARY_MAX = 200


def native_sidecars(workspace: Path) -> list[tuple[str, dict]]:
    """Every `boards/*.board.json` whose source engine is KiCad-native, as (relative path, payload).

    Sorted by name so a workspace with two boards gives a stable verdict. A sidecar that does not
    parse is skipped — the publisher writes them atomically, so a half file is another writer's.
    """
    boards = workspace / BOARDS_DIR
    if not boards.is_dir():
        return []
    out = []
    for path in sorted(boards.glob('*.board.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and (payload.get('source') or {}).get('engine') == NATIVE_ENGINE:
            out.append((path.relative_to(workspace).as_posix(), payload))
    return out


def _findings(sidecar: dict) -> list[dict[str, str]]:
    validation = sidecar.get('validation')
    warnings = validation.get('warnings') if isinstance(validation, dict) else None
    findings = []
    for w in warnings or []:
        if not isinstance(w, dict):
            continue
        severity = str(w.get('severity') or 'info')
        if severity not in SEVERITIES:
            severity = 'info'
        native = w.get('native') if isinstance(w.get('native'), dict) else {}
        ref = w.get('ref') or w.get('part') or native.get('reference') or native.get('ref') or ''
        findings.append({
            'severity': severity,
            'kind': str(w.get('kind') or ''),
            'message': str(w.get('detail') or w.get('message') or ''),
            'ref': str(ref),
        })
    return findings


def _phase(id_: str, state: str) -> dict[str, str]:
    return {'id': id_, 'name': PHASE_NAMES[id_], 'state': state}


def verdict(sidecars: list[tuple[str, dict]], *, active: str | None = None) -> dict[str, object]:
    """The spec-1 verdict for a workspace, folded from its native sidecars.

    No sidecar yet: nothing is ready and every phase is pending (or the one the agent marked
    active). Otherwise `ready` is every board's `fab.ready`; findings concatenate; the phase strip
    reads the worst board: a publication still `running` keeps Checks active, a `failed` one marks
    Checks failed, a `complete` one marks Checks done when no error finding remains and Fab done
    when the packet is ready.
    """
    if active is not None and active not in PHASES:
        raise ValueError(f'unknown phase {active!r}; one of {", ".join(PHASES)}')
    now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    if not sidecars:
        phases = [_phase(p, 'active' if p == active else 'pending') for p in PHASES]
        summary = {'build': 'Designing the board', 'checks': 'Running native checks',
                   'fab': 'Preparing the prototype packet'}.get(active or '', 'No board yet')
        return {'spec': 1, 'ready': False, 'summary': summary, 'findings': [], 'phases': phases, 'updatedAt': now}

    findings: list[dict[str, str]] = []
    publications: list[str] = []
    ready = True
    for _path, sidecar in sidecars:
        findings.extend(_findings(sidecar))
        fab = sidecar.get('fab') if isinstance(sidecar.get('fab'), dict) else {}
        ready = ready and bool(fab.get('ready'))
        native = sidecar.get('native') if isinstance(sidecar.get('native'), dict) else {}
        publications.append(str(native.get('publication') or 'complete'))
    errors = sum(1 for f in findings if f['severity'] == 'error')
    warns = sum(1 for f in findings if f['severity'] == 'warning')
    running = 'running' in publications
    failed = 'failed' in publications

    # The sidecar exists, so a design was authored and published at least once: Build is done.
    if running:
        checks, fab_state = 'active', 'pending'
    elif failed or errors:
        checks, fab_state = 'failed', 'pending'
    else:
        checks, fab_state = 'done', 'done' if ready else 'active'
    phases = [_phase('build', 'done'), _phase('checks', checks), _phase('fab', fab_state)]
    if active == 'build':
        # The agent is editing sources again: the board on disk is the previous revision.
        phases[0]['state'] = 'active'

    if ready:
        summary = 'Prototype-ready — physical hardware untested'
    elif running:
        summary = 'Native checks running'
    elif failed:
        first = next((f['message'] for f in findings if f['kind'] == 'native_check_failed'), '')
        summary = f'Native checks failed: {first}' if first else 'Native checks failed'
    else:
        parts = []
        if errors:
            parts.append(f"{errors} error{'' if errors == 1 else 's'}")
        if warns:
            parts.append(f"{warns} warning{'' if warns == 1 else 's'}")
        summary = ', '.join(parts) if parts else 'Not prototype-ready'
    if len(summary) > SUMMARY_MAX:
        summary = summary[:SUMMARY_MAX - 1] + '…'
    return {
        'spec': 1,
        'ready': ready,
        'summary': summary,
        'findings': findings,
        'artifact': sidecars[0][0],
        'phases': phases,
        'updatedAt': now,
    }


def write_verdict(workspace: Path | str, *, active: str | None = None) -> Path | None:
    """Write `<workspace>/.harness/verdict.json` from the sidecars on disk.

    Atomic (temp file + rename) because the host tails the path and must never read half a file.
    Never raises on I/O: the sidecar is the artifact of record and is already on disk; a host that
    cannot be told is a host that reads the sidecar itself. A failure goes to stderr — stdout is
    the publisher's one-JSON-line channel and must stay clean. A bad `active` value is the caller's
    bug and does raise.
    """
    workspace = Path(workspace)
    payload = verdict(native_sidecars(workspace), active=active)
    try:
        target = workspace / VERDICT_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + '.tmp')
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        os.replace(tmp, target)
        return target
    except OSError as exc:
        print(f'[kicadpy] harness verdict not written: {exc}', file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Write the Harness verdict for a KiCad-native workspace.')
    parser.add_argument('workspace', nargs='?', default='.', help='the workspace root (default: cwd)')
    parser.add_argument('--active', choices=PHASES, help='mark this phase active (e.g. build, before the first publish)')
    args = parser.parse_args(argv)
    target = write_verdict(Path(args.workspace).resolve(), active=args.active)
    if target is None:
        print(json.dumps({'ok': False, 'error': 'verdict not written'}))
        return 1
    payload = json.loads(target.read_text(encoding='utf-8'))
    print(json.dumps({'ok': True, 'verdict': str(target), 'ready': payload['ready'], 'summary': payload['summary']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
