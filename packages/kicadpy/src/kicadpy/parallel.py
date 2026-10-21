"""Opt-in, isolated contract-first KiCad pilot. Never called by the default tile/Stop hook.

Codex CLI integration: https://learn.chatgpt.com/docs/non-interactive-mode
Role ownership is an import check, not an OS security boundary. Real jobs require --execute.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import tarfile
import threading
import time

from .parallel_contract import (CONTRACT_SCHEMA, RESULT_SCHEMA, REVIEW_SCHEMA, ROLES,
                                OWNED, digest, encoded, owns, validate, validate_contract)

LEAD = 'gpt-6-astra'
WORKER = 'gpt-6.1-sol'
# Match the existing tile: native KiCad/pcbnew/routing cannot use workspace-write (README).
WORKER_SANDBOX = 'danger-full-access'
SKIP = {'.git', '.circuit', '.harness', '.kicadpy', '__pycache__', 'node_modules'}
MAX_FILE = 128 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
ACTIVE = {}
ACTIVE_LOCK = threading.Lock()


def pipeline_sha(root):
    """Bind the Python/checker source and launcher scripts, not mutable build caches."""
    root = Path(root)
    paths = []
    for relative in ('packages/kicadpy/src', 'packages/circuitpy/src', 'harness/kicad/toolchain'):
        paths.extend(p for p in (root / relative).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for relative in ('viewer/src/server/circuit/caseExport.mjs', 'harness/kicad/harness.json'):
        path = root / relative
        if path.is_file():
            paths.append(path)
    return digest({p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(paths)})


def cancel_jobs(root):
    with ACTIVE_LOCK:
        for pid in list(ACTIVE.get(str(root), ())):
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_bytes(encoded(value))
    os.replace(temporary, path)


def git(cwd, *args):
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                                    *args], cwd=cwd, stderr=subprocess.PIPE).decode().strip()


def snapshot(workspace):
    """Hash real outputs, rejecting links and limiting accidental build-directory explosions."""
    result, total = {}, 0
    for directory, dirs, files in os.walk(workspace):
        for name in dirs + files:
            if name != '.git' and (Path(directory) / name).is_symlink():
                raise ValueError('Pilot snapshots do not support symlinks')
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in sorted(files):
            if name == '.git':
                continue
            file = Path(directory) / name
            size = file.stat().st_size
            total += size
            if size > MAX_FILE or total > MAX_TOTAL or len(result) >= 10000:
                raise ValueError('Pilot output exceeds snapshot bounds')
            result[file.relative_to(workspace).as_posix()] = {
                'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                'executable': bool(file.stat().st_mode & 0o111),
            }
    return result


def changed(before, after):
    return sorted(n for n in before.keys() | after.keys() if before.get(n) != after.get(n))


def commit(workspace, names, message):
    if not names:
        return git(workspace, 'rev-parse', 'HEAD')
    for name in names:
        git(workspace, 'add', '-f', '--', name)
    if git(workspace, 'diff', '--cached', '--name-only'):
        git(workspace, '-c', 'user.name=KiCad Pilot', '-c', 'user.email=pilot@localhost',
            'commit', '-qm', message)
    return git(workspace, 'rev-parse', 'HEAD')


def initialize(directory, prompt_file, *, instructions=None, seed=None, mode='parallel',
               lead_model=LEAD, worker_model=WORKER, timeout=1800):
    """Create a new experiment; seed is a clean Git revision, never the live working tree."""
    root = Path(directory).resolve()
    if root.exists():
        raise ValueError('Pilot directory must not exist; never initialize in a live run')
    if mode not in ('parallel', 'sequential', 'solo') or not 10 <= timeout <= 14400:
        raise ValueError('Invalid mode or timeout')
    prompt = Path(prompt_file).read_text()
    if not prompt.strip() or len(prompt) > 100000:
        raise ValueError('Expected a bounded nonempty product prompt')
    source = Path(instructions or Path(os.environ.get('KICAD_HARNESS_ROOT', Path(__file__).resolve().parents[4])) / 'harness/kicad').resolve()
    texts = {'AGENTS.md': (source / 'AGENTS.md').read_text(),
             'skills/kicad/SKILL.md': (source / 'skills/kicad/SKILL.md').read_text()}
    for p in sorted((source / 'skills/kicad/references').glob('*.md')):
        texts['skills/kicad/references/' + p.name] = p.read_text()
    seed_files, seed_commit = {}, None
    if seed:
        seed = Path(seed).resolve()
        if git(seed, 'rev-parse', '--show-toplevel') != str(seed) or git(seed, 'status', '--porcelain'):
            raise ValueError('Seed must be the root of a clean Git repository')
        if root.is_relative_to(seed):
            raise ValueError('Create the pilot outside the seed repository')
        seed_commit = git(seed, 'rev-parse', 'HEAD')
        archive = subprocess.check_output(['git', 'archive', seed_commit], cwd=seed)
        total = 0
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar:
                name = PurePosixPath(member.name)
                if name.is_absolute() or '..' in name.parts or member.issym() or member.islnk():
                    raise ValueError('Unsafe seed member')
                if member.isfile() and (owns('solo', str(name)) or str(name) in ('README.md', 'project.json')):
                    total += member.size
                    if member.size > MAX_FILE or total > MAX_TOTAL or len(seed_files) >= 10000:
                        raise ValueError('Seed file too large')
                    seed_files[str(name)] = (tar.extractfile(member).read(), member.mode)
    root.mkdir(parents=True)
    control = root / '.pilot'
    control.mkdir()
    (control / 'prompt.txt').write_text(prompt)
    for name, text in texts.items():
        p = control / 'instructions' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    workspace = root / 'workspace'
    workspace.mkdir()
    (workspace / '.circuit').mkdir()
    git(workspace, 'init', '-q', '-b', 'pilot')
    for name, (data, mode_bits) in seed_files.items():
        p = workspace / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(mode_bits & 0o777)
    (workspace / 'AGENTS.md').write_text(
        '# Experimental pilot workspace\nFollow the stage-specific job and PILOT_CONTRACT.json. '
        'Do not commit, push, order, flash or access hardware.\n\n' + texts['AGENTS.md'])
    (workspace / 'PILOT_BRIEF.md').write_text(prompt)
    if not (workspace / 'project.json').exists():
        write(workspace / 'project.json', {'id': 'workspace', 'name': root.name, 'engine': 'kicad-native'})
    if not (workspace / 'product.json').exists():
        write(workspace / 'product.json', {'name': root.name, 'description': prompt})
    (workspace / '.git/info/exclude').write_text('.circuit/\n.harness/\n.kicadpy/\n__pycache__/\nnode_modules/\n')
    names = sorted(snapshot(workspace))
    base = commit(workspace, names, 'Seed isolated KiCad pilot')
    state = {'schema': 1, 'status': 'initialized', 'mode': mode, 'leadModel': lead_model,
             'workerModel': worker_model, 'timeoutSeconds': timeout, 'revision': 0,
             'workerSandbox': WORKER_SANDBOX,
             'seedCommit': seed_commit, 'baseCommit': base, 'jobs': [], 'createdAt': time.time(),
             'planAttempts': 0, 'rounds': [],
             'instructionsSha256': digest(texts), 'promptSha256': digest(prompt),
             'pipelineSha256': pipeline_sha(source.parents[1]),
             'toolchainRoot': str(source.parents[1]), 'hardwareTested': False}
    write(control / 'state.json', state)
    return state


def load(root):
    root = Path(root).resolve()
    state = json.loads((root / '.pilot/state.json').read_text())
    if state.get('schema') != 1:
        raise ValueError('Not a pilot')
    prompt = (root / '.pilot/prompt.txt').read_text()
    texts = {p.relative_to(root / '.pilot/instructions').as_posix(): p.read_text()
             for p in (root / '.pilot/instructions').rglob('*.md')}
    if digest(prompt) != state['promptSha256'] or digest(texts) != state['instructionsSha256']:
        raise ValueError('Prompt/instruction snapshot changed; use a new benchmark run')
    if (root / 'workspace/PILOT_BRIEF.md').read_text() != prompt:
        raise ValueError('Workspace brief changed')
    if pipeline_sha(state['toolchainRoot']) != state['pipelineSha256']:
        raise ValueError('Pipeline/checker source changed; use a pinned checkout for this pilot')
    if state.get('contractFile'):
        contract_path = (root / state['contractFile']).resolve()
        if not contract_path.is_relative_to(root / '.pilot/contracts'):
            raise ValueError('Invalid sealed contract path')
        contract = json.loads(contract_path.read_text())
        if digest(contract) != state['contractSha256'] or contract['revision'] != state['revision']:
            raise ValueError('Sealed contract changed')
        mirror = json.loads((root / 'workspace/PILOT_CONTRACT.json').read_text())
        if mirror != contract:
            raise ValueError('Workspace contract is stale')
    return root, state


@contextlib.contextmanager
def locked(root):
    with (Path(root) / '.pilot/lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another pilot operation is running') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def usage_of(text):
    totals = None
    for line in text.splitlines():
        try:
            event = json.loads(line)
            usage = event.get('usage')
            if event.get('type') != 'turn.completed' or not isinstance(usage, dict):
                continue
            fields = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
            if any(isinstance(usage.get(k, 0), bool) or not isinstance(usage.get(k, 0), int) or usage.get(k, 0) < 0 for k in fields):
                continue
            if not all(k in usage for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')):
                continue
            if usage['cached_input_tokens'] > usage['input_tokens']:
                continue
            totals = totals or {k: 0 for k in fields}
            for k in fields:
                totals[k] += usage.get(k, 0)
        except (ValueError, AttributeError):
            continue
    return totals


def codex(root, state, stage, model, workspace, prompt, schema, readonly):
    """One bounded CLI process. Preserve logs/usage even on model failure or timeout."""
    job = Path(root) / '.pilot/jobs' / f"a{state['planAttempts']:03}-r{state['revision']:03}-{stage}"
    job.mkdir(parents=True, exist_ok=False)
    write(job / 'schema.json', schema)
    (job / 'prompt.txt').write_text(prompt)
    output = job / 'answer.json'
    command = ['codex', 'exec', '--ignore-user-config', '--ephemeral', '--json',
               '-m', model, '-c', 'approval_policy="never"',
               '-c', 'model_reasoning_effort="medium"' if readonly else 'model_reasoning_effort="high"',
               '-s', 'read-only' if readonly else WORKER_SANDBOX, '-C', str(workspace),
               '--output-schema', str(job / 'schema.json'), '-o', str(output), '-']
    env = {k: v for k, v in os.environ.items() if not k.startswith('HARNESS_') and
           k not in ('CIRCUIT_WORKSPACE', 'CIRCUIT_SKILLS_DIR', 'CODEX_THREAD_ID')}
    tool_root = Path(state['toolchainRoot'])
    tile_env = json.loads((tool_root / 'harness/kicad/harness.json').read_text())['agent']['env']
    for key, value in tile_env.items():
        if key.startswith(('KICAD_', 'PICO_')) or key in ('CIRCUIT_TOOLCHAIN', 'CMAKE_PREFIX_PATH', 'DISABLE_AUTOUPDATER'):
            value = value.replace('${dsh}', str(tool_root / 'harness/kicad')).replace('${workspace}', str(workspace))
            if '${' in value:
                raise ValueError('Unresolved tile environment placeholder')
            env[key] = str(Path(value).resolve()) if value.startswith('/') else value
    env.update(KICAD_ADVISOR='off', KICAD_HARNESS_ROOT=str(tool_root),
               KICAD_HARNESS_PYTHON=str(tool_root / 'harness/kicad/toolchain/python'),
               KICAD_CASE_PYTHON=str(tool_root / 'harness/kicad/toolchain/case-python'),
               HARNESS_SKILLS_DIR=str(Path(root) / '.pilot/instructions/skills'),
               CIRCUIT_SKILLS_DIR=str(Path(root) / '.pilot/instructions/skills'))
    began = time.time()
    stdout, stderr, result, error = '', '', None, None
    process = None
    io_complete = False
    try:
        process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
        with ACTIVE_LOCK:
            ACTIVE.setdefault(str(root), set()).add(process.pid)
        stdout, stderr = process.communicate(prompt, timeout=state['timeoutSeconds'])
        io_complete = True
        if process.returncode:
            raise ValueError(f'Codex exited {process.returncode}')
        result = json.loads(output.read_text())
        validate(result, schema)
    except (OSError, ValueError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        error = str(exc) or type(exc).__name__
    finally:
        if process is not None:
            # Include children left running after the CLI exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if not io_complete:
                try:
                    stdout, stderr = process.communicate(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    # A separately-sessioned tool must not hold the controller's pipes forever.
                    decode = lambda value: value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
                    stdout, stderr = decode(exc.output), decode(exc.stderr)
                    process.stdout.close()
                    process.stderr.close()
                    error = (error or '') + '; job pipes did not close after termination'
            with ACTIVE_LOCK:
                ACTIVE.get(str(root), set()).discard(process.pid)
        (job / 'events.jsonl').write_text(stdout)
        (job / 'stderr.log').write_text(stderr)
    receipt = {'stage': stage, 'model': model, 'startedAt': began, 'seconds': time.time() - began,
               'usage': usage_of(stdout), 'result': result, 'error': error,
               'logDirectory': str(job.relative_to(root))}
    write(job / 'receipt.json', receipt)
    return receipt


def call(root, state, stage, workspace, prompt, schema, readonly, runner):
    model = state['leadModel'] if readonly else state['workerModel']
    began = time.time()
    try:
        receipt = runner(root, state, stage, model, workspace, prompt, schema, readonly)
    except Exception as exc:  # preserve a failed client receipt; no silent missing-usage job
        receipt = {'stage': stage, 'model': model, 'startedAt': began, 'seconds': time.time() - began,
                   'usage': None, 'result': None, 'error': f'{type(exc).__name__}: {exc}'}
    # List append is serialized here; writes to state.json stay on the controller thread.
    with ACTIVE_LOCK:
        state['jobs'].append(receipt)
    return receipt


def seal(root, state, interfaces):
    validate_contract(interfaces)
    revision = state['revision'] + 1
    contract = {'schema': 1, 'revision': revision, 'interfaces': interfaces,
                'promptSha256': state['promptSha256'], 'instructionsSha256': state['instructionsSha256']}
    sha = digest(contract)
    filename = f'.pilot/contracts/r{revision:03}-{sha}.json'
    write(root / filename, contract)
    write(root / 'workspace/PILOT_CONTRACT.json', contract)
    commit(root / 'workspace', ['PILOT_CONTRACT.json'], f'Seal contract revision {revision}')
    state.update(revision=revision, contractSha256=sha, contractFile=filename, status='planned', findings=[])
    state.pop('artifactManifest', None)


def plan(directory, *, execute=False, revise=False, contract_from=None, runner=codex):
    if not execute and not contract_from:
        raise ValueError('Model planning requires explicit --execute')
    root, state = load(directory)
    with locked(root):
        root, state = load(root)
        if state['status'] not in ('initialized', 'planning_failed') and not revise:
            raise ValueError('A contract already exists; use plan --revise to invalidate all worker outputs')
        if state['status'] == 'running':
            raise ValueError('Cannot revise an active round')
        workspace = root / 'workspace'
        if git(workspace, 'status', '--porcelain'):
            raise ValueError('Integration workspace changed outside the pilot; resolve it before planning')
        state['planAttempts'] += 1
        began = time.time()
        prompt = ('You are the LEAD planning an isolated native KiCad experiment. Read AGENTS.md and '
                  'the supplied product brief. Do not modify files or run hardware. Publish a complete '
                  'shared contract matching the schema: mm coordinate frame, exact board outline and '
                  'mounts, power rails, device pins/nets, actual module positions/envelopes, mechanical '
                  'envelope and firmware behavior. Use owned/reference components; mark unmeasured '
                  'dimensions and uncertain hardware in assumptions. Give disjoint board, firmware '
                  'and case/frame tasks. Frame and device mounts first; outer covers follow integration. '
                  'Never weaken native electrical/manufacturing checks. If revising, resolve prior '
                  'findings and preserve compatible work.\n\nPRODUCT:\n' +
                  (root / '.pilot/prompt.txt').read_text() + '\n\nPRIOR FINDINGS:\n' +
                  json.dumps(state.get('findings', [])))
        before = snapshot(workspace)
        try:
            if contract_from:
                other_root, other = load(contract_from)
                for key in ('promptSha256', 'instructionsSha256', 'pipelineSha256', 'seedCommit'):
                    if state[key] != other[key]:
                        raise ValueError(f'Control inputs differ: {key}')
                if state['revision'] or not other.get('contractFile') or other['revision'] != 1:
                    raise ValueError('Reuse a revision-one sealed plan only into a fresh pilot')
                interfaces = json.loads((other_root / other['contractFile']).read_text())['interfaces']
                state['planReusedFrom'] = {'directory': str(other_root), 'contractSha256': other['contractSha256']}
            else:
                job = call(root, state, 'plan', workspace, prompt, CONTRACT_SCHEMA, True, runner)
                if job['error']:
                    raise ValueError(job['error'])
                interfaces = job['result']
            if snapshot(workspace) != before:
                raise ValueError('Read-only planner changed workspace')
            seal(root, state, interfaces)
        except Exception as exc:  # a malformed result must not leave a runnable/approved plan
            # Keep the previously sealed revision readable; log directories identify every attempt.
            state['status'], state['findings'] = 'planning_failed', [str(exc)]
        finally:
            state['planningSeconds'] = state.get('planningSeconds', 0) + time.time() - began
            write(root / '.pilot/state.json', state)
            write(root / '.pilot/metrics.json', metrics(state))
        return state


def worker_prompt(state, role, stage):
    task = {'board': 'Build and publish the real native schematic/PCB and enclosure contract.',
            'firmware': 'Implement and build firmware from the shared pin/power contract; include firmware/WIRING.md.',
            'case': 'Build the functional frame/device/joint mounts only. Keep covers for the integration stage.',
            'solo': 'Build board, firmware and functional frame in that order; defer cosmetic covers to integration.'}[role]
    if role == 'solo' and stage.endswith('-final'):
        task = ('Reconcile firmware/WIRING against the actual published netlist and rebuild. Then '
                'complete removable covers, full-travel checks, interactive overview and arranged '
                'production/TEST slicer projects from the actual board/STEP. Preserve board interfaces; '
                'run native firmware and case checks.')
    elif stage.endswith('-final'):
        task = ('Reconcile firmware pins/power/behavior and WIRING against the actual published netlist and rebuild.'
                if role == 'firmware' else
                'Use the actual published board/STEP and checked functional frame. Add removable covers, '
                'check full travel with all ribs/hardware/wires, generate the interactive overview and '
                'native arranged production/TEST slicer projects; run the real case checks.')
    allowed = [p for r in (ROLES if role == 'solo' else (role,)) for p in OWNED[r]]
    return ('This is a SCOPED WORKER stage of an experimental KiCad team, not a request to finish other '
            'roles or run the usual full-build Stop loop. Read AGENTS.md, PILOT_BRIEF.md, PILOT_CONTRACT.json and the '
            'KiCad tool card/references under HARNESS_SKILLS_DIR. The sealed contract has precedence '
            'for interfaces. Never edit it. Never commit/push, delegate to another model, change gates, '
            'flash, access serial/hardware, order or print. Use the specified local toolchains; '
            'KICAD_ADVISOR is off. Do not modify the toolchain/shared knowledge or run knowledge learn. '
            'Normal native scratch stores (.kicadpy/.circuit/.harness) are allowed but never imported. '
            'All changes, scripts and persistent build outputs must be inside '
            f'these owned paths: {json.dumps(allowed)}. Other domains are read-only; do not write a '
            'top-level README, .git/config or other shared inputs. Record a blocker instead of silently '
            'changing an interface. Keep routine engineering choices autonomous and state assumptions. '
            'Native gates remain authoritative; CAD does not prove physical fit.\n' + task +
            f'\nReturn stage={stage}, contractSha256={state["contractSha256"]}, concise summary and blockers. '
            'An unresolved task is a blocker, never a claimed success.')


def dispatch(root, state, stages, runner):
    workspace = root / 'workspace'
    base = git(workspace, 'rev-parse', 'HEAD')
    baseline = snapshot(workspace)
    metadata = lambda: {name: (workspace / '.git' / name).read_bytes()
                        if (workspace / '.git' / name).exists() else None
                        for name in ('config', 'info/exclude', 'info/attributes')}
    git_before = metadata()
    jobs = []
    for role, stage in stages:
        dest = root / 'workers' / f"r{state['revision']:03}-{stage}"
        dest.parent.mkdir(exist_ok=True)
        if dest.exists():
            raise ValueError('Stage already attempted; revise the contract for a new bounded round')
        git(workspace, 'worktree', 'add', '--detach', str(dest), base)
        (dest / '.circuit').mkdir(exist_ok=True)
        jobs.append((role, stage, dest, (dest / '.git').read_bytes()))
    def one(job):
        role, stage, dest, pointer = job
        receipt = call(root, state, stage, dest, worker_prompt(state, role, stage), RESULT_SCHEMA, False, runner)
        return role, stage, dest, pointer, receipt
    if state['mode'] == 'sequential':
        results = [one(job) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            try:
                results = list(pool.map(one, jobs))
            except KeyboardInterrupt:
                cancel_jobs(root)
                raise
    imports, failures = [], []
    for role, stage, dest, pointer, receipt in results:
        try:
            if receipt['error']:
                raise ValueError(receipt['error'])
            validate(receipt['result'], RESULT_SCHEMA)
            result = receipt['result']
            if result['contractSha256'] != state['contractSha256'] or result['stage'] != stage:
                raise ValueError('Worker answered for another contract/stage')
            if result['blockers']:
                raise ValueError('; '.join(result['blockers']))
            if (dest / '.git').read_bytes() != pointer:
                raise ValueError('Worker changed its Git worktree pointer')
            if git(dest, 'rev-parse', 'HEAD') != base:
                raise ValueError('Worker committed or moved HEAD')
            after = snapshot(dest)
            names = changed(baseline, after)
            if any(not owns(role, n) for n in names):
                raise ValueError('Worker changed a contract/shared/unowned file')
            imports.append((dest, names, after))
        except (OSError, ValueError) as exc:
            failures.append(f'{stage}: {exc}')
    if failures:
        raise ValueError('\n'.join(failures))
    load(root)  # reject changed frozen inputs before importing any worker output
    if snapshot(workspace) != baseline or git(workspace, 'rev-parse', 'HEAD') != base:
        raise ValueError('Integration workspace changed while workers ran')
    if metadata() != git_before or git(workspace, 'diff', '--cached', '--name-only'):
        raise ValueError('Worker changed shared Git metadata or the integration index')
    # Capture and verify bytes before importing any role. Ownership sets are disjoint.
    payloads = {}
    for dest, members, after in imports:
        for name in members:
            data = (dest / name).read_bytes() if name in after else None
            if data is not None and hashlib.sha256(data).hexdigest() != after[name]['sha256']:
                raise ValueError('Worker output changed after validation')
            payloads[name] = (data, (dest / name).stat().st_mtime_ns if data is not None else None)
    names = []
    for dest, members, after in imports:
        for name in members:
            target = workspace / name
            if name in after:
                target.parent.mkdir(parents=True, exist_ok=True)
                data, mtime = payloads[name]
                target.write_bytes(data)
                target.chmod(0o755 if after[name]['executable'] else 0o644)
                # Firmware freshness uses source/binary mtimes; importing must retain build order.
                os.utime(target, ns=(mtime, mtime))
            else:
                target.unlink()
            names.append(name)
    commit(workspace, sorted(set(names)), 'Integrate ' + ', '.join(s for _, s in stages))


def native_check(workspace, complete):
    """Use the existing parsed native publisher, firmware freshness and case gates unchanged."""
    from .autofinish import inspect_board
    from .firmware import firmware_built, firmware_written
    from .case import status
    (Path(workspace) / '.circuit').mkdir(exist_ok=True)
    passed, findings = inspect_board(workspace)
    if complete:
        if not firmware_written(workspace) or not firmware_built(workspace):
            findings.append('Firmware is missing or its compiled binary is stale')
        if not (Path(workspace) / 'firmware/WIRING.md').is_file():
            findings.append('firmware/WIRING.md is missing')
        case_state, reason = status(workspace)
        if case_state != 'passed':
            findings.append('Final case has not passed the existing native case check: ' + reason)
        if not (Path(workspace) / 'case/README.md').is_file():
            findings.append('case/README.md assembly/overview entry point is missing')
        try:
            check_plates(workspace)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            findings.append('Prepared print project is not verified: ' + str(exc))
    return {'passed': passed and not findings, 'findings': findings,
            'scope': 'native-board-firmware-case' if complete else 'native-board', 'hardwareTested': False}


def check_plates(workspace):
    """Reuse Print all's binding validator without exporting, opening a slicer or printing."""
    module = Path(__file__).resolve().parents[4] / 'viewer/src/server/circuit/caseExport.mjs'
    script = '''import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
const {preparedPrintProject} = await import(pathToFileURL(process.argv[1]).href);
const root = process.argv[2];
const manifest = JSON.parse(fs.readFileSync(root + '/case/out/parts.json', 'utf8'));
if (!preparedPrintProject(root, manifest)) throw new Error('Missing native arranged printProject');
console.log(JSON.stringify({passed:true}));'''
    process = subprocess.run(['node', '--input-type=module', '-e', script, str(module), str(workspace)],
                             text=True, capture_output=True, timeout=30)
    if process.returncode or json.loads(process.stdout).get('passed') is not True:
        raise ValueError(process.stderr[-1600:] or 'Print project binding failed')


def review(root, state, stage, checks, runner):
    workspace = root / 'workspace'
    before = snapshot(workspace)
    prompt = ('You are the independent LEAD reviewer. Read PILOT_CONTRACT.json and actual artifacts, '
              'not raw worker transcripts. Check the actual netlist/board/published STEP against '
              'contract outline, mounts, coordinates, power and pin assignments; compare actual '
              'firmware definitions and WIRING with that netlist. For structure-review verify exposed '
              'device/frame/joint insertion, retention, tool access and travel before covers. For '
              'final-review check the complete case, overview, arranged 3MF and reports against final '
              'board/STEP, fasteners and wires. Report file-cited engineering defects only. Do not '
              'edit anything, run hardware or weaken gates. A model approval cannot override a failed '
              'native gate. Physical fit/strength remains untested. Approve only if there are no '
              'unresolved concerns; otherwise revise with existing file paths and concrete findings. '
              f'Return contractSha256={state["contractSha256"]}.\nNATIVE CHECKS:\n' + json.dumps(checks))
    job = call(root, state, stage, workspace, prompt, REVIEW_SCHEMA, True, runner)
    if job['error']:
        raise ValueError(job['error'])
    validate(job['result'], REVIEW_SCHEMA)
    r = job['result']
    if snapshot(workspace) != before or r['contractSha256'] != state['contractSha256']:
        raise ValueError('Review changed artifacts or used another contract')
    for finding in r['findings']:
        p = (workspace / finding['path']).resolve()
        if not p.is_relative_to(workspace.resolve()) or not p.is_file():
            raise ValueError('Review finding must cite an existing workspace file')
    if r['decision'] == 'approve' and r['findings']:
        raise ValueError('Approval with unresolved findings is invalid')
    if r['decision'] != 'approve':
        raise ValueError(r['summary'] + ': ' + json.dumps(r['findings']))
    return r


def run(directory, *, execute=False, runner=codex, checker=native_check):
    if not execute:
        raise ValueError('Paid/model execution requires explicit --execute')
    root, state = load(directory)
    with locked(root):
        root, state = load(root)
        if state['status'] != 'planned':
            raise ValueError('Plan first; a failed/completed round needs plan --revise before retry')
        workspace = root / 'workspace'
        state['status'], state['roundStartedAt'] = 'running', time.time()
        write(root / '.pilot/state.json', state)
        try:
            stages = [('solo', 'solo')] if state['mode'] == 'solo' else [(r, r) for r in ROLES]
            dispatch(root, state, stages, runner)
            before = snapshot(workspace)
            checks = checker(workspace, False)
            names = changed(before, snapshot(workspace))
            if any(not owns('board', n) for n in names):
                raise ValueError('Native board check wrote outside board-owned artifacts')
            commit(workspace, names, 'Record native board publication')
            write(root / f".pilot/rounds/{state['revision']}/board-check.json", checks)
            review(root, state, 'structure-review', checks, runner)
            if not checks.get('passed'):
                raise ValueError('Native board check failed: ' + str(checks.get('findings')))
            # Both finishers now read the actual integrated PCB/netlist/STEP. Covers come last.
            final = ([('solo', 'solo-final')] if state['mode'] == 'solo' else
                     [('firmware', 'firmware-final'), ('case', 'case-final')])
            dispatch(root, state, final, runner)
            before = snapshot(workspace)
            checks = checker(workspace, True)
            names = changed(before, snapshot(workspace))
            if any(not owns('solo', n) for n in names):
                raise ValueError('Native final check wrote outside owned artifacts')
            commit(workspace, names, 'Record final native checks')
            write(root / f".pilot/rounds/{state['revision']}/final-check.json", checks)
            review(root, state, 'final-review', checks, runner)
            if not checks.get('passed'):
                raise ValueError('Native final check failed: ' + str(checks.get('findings')))
            load(root)  # reject edits to frozen input snapshots and sealed contract
            state['artifactManifest'] = snapshot(workspace)
            state['status'], state['findings'] = 'candidate_ready', []
        except Exception as exc:  # fail closed and persist diagnostics even for a checker/client defect
            state['status'], state['findings'] = 'needs_revision', [f'{type(exc).__name__}: {exc}']
        except KeyboardInterrupt:
            state['status'], state['findings'] = 'interrupted', ['Operator interrupted the pilot']
        finally:
            state['roundFinishedAt'] = time.time()
            state['rounds'].append({'revision': state['revision'], 'status': state['status'],
                                    'seconds': state['roundFinishedAt'] - state['roundStartedAt']})
            write(root / '.pilot/state.json', state)
            write(root / '.pilot/metrics.json', metrics(state))
        return state


def metrics(state):
    jobs = state['jobs']
    totals = {k: sum((j.get('usage') or {}).get(k, 0) for j in jobs)
              for k in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')}
    totals['fresh_input_tokens'] = totals['input_tokens'] - totals['cached_input_tokens']
    return {'schema': 1, 'mode': state['mode'], 'leadModel': state['leadModel'],
            'workerSandbox': state['workerSandbox'],
            'workerModel': state['workerModel'], 'contractSha256': state.get('contractSha256'),
            'promptSha256': state['promptSha256'], 'instructionsSha256': state['instructionsSha256'],
            'pipelineSha256': state['pipelineSha256'],
            'seedCommit': state['seedCommit'], 'repairRounds': max(0, state['revision'] - 1),
            'wallSeconds': state.get('planningSeconds', 0) + sum(r['seconds'] for r in state['rounds']),
            'elapsedSinceInitSeconds': state.get('roundFinishedAt', time.time()) - state['createdAt'],
            'rounds': state['rounds'], 'planAttempts': state['planAttempts'],
            'planReusedFrom': state.get('planReusedFrom'),
            'agentSeconds': sum(j['seconds'] for j in jobs), 'reportedUsage': totals,
            'usageComplete': bool(jobs) and all(j.get('usage') is not None for j in jobs),
            'leadJobs': [j for j in jobs if j['stage'] in ('plan', 'structure-review', 'final-review')],
            'workerJobs': [j for j in jobs if j['stage'] not in ('plan', 'structure-review', 'final-review')],
            'advisorCalls': 0, 'approvalCalls': 0, 'actualBilledUsd': None, 'estimatedCostUsd': None,
            'hardwareTested': False, 'status': state['status']}


def status(directory):
    root, state = load(directory)
    if state['status'] == 'candidate_ready' and snapshot(root / 'workspace') != state['artifactManifest']:
        return {**state, 'status': 'stale', 'findings': ['Artifacts changed after final review']}
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    init = subs.add_parser('init')
    init.add_argument('directory')
    init.add_argument('--prompt', required=True)
    init.add_argument('--instructions', help='Installed harness/kicad directory')
    init.add_argument('--seed', help='Optional clean Git workspace; archived, never modified')
    init.add_argument('--mode', choices=['parallel', 'sequential', 'solo'], default='parallel')
    init.add_argument('--lead-model', default=LEAD)
    init.add_argument('--worker-model', default=WORKER)
    init.add_argument('--timeout', type=int, default=1800)
    for name in ('plan', 'run', 'status', 'metrics'):
        p = subs.add_parser(name)
        p.add_argument('directory')
        if name in ('plan', 'run'):
            p.add_argument('--execute', action='store_true')
        if name == 'plan':
            p.add_argument('--revise', action='store_true')
            p.add_argument('--contract-from', help='Reuse a revision-one plan with identical frozen inputs; no model call')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            result = initialize(args.directory, args.prompt, instructions=args.instructions, seed=args.seed,
                                mode=args.mode, lead_model=args.lead_model, worker_model=args.worker_model,
                                timeout=args.timeout)
        elif args.command == 'plan':
            result = plan(args.directory, execute=args.execute, revise=args.revise, contract_from=args.contract_from)
        elif args.command == 'run':
            result = run(args.directory, execute=args.execute)
        else:
            result = status(args.directory)
            if args.command == 'metrics':
                result = metrics(result)
        failed = result.get('status') in ('needs_revision', 'interrupted', 'planning_failed', 'stale')
        print(json.dumps({'ok': not failed, **result}))
        return 2 if failed else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
