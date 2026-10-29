"""Lead-routed 3D print coordination, separate from design readiness.

This module never starts a printer. A verified case project identifies proposed
plates; only an exact user message can create a physical-print confirmation.
Printer observations are evidence of a job state, not of a usable physical part.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from . import parallel
from .parallel_addendum import PRINT_ROLE, load_addenda, notes_for_role
from .parallel_contract import STRING, array, digest, obj, validate


HEX64 = re.compile(r'[0-9a-f]{64}\Z')
LEDGER = 'print/events.jsonl'
MAX_LEDGER_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 1000
MAX_PARTS = 128
PLAN_SCHEMA = obj({
    'contractSha256': STRING,
    'action': {'type': 'string', 'enum': ['ask_print', 'record_confirmation', 'observe', 'wait']},
    'summary': STRING,
    'question': STRING,
    'plateIndex': {'type': 'number'},  # 0 means no verified matching plate
    'parts': array(obj({'name': STRING, 'quantity': {'type': 'number'},
                        'instanceIds': array(STRING)})),
    'confirmationStatus': {'type': 'string', 'enum': ['none', 'printed', 'fit', 'failed', 'discarded']},
    'observationStatus': {'type': 'string', 'enum': ['none', 'queued', 'printing', 'completed', 'failed', 'unknown']},
    'printerJobId': STRING,  # '-' means no observed job ID
})
REVIEW_SCHEMA = obj({
    'contractSha256': STRING,
    'decision': {'type': 'string', 'enum': ['approve', 'revise']},
    'summary': STRING,
    'findings': array(STRING),
})
STATUSES = {
    'proposal': frozenset(('proposed', 'unavailable')),
    'printer_observation': frozenset(('queued', 'printing', 'completed', 'failed', 'unknown')),
    'user_confirmation': frozenset(('printed', 'fit', 'failed', 'discarded')),
}


def _safe_file(root, relative, maximum):
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f'Linked print evidence: {relative}')
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError(f'Missing or oversized print evidence: {relative}')
    return path


def _case_snapshot(workspace):
    """Bind print coordination to the existing native 3MF verifier and part IDs."""
    manifest_path = workspace / 'case/out/parts.json'
    if not manifest_path.exists():
        return None
    manifest_path = _safe_file(workspace, 'case/out/parts.json', 2 * 1024 * 1024)
    manifest = json.loads(manifest_path.read_text())
    project = manifest.get('printProject')
    if not isinstance(project, dict) or not project.get('file') or not project.get('report'):
        return None
    # Match the Case tab's native readiness gate, including the handwired path.
    module = Path(__file__).resolve().parents[4] / 'viewer/src/server/circuit/catalog.mjs'
    script = ('import {pathToFileURL} from "node:url"; '
              'const m=await import(pathToFileURL(process.argv[1]).href); '
              'const root=process.argv[2], status=m.casePrintStatus(root,"main"); '
              'const board=status.scope==="pcb" ? '
              'm.scanProjectCatalog({projectDir:root,projectId:"print-evidence"}).entries.find('
              'entry=>entry.file==="design/main.kicad_pcb") : null; '
              'console.log(JSON.stringify({state:status.state, '
              'boardReady:status.scope!=="pcb" || !!board && board.nativeStale!==true}));')
    readiness = subprocess.run(['node', '--input-type=module', '-e', script,
                                str(module), str(workspace)], text=True, capture_output=True,
                               timeout=15)
    try:
        report = json.loads(readiness.stdout) if not readiness.returncode else {}
        ready = report.get('state') == 'passed' and report.get('boardReady') is True
    except (ValueError, AttributeError):
        ready = False
    if not ready:
        return None
    if (manifest.get('verification') or {}).get('nativePCBCheckerApplies') is not False:
        verdict_file = workspace / '.harness/verdict.json'
        if (not verdict_file.is_file() or verdict_file.is_symlink() or
                json.loads(verdict_file.read_text()).get('ready') is not True):
            return None
    try:
        parallel.check_plates(workspace)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    out = (workspace / 'case/out').resolve(strict=True)
    case_dir = (workspace / 'case').resolve(strict=True)
    files = []
    for key in ('file', 'report'):
        relative = project[key]
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError('Invalid prepared print project path')
        file = (out / relative).resolve(strict=True)
        if not file.is_relative_to(case_dir) or file == case_dir or not file.is_file():
            raise ValueError('Prepared print project leaves case/')
        current = workspace
        for part in file.relative_to(workspace).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('Prepared print project has a linked path')
        files.append(file)
    project_file, report_file = files
    report = json.loads(report_file.read_text())
    parts = {}
    for part in manifest.get('parts', []):
        if (part.get('printable') is False or part.get('role') == 'coupon' or
                'DO_NOT_PRINT' in str(part.get('name', '')).upper()):
            continue
        name, declared_sha = part.get('name'), part.get('sha256STL')
        quantity = part.get('quantity', 1)
        stl = part.get('stl')
        if (not isinstance(name, str) or not name or name in parts or
                not isinstance(stl, str) or not stl or Path(stl).is_absolute() or
                '..' in Path(stl).parts or
                type(quantity) is not int or quantity < 1 or quantity > 1000):
            raise ValueError('Invalid printable part identity')
        stl_file = _safe_file(workspace, 'case/out/' + stl, 64 * 1024 * 1024)
        sha = hashlib.sha256(stl_file.read_bytes()).hexdigest()
        if declared_sha is not None and declared_sha != sha:
            raise ValueError('Declared printable mesh hash differs from actual STL')
        parts[name] = {'name': name, 'revision': str(part.get('revision') or manifest.get('revision') or 'unknown'),
                       'sha256STL': sha, 'quantity': quantity}
    plates = {}
    for plate in report.get('plates', []):
        index = plate.get('index')
        names = [str(name).removesuffix('.stl') for name in plate.get('parts', [])]
        if type(index) is not int or index < 1 or index in plates or any(name not in parts for name in names):
            raise ValueError('Invalid verified print plate')
        plates[index] = names
    if not parts or not plates:
        raise ValueError('Verified print project lacks production parts or plates')
    return {'revision': str(manifest.get('revision') or 'unknown'),
            'manifestSha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            'printProjectSha256': hashlib.sha256(project_file.read_bytes()).hexdigest(),
            'parts': parts, 'plates': plates}


def _account_snapshot(value):
    """Accept only a bounded, sanitized observation; credentials never enter model views."""
    if value is None:
        return {'status': 'unavailable', 'source': 'printer_lan', 'jobs': []}
    if not isinstance(value, dict) or value.get('status') not in ('unavailable', 'connected'):
        raise ValueError('Invalid printer account snapshot')
    jobs = value.get('jobs', [])
    if not isinstance(jobs, list) or len(jobs) > 128:
        raise ValueError('Printer account job list exceeds bounds')
    clean = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError('Invalid printer job')
        item = {key: job.get(key) for key in ('id', 'status', 'fileName', 'plateIndex',
                                               'projectSha256', 'startedAt', 'finishedAt',
                                               'progressPct', 'remainingMinutes',
                                               'currentLayer', 'totalLayers', 'observedAt')}
        if (not isinstance(item['id'], str) or not 0 < len(item['id']) <= 200 or
                item['status'] not in STATUSES['printer_observation']):
            raise ValueError('Invalid printer job ID or status')
        if item['projectSha256'] is not None and (not isinstance(item['projectSha256'], str) or
                                                  not HEX64.fullmatch(item['projectSha256'])):
            raise ValueError('Invalid printer job project hash')
        if item['plateIndex'] is not None and (type(item['plateIndex']) is not int or
                                               item['plateIndex'] < 1):
            raise ValueError('Invalid printer job plate')
        for key, maximum in (('progressPct', 100), ('remainingMinutes', 100000),
                             ('currentLayer', 100000), ('totalLayers', 100000)):
            number = item[key]
            if number is not None and (type(number) not in (int, float) or
                                       not 0 <= number <= maximum or
                                       (key != 'progressPct' and type(number) is not int)):
                raise ValueError(f'Invalid printer job {key}')
        if any(v is not None and (not isinstance(v, str) or len(v) > 300)
               for k, v in item.items() if k not in ('plateIndex', 'progressPct',
                                                    'remainingMinutes', 'currentLayer',
                                                    'totalLayers')):
            raise ValueError('Oversized printer job field')
        clean.append(item)
    return {'status': value['status'], 'source': 'printer_lan', 'jobs': clean}


def _local_account_snapshot():
    """Ask the optional passive LAN adapter for one current status, never history."""
    script = Path(__file__).resolve().parents[4] / 'viewer/src/server/circuit/bambuConfig.mjs'
    if not script.is_file():
        return None
    try:
        proc = subprocess.run(['node', str(script), '--json'], text=True, capture_output=True,
                              timeout=9, check=False)
        if proc.returncode or len(proc.stdout) > 128 * 1024:
            return None
        observed = json.loads(proc.stdout)
        if not isinstance(observed, dict) or not isinstance(observed.get('account'), dict):
            return None
        jobs = []
        for item in observed.get('jobs', []):
            if not isinstance(item, dict):
                continue
            state = {'preparing': 'queued', 'printing': 'printing',
                     'finished': 'completed', 'failed': 'failed'}.get(item.get('status'), 'unknown')
            jobs.append({'id': item.get('jobId'), 'status': state,
                         'fileName': item.get('jobName'), 'plateIndex': None,
                         'projectSha256': None, 'startedAt': None, 'finishedAt': None,
                         'progressPct': item.get('progressPct'),
                         'remainingMinutes': item.get('remainingMinutes'),
                         'currentLayer': item.get('currentLayer'),
                         'totalLayers': item.get('totalLayers'),
                         'observedAt': item.get('observedAt')})
        return {'status': 'connected' if observed['account'].get('state') == 'connected'
                else 'unavailable', 'jobs': jobs}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _read_ledger(workspace, contract_sha):
    path = workspace / LEDGER
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Print ledger cannot be linked')
    if not path.exists():
        return []
    if not path.is_file() or path.stat().st_size > MAX_LEDGER_BYTES:
        raise ValueError('Invalid print ledger size')
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('Print ledger has an incomplete final line')
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError('Print ledger is not UTF-8') from exc
    entries, previous = [], contract_sha
    for line in lines:
        if not line:
            raise ValueError('Print ledger contains a blank line')
        if len(entries) >= MAX_EVENTS or len(line) > 16_000:
            raise ValueError('Print ledger exceeds event bounds')
        entry = json.loads(line)
        if line != json.dumps(entry, sort_keys=True, separators=(',', ':'), ensure_ascii=True):
            raise ValueError('Print ledger event is not canonical JSON')
        if (not isinstance(entry, dict) or entry.get('schema') != 1 or
                entry.get('sequence') != len(entries) + 1 or
                entry.get('previousSha256') != previous or
                entry.get('contractSha256') != contract_sha or
                entry.get('kind') not in STATUSES or
                entry.get('status') not in STATUSES[entry['kind']] or
                not isinstance(entry.get('eventId'), str) or
                not HEX64.fullmatch(entry['eventId']) or
                not isinstance(entry.get('sha256'), str) or
                entry['sha256'] != digest({k: v for k, v in entry.items() if k != 'sha256'})):
            raise ValueError('Print ledger chain or event changed')
        previous = entry['sha256']
        entries.append(entry)
    if len({entry['eventId'] for entry in entries}) != len(entries):
        raise ValueError('Duplicate print event ID')
    return entries


def _append_event(workspace, contract_sha, unsigned):
    directory = workspace / 'print'
    if directory.is_symlink():
        raise ValueError('Print directory cannot be linked')
    directory.mkdir(exist_ok=True)
    lock = workspace / '.circuit/print-ledger.lock'
    lock.parent.mkdir(exist_ok=True)
    if lock.is_symlink():
        raise ValueError('Print ledger lock cannot be linked')
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        entries = _read_ledger(workspace, contract_sha)
        if any(entry['eventId'] == unsigned['eventId'] for entry in entries):
            return next(entry for entry in entries if entry['eventId'] == unsigned['eventId'])
        entry = {'schema': 1, 'sequence': len(entries) + 1,
                 'previousSha256': entries[-1]['sha256'] if entries else contract_sha,
                 **unsigned}
        entry['sha256'] = digest(entry)
        line = json.dumps(entry, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n'
        if (len(line) > 16_000 or sum(len(json.dumps(e, sort_keys=True, separators=(',', ':')) + '\n')
                                   for e in entries) + len(line) > MAX_LEDGER_BYTES):
            raise ValueError('Print event exceeds ledger bounds')
        with (directory / 'events.jsonl').open('a', encoding='utf-8') as output:
            output.write(line)
            output.flush()
        return entry


def copy_ledger_to_pilot(workspace, pilot_root):
    """Snapshot canonical physical evidence as a read-only input to later case work."""
    workspace = Path(workspace).resolve(strict=True)
    pilot_root, state = parallel.load(pilot_root)
    source = workspace / LEDGER
    target = pilot_root / 'workspace' / LEDGER
    if not source.exists():
        if target.exists():
            raise ValueError('Seed contains print history absent from the visible project')
        return False
    _read_ledger(workspace, state['contractSha256'])
    raw = source.read_bytes()
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('Pilot print ledger cannot be linked')
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(raw)
    if target.read_bytes() != raw:
        raise ValueError('Pilot print ledger copy changed')
    parallel.commit(pilot_root / 'workspace', [LEDGER], 'Snapshot confirmed print evidence')
    return True


def initial_print_note(pilot_root):
    """The initial handoff comes from the one sealed prompt, without a new note."""
    pilot_root, state = parallel.load(pilot_root)
    if state.get('revision') != 1 or not state.get('contractSha256'):
        raise ValueError('Initial print handoff needs the sealed original contract')
    raw = (pilot_root / '.pilot/prompt.txt').read_bytes()
    return {'sha256': state['contractSha256'], 'contractSha256': state['contractSha256'],
            'userText': raw.decode('utf-8'),
            'userTextSha256': hashlib.sha256(raw).hexdigest(),
            'routes': [PRINT_ROLE]}


def _model_call(root, state, stage, view, prompt, schema, runner):
    before = parallel.snapshot(view)
    for delay in (0, *parallel.CAPACITY_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        receipt = runner(root, state, stage, state['workerModel'], view, prompt, schema, True)
        if parallel.snapshot(view) != before:
            raise ValueError('Read-only print team changed its evidence view')
        if not receipt.get('error'):
            result = receipt.get('result')
            validate(result, schema)
            return result
        if receipt.get('failureKind') != 'model_capacity':
            raise ValueError(parallel.job_error(receipt))
    raise parallel.ModelCapacityExhausted(parallel.job_error(receipt))


def _event_from_plan(plan, case, account, note, prior):
    action = plan['action']
    index = plan['plateIndex']
    if type(index) is not int or index < 0:
        raise ValueError('Print team returned an invalid plate index')
    if len(plan['parts']) > MAX_PARTS or len({p['name'] for p in plan['parts']}) != len(plan['parts']):
        raise ValueError('Print team returned duplicate or excessive parts')
    if action == 'record_confirmation' and plan['confirmationStatus'] == 'none':
        raise ValueError('A physical confirmation needs an explicit status')
    if action == 'record_confirmation' and not index:
        raise ValueError('A physical confirmation needs a verified print plate')
    if action != 'record_confirmation' and plan['confirmationStatus'] != 'none':
        raise ValueError('A non-confirmation cannot claim a physical status')
    if action == 'observe' and plan['observationStatus'] == 'none':
        raise ValueError('A printer observation needs a reported job status')
    if action != 'observe' and plan['observationStatus'] != 'none':
        raise ValueError('Only account observations can carry printer status')
    if case is None and (index or plan['parts']):
        raise ValueError('Unverified case output cannot identify physical parts')
    if case is not None and index and index not in case['plates']:
        raise ValueError('Unknown verified print plate')
    known_ids = {}
    for event in prior:
        if event['kind'] == 'user_confirmation':
            for part in event['parts']:
                for instance in part['instanceIds']:
                    known_ids[instance] = (part['name'], part['revision'],
                                           part['sha256STL'], event['status'])
    parts = []
    for selected in plan['parts']:
        name, quantity = selected['name'], selected['quantity']
        if (case is None or name not in case['parts'] or type(quantity) is not int or
                quantity < 1 or quantity > 1000 or
                (index and name not in case['plates'][index])):
            raise ValueError('Print part does not match verified case or quantity')
        identity = case['parts'][name]
        refs = selected['instanceIds']
        if action == 'record_confirmation' and plan['confirmationStatus'] != 'printed':
            if (len(refs) != quantity or len(set(refs)) != len(refs) or
                    any(known_ids.get(ref) not in ((name, identity['revision'],
                                                    identity['sha256STL'], 'printed'),
                                                   (name, identity['revision'],
                                                    identity['sha256STL'], 'fit'))
                        for ref in refs)):
                raise ValueError('Physical status update needs identified existing print instances')
            ids = refs
        else:
            if refs:
                raise ValueError('New print claim cannot reuse physical instance IDs')
            ids = ([digest({'note': note['sha256'], 'part': name, 'instance': n})
                    for n in range(quantity)] if action == 'record_confirmation' else [])
        parts.append({**identity, 'quantity': quantity, 'instanceIds': ids})
    if action == 'ask_print' and (case is None or not index or not parts):
        raise ValueError('Print request needs verified plate and parts')
    if action == 'record_confirmation' and not parts:
        raise ValueError('No verified physical part was identified')
    job_id = None if plan['printerJobId'] == '-' else plan['printerJobId']
    matched_job = next((job for job in account['jobs'] if job['id'] == job_id), None)
    if action == 'observe':
        if matched_job is None or matched_job['status'] != plan['observationStatus']:
            raise ValueError('Printer observation is absent from account evidence')
        if (case is None or matched_job['projectSha256'] != case['printProjectSha256'] or
                matched_job['plateIndex'] != index):
            if parts or index:
                raise ValueError('Unmatched printer job cannot identify case parts or plate')
        elif parts and set(p['name'] for p in parts) != set(case['plates'][index]):
            raise ValueError('Observed plate parts do not match the verified print project')
    elif job_id is not None:
        raise ValueError('Only account observations can cite a printer job')
    kind = ('user_confirmation' if action == 'record_confirmation' else
            'printer_observation' if action == 'observe' else 'proposal')
    status = (plan['confirmationStatus'] if kind == 'user_confirmation' else
              plan['observationStatus'] if kind == 'printer_observation' else
              'proposed' if action == 'ask_print' else 'unavailable')
    event_id = (note['sha256'] if kind == 'user_confirmation' else
                digest({'note': note['sha256'], 'kind': kind, 'status': status,
                        'case': case['printProjectSha256'] if case else None,
                        'job': job_id}))
    evidence_kind = 'printer_lan' if kind == 'printer_observation' else 'user_message'
    evidence_sha = (digest(account) if kind == 'printer_observation' else note['userTextSha256'])
    return {'eventId': event_id, 'at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'kind': kind, 'status': status, 'contractSha256': note['contractSha256'],
            'releaseNoteSha256': note['sha256'],
            'case': ({key: case[key] for key in ('revision', 'manifestSha256', 'printProjectSha256')}
                     if case else None), 'plateIndex': index or None, 'parts': parts,
            'printerJobId': job_id,
            'evidence': {'kind': evidence_kind, 'sha256': evidence_sha,
                         'summary': ('Exact lead-routed user message' if kind != 'printer_observation'
                                     else 'Sanitized current LAN printer status')},
            'summary': plan['summary']}


def run_print_team(workspace, pilot_root, release_note, *, runner=None, account_snapshot=None):
    """Run print planner and independent reviewer for one lead-routed note.

    A print-only follow-up does not call the design dispatcher or modify
    `teamReady`. Printer access is optional; absent account access is explicit.
    """
    workspace = Path(workspace).resolve(strict=True)
    pilot_root, state = parallel.load(pilot_root)
    if PRINT_ROLE not in release_note.get('routes', ()):
        raise ValueError('Lead did not route this message to the print team')
    chain = load_addenda(pilot_root, state['contractSha256'])
    initial = (release_note == initial_print_note(pilot_root) and
               state.get('status') in ('candidate_ready', 'print_only'))
    if not initial and not any(entry == release_note for entry in chain['entries']):
        raise ValueError('Print team needs a verified lead release note')
    if hashlib.sha256(release_note['userText'].encode('utf-8')).hexdigest() != release_note['userTextSha256']:
        raise ValueError('Print note user text changed')
    case = _case_snapshot(workspace)
    account = _account_snapshot(_local_account_snapshot() if account_snapshot is None
                                else account_snapshot)
    prior = _read_ledger(workspace, state['contractSha256'])
    with tempfile.TemporaryDirectory(prefix='print-team-', dir=pilot_root / '.pilot') as directory:
        view = Path(directory)
        (view / 'AGENTS.md').write_text('Read-only print coordination. Do not start a printer, '
                                       'edit CAD or claim a print from a job status.\n')
        (view / 'PILOT_CONTRACT.json').write_bytes((pilot_root / 'workspace/PILOT_CONTRACT.json').read_bytes())
        (view / 'USER_NOTE.md').write_text(release_note['userText'])
        parallel.write(view / 'CASE_EVIDENCE.json', case or {'status': 'unavailable'})
        parallel.write(view / 'ACCOUNT_STATUS.json', account)
        parallel.write(view / 'PRINT_EVENTS.json', prior)
        parallel.git(view, 'init', '-q', '-b', 'print')
        parallel.git(view, 'add', '--', 'AGENTS.md', 'PILOT_CONTRACT.json', 'USER_NOTE.md',
                     'CASE_EVIDENCE.json', 'ACCOUNT_STATUS.json', 'PRINT_EVENTS.json')
        parallel.git(view, '-c', 'user.name=KiCad Print', '-c', 'user.email=print@localhost',
                     'commit', '-qm', 'Snapshot print evidence')
        runner = runner or parallel.codex
        prompt = (
            'You are the 3D PRINT PLANNER, assigned by the lead. Read the exact USER_NOTE.md, '
            'original sealed contract, verified CASE_EVIDENCE.json, ACCOUNT_STATUS.json, '
            'and earlier PRINT_EVENTS.json. Do not edit files, operate a printer, or change CAD. '
            'The user speaks only to the lead. ACCOUNT_STATUS is only a current local LAN '
            'observation, not cloud account history. If a verified plate is ready and the user has '
            'not explicitly asked to print it, propose a precise question asking which plate '
            'to print; do not send or start a job. An account status of completed never proves '
            'a physical part exists or fits. Record user_confirmation only when the exact '
            'user message explicitly confirms a physical part and quantity. Use current part '
            'name/revision/STL identity from CASE_EVIDENCE; uncertain identity requires a question, '
            'not a guessed record. For fit/failed/discarded, cite existing instance IDs. '
            'If account access or verified case evidence is unavailable, state that plainly. '
            'For missing values use plateIndex=0, printerJobId="-", confirmationStatus="none", '
            'observationStatus="none", question="-", and parts=[]. '
            f'Return contractSha256={state["contractSha256"]}.\n\n'
            + notes_for_role(pilot_root, PRINT_ROLE)
        )
        plan = _model_call(pilot_root, state, 'print-plan-' + release_note['sha256'][:12],
                           view, prompt, PLAN_SCHEMA, runner)
        if plan['contractSha256'] != state['contractSha256']:
            raise ValueError('Print planner used another contract')
        unsigned = _event_from_plan(plan, case, account, release_note, prior)
        parallel.write(view / 'PROPOSAL.json', {'plan': plan, 'event': unsigned})
        parallel.git(view, 'add', '--', 'PROPOSAL.json')
        parallel.git(view, '-c', 'user.name=KiCad Print', '-c', 'user.email=print@localhost',
                     'commit', '-qm', 'Snapshot print proposal')
        review_prompt = (
            'You are the independent 3D PRINT REVIEWER. Inspect USER_NOTE.md, CASE_EVIDENCE.json, '
            'ACCOUNT_STATUS.json, PRINT_EVENTS.json and PROPOSAL.json. Do not edit files or operate '
            'a printer. Approve only if every proposed part, quantity, instance ID, plate and job '
            'is supported by the exact evidence. A printer-completed job is not a human-confirmed '
            'print; a sliced or opened 3MF is not a print. Reject any physical confirmation the '
            'user did not explicitly provide. Return concrete findings if revising. '
            f'Return contractSha256={state["contractSha256"]}.'
        )
        review = _model_call(pilot_root, state, 'print-review-' + release_note['sha256'][:12],
                             view, review_prompt, REVIEW_SCHEMA, runner)
        if review['contractSha256'] != state['contractSha256'] or review['decision'] != 'approve':
            raise ValueError('Print reviewer rejected proposed event: ' + json.dumps(review['findings']))
        if review['findings']:
            raise ValueError('Print reviewer approved with unresolved findings')
    event = _append_event(workspace, state['contractSha256'], unsigned)
    return {'status': event['status'], 'summary': plan['summary'],
            'question': None if plan['question'] == '-' else plan['question'],
            'event': event, 'accountStatus': account['status']}
