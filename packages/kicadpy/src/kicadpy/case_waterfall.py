"""The case team's light build waterfall: one stage at a time, bounded repairs, one notes list.

Order (fixed): simple parts (frame/box and small parts) -> moving parts -> joints/snap fits -> lid.
The builder declares the stages that apply, with their parts, in case/checks.json:

    {"stages": {"simple": {"parts": ["frame", "battery_tray"]},
                "moving": {"parts": ["leg_FL", "leg_FR"]},
                "joints": {"parts": ["hip_pin"]}},
     "checks": [{"name": "print", "stage": "simple", ...},
                {"name": "legs-print", "stage": "moving", "kind": "print", ...},
                {"name": "legs-sweep", "stage": "moving", "kind": "motion", ...}]}

After each builder turn the harness runs the checks (kicadpy.case_checks), then this ledger
decides, without a model:

- the current stage is the first one in order that is still open;
- each budget key of the stage (a check kind: print, motion, check, plus the tester's review)
  gets at most MAX_REPAIRS repair turns after the first build; then it becomes a NOTE and the
  stage moves on. A stage over its time cap notes what still fails and moves on too;
- a closed stage freezes its parts: a later turn that changes them is undone from the stash,
  never reworked. Only the end step (the lead advisor, once) may unfreeze named parts;
- every NOTE lands in one list, case/NOTES.md, the one the person reads.

The ledger is pure data; parallel.py stores it under .pilot (out of the builder's reach).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import time

from . import case_measure
from .case_checks import CHECKS_FILE, STAGES

MAX_REPAIRS = 2
STAGE_SECONDS = {'simple': 90 * 60, 'moving': 90 * 60, 'joints': 60 * 60, 'lid': 45 * 60}
MAX_TURNS = 4 * (1 + MAX_REPAIRS) + 2   # every stage built and repaired twice, the end repair, slack
NOTES_FILE = 'case/NOTES.md'
STATUS_FILE = 'case/stages.json'   # per-stage and per-part status, for the viewer's parts list
HARNESS_FILES = (NOTES_FILE, STATUS_FILE)
# A part's status chip: waiting -> working (being drawn) -> testing -> valid, or note (moved on
# after two tries); done once the whole case passed.
STATUSES = ('waiting', 'working', 'testing', 'valid', 'note', 'done')
GROUPS = ('frame', 'glue-on', 'moving', 'joints', 'lid')
STAGE_TITLES = {'simple': 'Simple parts (frame/box and small parts)', 'moving': 'Moving parts',
                'joints': 'Joints and snap fits', 'lid': 'Lid'}
FAILING = (case_measure.FAIL, case_measure.UNVERIFIED)
MAX_NOTES = 200
BUILD_RULE = ('Build with the cached builder (kicadpy.case_build in case/build.py): it rebuilds only the '
              'parts whose code or inputs changed; use --only <part> for a scoped fix. Never rm -rf case/out '
              'and never --force unless the person asked for a full rebuild. A closed part is kept even when '
              'its code changed; rebuild it with --only <part> only when the lead allowed it to change.')


def plan(worktree):
    """(stages {name: [part names]} in build order, problems); None when no stages are declared."""
    try:
        data = json.loads((Path(worktree) / CHECKS_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None, []
    stages = data.get('stages') if isinstance(data, dict) else None
    if stages is None:
        return None, []
    if not isinstance(stages, dict) or not stages:
        return None, [f'{CHECKS_FILE} "stages" must be an object of stage name to {{"parts": [...]}}']
    result, problems = {}, []
    for name in STAGES:
        if name not in stages:
            continue
        parts = stages[name].get('parts') if isinstance(stages[name], dict) else None
        if not isinstance(parts, list) or not all(isinstance(part, str) and part for part in parts):
            problems.append(f'{CHECKS_FILE} stage {name} needs a "parts" list of part names')
            parts = []
        result[name] = parts[:200]
    problems += [f'{CHECKS_FILE} has unknown stage {name!r}; use {", ".join(STAGES)}'
                 for name in stages if name not in STAGES]
    return result, problems


def glue_on(worktree):
    """Simple-stage parts declared as glue-on holders/trays ("glueOn" in the simple stage)."""
    try:
        data = json.loads((Path(worktree) / CHECKS_FILE).read_text(encoding='utf-8'))
        names = data['stages']['simple'].get('glueOn', [])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []
    return [name for name in names if isinstance(name, str)][:100] if isinstance(names, list) else []


def start(stages, now=None):
    return {'schema': 1, 'plan': list(stages), 'parts': dict(stages),
            'stages': {name: {'status': 'open', 'startedAt': None, 'fails': {}, 'noted': []}
                       for name in stages},
            'notes': [], 'frozen': {}, 'checkLevels': {}, 'end': None, 'endTurn': False, 'turns': 0,
            'createdAt': now if now is not None else time.time()}


def update_plan(ledger, stages):
    """A builder may add or re-scope stages it has not finished; a closed stage stays as it was."""
    for name in STAGES:
        if name not in stages:
            continue
        entry = ledger['stages'].setdefault(name, {'status': 'open', 'startedAt': None,
                                                   'fails': {}, 'noted': []})
        if entry['status'] == 'open':
            ledger['parts'][name] = stages[name]
    ledger['plan'] = [name for name in STAGES if name in ledger['stages']]
    return ledger


def current(ledger):
    return next((name for name in ledger['plan'] if ledger['stages'][name]['status'] == 'open'), None)


def done(ledger):
    return current(ledger) is None


def note(ledger, stage, key, message, now=None):
    if len(ledger['notes']) < MAX_NOTES:
        ledger['notes'].append({'stage': stage, 'key': key, 'message': str(message)[:500],
                                'at': now if now is not None else time.time()})


def stage_keys(summary, stage):
    """{budget key: (level, message)} for the stage's checks; the native check is the end's."""
    grouped = {}
    for entry in summary.get('checks', []):
        if entry.get('stage') == stage:
            grouped.setdefault(entry.get('kind') or 'check', []).append(entry)
    keys = {}
    for key, entries in grouped.items():
        level = case_measure.verdict(entries)
        bad = [f"{entry['name']}: {entry['message']}" for entry in entries if entry['level'] in FAILING]
        keys[key] = (level, '; '.join(bad) or f"{entries[0]['name']}: {entries[0]['message']}")
    if not keys:
        keys['check'] = (case_measure.UNVERIFIED,
                         f'no check in {CHECKS_FILE} covers the {stage} stage; declare one')
    return keys


def machine_open(ledger, stage, keys):
    """The keys that still fail and still have repair turns left: while any, the tester waits."""
    noted = set(ledger['stages'][stage]['noted'])
    return sorted(key for key, (level, _) in keys.items() if level in FAILING and key not in noted)


def advance(ledger, stage, keys, review=None, now=None, elapsed=None):
    """Record one evaluation of the current stage; return its status (open, passed or noted).

    `review` is the tester's verdict for the stage (decision, findings) or None when it did
    not run because a machine check still fails.
    """
    now = now if now is not None else time.time()
    entry = ledger['stages'][stage]
    if entry['startedAt'] is None:
        entry['startedAt'] = now
    results = {key: value for key, value in keys.items()}
    if review is not None:
        findings = review.get('findings') or []
        failing = review.get('decision') == 'revise' and findings
        results['review'] = ((case_measure.FAIL, '; '.join(str(item.get('message', item))[:200]
                                                           for item in findings[:5]))
                             if failing else (case_measure.PASS, 'tester approved'))
    for key, (level, message) in results.items():
        if level not in FAILING or key in entry['noted']:
            continue
        entry['fails'][key] = entry['fails'].get(key, 0) + 1
        if entry['fails'][key] > MAX_REPAIRS:
            entry['noted'].append(key)
            note(ledger, stage, key, f'still failing after {MAX_REPAIRS} repair tries: {message}', now)
    seconds = elapsed if elapsed is not None else now - entry['startedAt']
    still = [key for key, (level, _) in results.items() if level in FAILING and key not in entry['noted']]
    if still and seconds > STAGE_SECONDS.get(stage, 3600):
        for key in still:
            entry['noted'].append(key)
            note(ledger, stage, key, f'over the {STAGE_SECONDS.get(stage, 3600) // 60} min stage cap: '
                 f'{results[key][1]}', now)
        still = []
    reviewed = 'review' in results or 'review' in entry['noted']
    if not still and reviewed:
        entry['status'] = 'noted' if entry['noted'] else 'passed'
        entry['closedAt'] = now
    return entry['status']


def close_all(ledger, reason, now=None):
    """The team's wall clock ran out: note every open stage and move to the end."""
    for name in ledger['plan']:
        entry = ledger['stages'][name]
        if entry['status'] == 'open':
            entry['status'] = 'noted'
            entry['noted'].append('time')
            note(ledger, name, 'time', reason, now)


def part_files(worktree, names):
    """case/out paths (STL and STEP) of the named parts, from the exported parts.json."""
    return sorted({path for _, path in part_paths(worktree, names)})


def part_paths(worktree, names):
    """[(part name, case/out path)] for the named parts' STL and STEP files."""
    try:
        manifest = json.loads((Path(worktree) / 'case/out/parts.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    wanted, files = set(names), []
    for part in manifest.get('parts', []) if isinstance(manifest, dict) else []:
        if not isinstance(part, dict) or part.get('name') not in wanted:
            continue
        for key in ('stl', 'step'):
            member = part.get(key)
            if not isinstance(member, str) or not member:
                continue
            path = PurePosixPath('case/out') / member
            if '..' in path.parts or path.is_absolute():
                continue
            if (Path(worktree) / path).is_file() and not (Path(worktree) / path).is_symlink():
                files.append((part['name'], str(path)))
    return sorted(set(files))


def digest(path):
    """A part's identity. A STEP export differs run to run in its HEADER timestamp and in the
    translator's per-process product counter ("Open CASCADE STEP translator 7.8 2"): both are
    left out, so re-exporting unchanged geometry keeps the same digest."""
    data = Path(path).read_bytes()
    if Path(path).suffix.lower() in ('.step', '.stp'):
        marker = data.find(b'ENDSEC;')
        if data.lstrip().startswith(b'ISO-10303-21') and marker >= 0:
            data = data[marker + len(b'ENDSEC;'):]
        data = re.sub(rb"Open CASCADE STEP translator [^']*", b'Open CASCADE STEP translator', data)
    return hashlib.sha256(data).hexdigest()


def notes_markdown(ledger):
    """The one notes list the person reads: what the team moved past, and why."""
    lines = ['# Case notes', '',
             'The case team builds in stages: simple parts, moving parts, joints and fits, then the lid.',
             f'Each stage gets at most {MAX_REPAIRS} repair tries per check, then what still fails is noted '
             'here and the team moves on. A part that passed is not reworked.',
             'Written by the harness; do not edit.', '']
    for name in ledger['plan']:
        entry = ledger['stages'][name]
        status = {'open': 'in progress', 'passed': 'passed', 'noted': 'moved on with notes'}[entry['status']]
        lines.append(f'- {STAGE_TITLES[name]}: {status}')
    lines.append('')
    if not ledger['notes']:
        lines.append('No notes: every stage passed its checks.')
    for item in ledger['notes']:
        lines.append(f"- [ ] **{STAGE_TITLES.get(item['stage'], item['stage'])}** ({item['key']}): {item['message']}")
    if ledger.get('end'):
        lines += ['', f"Lead review at the end: {ledger['end']}" +
                  (f" — {ledger['endSummary']}" if ledger.get('endSummary') else '')]
    return '\n'.join(lines) + '\n'


def prompt_text(ledger):
    """What the builder does this turn."""
    stage = current(ledger)
    closed = [name for name in ledger['plan'] if ledger['stages'][name]['status'] != 'open']
    frozen = sorted(ledger['frozen'])
    text = ['\nCASE WATERFALL (the harness tracks it; follow it):']
    if stage is None:
        text.append('Every stage is closed. Change only what the lead advice below names.')
    else:
        entry = ledger['stages'][stage]
        text.append(f'Current stage: {stage} ({STAGE_TITLES[stage]}), parts {ledger["parts"].get(stage, [])}. '
                    'Build or repair ONLY this stage this turn, export it, declare its checks with '
                    f'"stage": "{stage}" in {CHECKS_FILE}, then end your turn: the harness checks it right away.')
        if entry['fails']:
            text.append('Failed so far: ' + json.dumps(entry['fails'], sort_keys=True) +
                        f'; a check gets {MAX_REPAIRS} repair tries, then it is noted and the team moves on.')
    text.append(BUILD_RULE)
    if closed:
        text.append('Closed stages (never rework their parts; the harness restores any change): ' +
                    ', '.join(f"{name} {ledger['stages'][name]['status']}" for name in closed) + '.')
    if frozen:
        text.append('Frozen files: ' + ', '.join(frozen[:40]) + ('…' if len(frozen) > 40 else '') + '.')
    if ledger['notes']:
        text.append(f'{len(ledger["notes"])} note(s) are on the list in {NOTES_FILE}; do not try to fix them now.')
    if ledger.get('followUp'):
        text.append('This is a follow-up request: build only the new part it asks for and its own check '
                    '(printable, and fits where it glues or mounts); every earlier part stays as it is and is '
                    'not rechecked. Do exactly what was asked, nothing extra.')
    return ' '.join(text)


def brief(ledger):
    """The compact form the tester and the lead read."""
    return {'current': current(ledger), 'plan': ledger['plan'],
            'status': {name: ledger['stages'][name]['status'] for name in ledger['plan']},
            'parts': {name: ledger['parts'].get(name, []) for name in ledger['plan']},
            'notes': [f"{item['stage']}/{item['key']}: {item['message']}" for item in ledger['notes'][:30]],
            'frozen': sorted(ledger['frozen'])[:60], 'followUp': bool(ledger.get('followUp'))}


def status_document(ledger, phase=None, glue=()):
    """case/stages.json: each stage and part with its status chip; `phase` is the current stage's."""
    now_stage = current(ledger)
    glue = set(glue)
    stages, parts = [], {}
    for name in ledger['plan']:
        entry = ledger['stages'][name]
        if entry['status'] == 'passed':
            status = 'done' if ledger.get('finished') else 'valid'
        elif entry['status'] == 'noted':
            status = 'note'
        elif name == now_stage:
            status = phase if phase in ('working', 'testing') else 'working'
        else:
            status = 'waiting'
        members = ledger['parts'].get(name, [])
        notes = [item['message'] for item in ledger['notes'] if item['stage'] == name][:10]
        stages.append({'name': name, 'title': STAGE_TITLES[name], 'status': status, 'parts': members,
                       'notes': notes})
        for part in members:
            group = ('glue-on' if part in glue else 'frame') if name == 'simple' else name
            parts.setdefault(part, {'stage': name, 'group': group, 'status': status})
    return {'schema': 1, 'current': now_stage, 'finished': bool(ledger.get('finished')),
            'stages': stages, 'parts': parts, 'notes': len(ledger['notes']),
            'checks': ledger.get('checkLevels', {})}


def record_checks(ledger, stage, summary):
    """A closing stage keeps its checks' results: they are reused, not rerun, while its parts stay frozen."""
    for entry in summary.get('checks', []):
        if entry.get('stage') == stage and not entry.get('reused'):
            ledger['checkLevels'][entry['name']] = [entry['level'], str(entry['message'])[:300]]


def frozen_parts(ledger):
    return {entry.get('part') for entry in ledger['frozen'].values() if entry.get('part')}


def reuse_for(ledger, checks):
    """{check name: (level, message)} to reuse: its stage is closed, or every part it names is frozen."""
    frozen = frozen_parts(ledger)
    reuse = {}
    for check in checks:
        level = ledger['checkLevels'].get(check['name'])
        if level is None:
            continue
        closed = ledger['stages'].get(check.get('stage'), {}).get('status') in ('passed', 'noted')
        scoped = bool(check.get('parts')) and set(check['parts']) <= frozen
        if closed or scoped:
            reuse[check['name']] = tuple(level)
    return reuse


def rework(worktree):
    """Earlier parts the person's follow-up request names ("rework" in case/checks.json)."""
    try:
        names = json.loads((Path(worktree) / CHECKS_FILE).read_text(encoding='utf-8')).get('rework', [])
    except (OSError, ValueError, AttributeError):
        return []
    return [name for name in names if isinstance(name, str)][:50] if isinstance(names, list) else []


def previous_parts(document):
    """Parts an earlier accepted run closed (valid, done or noted), from its case/stages.json."""
    if not isinstance(document, dict) or not document.get('finished') or not isinstance(document.get('parts'), dict):
        return set()
    return {name for name, info in document['parts'].items()
            if isinstance(info, dict) and info.get('status') in ('valid', 'done', 'note')}


def seed(ledger, document, frozen):
    """A follow-up run ("add X"): what an earlier run closed stays closed, frozen and not rechecked.

    `frozen` is the set of earlier part names now frozen. A stage with only earlier parts starts
    closed; a stage with a new part opens for that part alone.
    """
    checks = document.get('checks') if isinstance(document, dict) else None
    if isinstance(checks, dict):
        for name, value in checks.items():
            if isinstance(value, list) and len(value) == 2 and value[0] in (
                    case_measure.PASS, case_measure.REVIEW, case_measure.UNVERIFIED, case_measure.FAIL):
                ledger['checkLevels'][name] = [value[0], str(value[1])[:300]]
    ledger['followUp'] = True
    for name in ledger['plan']:
        members = ledger['parts'].get(name, [])
        if members and set(members) <= frozen:
            ledger['stages'][name]['status'] = 'passed'
            ledger['stages'][name]['seeded'] = True
    return ledger
