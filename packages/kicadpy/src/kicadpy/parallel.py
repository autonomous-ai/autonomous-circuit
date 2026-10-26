"""Opt-in, isolated contract-first KiCad pilot. Never called by the default tile/Stop hook.

Codex CLI integration: https://learn.chatgpt.com/docs/non-interactive-mode
Role ownership is an import check, not an OS security boundary. Real jobs require --execute.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
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

from .parallel_contract import (ADVICE_SCHEMA, CONTRACT_SCHEMA, RESULT_SCHEMA, REVIEW_SCHEMA, ROLES,
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
CAPACITY_RETRY_DELAYS = (5, 15)
MAX_STAGE_ATTEMPTS = 4
TIMEOUT_CONTINUATION_SECONDS = 600
BOARD_STAGE_WALL_SECONDS = 3 * 60 * 60
BOARD_MAX_TIMEOUT_CONTINUATIONS = 6
MAX_ADVICE_ROUNDS = 2
MAX_TEAM_REPAIR_ROUNDS = 3
MAX_TEAM_INTEGRATION_ROUNDS = 2
TIMEOUT_ADVICE_SECONDS = 5 * 60
SHARED_INTERFACE_FIELDS = frozenset({
    'coordinateFrame', 'board', 'powerRails', 'pins', 'modules',
    'mechanicalEnvelopeMm', 'firmwareBehavior', 'assumptions',
})


def _stage_clock():
    return time.monotonic()


class ModelCapacityExhausted(ValueError):
    """A bounded model-capacity retry failed; changing the design contract cannot fix it."""


class JobInterrupted(ValueError):
    """The model process was interrupted and must not be treated as a design defect."""


class ExecutionLimitExhausted(ValueError):
    """A bounded job timed out; a new design contract cannot fix the execution limit."""


class ContractRevisionRequired(ValueError):
    """The lead identified an actual shared interface change."""

    def __init__(self, message, fields=()):
        super().__init__(message)
        self.fields = tuple(fields)


class ExternalBlocker(ValueError):
    """The lead identified an unresolved external requirement or dependency."""


def job_error(receipt):
    """Surface structured failure classification without changing the raw job receipt."""
    error = receipt['error']
    kind = receipt.get('failureKind')
    suppressed = receipt.get('retrySuppressed')
    if isinstance(suppressed, str) and suppressed:
        error += f'; retry suppressed: {suppressed}'
    if kind in ('model_capacity', 'timeout', 'interrupted'):
        attempts = receipt.get('attempt')
        suffix = f' after {attempts} attempt(s)' if isinstance(attempts, int) and attempts > 0 else ''
        return f'{kind}{suffix}: {error}'
    return error


def pipeline_sha(root):
    """Bind the Python/checker source and launcher scripts, not mutable build caches."""
    root = Path(root)
    paths = []
    for relative in ('packages/kicadpy/src', 'packages/circuitpy/src', 'harness/kicad/toolchain'):
        paths.extend(p for p in (root / relative).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    paths.extend((root / 'packages/kicadpy').glob('*.md'))
    paths.extend((root / 'packages/golden-blocks').rglob('BLOCK.md'))
    paths.extend((root / 'packages/golden-blocks/modules').rglob('module.json'))
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
    if mode not in ('parallel', 'sequential', 'solo', 'teams') or not 10 <= timeout <= 14400:
        raise ValueError('Invalid mode or timeout')
    prompt = Path(prompt_file).read_text()
    if not prompt.strip() or len(prompt) > 100000:
        raise ValueError('Expected a bounded nonempty product prompt')
    source = Path(instructions or Path(os.environ.get('KICAD_HARNESS_ROOT', Path(__file__).resolve().parents[4])) / 'harness/kicad').resolve()
    # Alternate instruction snapshots must not silently select another checker's Python/runtime.
    tool_root = Path(__file__).resolve().parents[4]
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
             'pipelineSha256': pipeline_sha(tool_root),
             'gitMetadataSha256': digest(_git_metadata_sha(workspace)),
             'toolchainRoot': str(tool_root), 'hardwareTested': False}
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
    if (state.get('gitMetadataSha256') is not None and
            digest(_git_metadata_sha(root / 'workspace')) != state['gitMetadataSha256']):
        raise ValueError('Pilot shared Git metadata changed outside the controller')
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


def capacity_event(text):
    """Recognize an actual Codex failure event, never text echoed from a prompt/tool."""
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get('type') not in ('error', 'turn.failed'):
            continue
        error = event.get('error')
        message = event.get('message') if event['type'] == 'error' else (
            error.get('message') if isinstance(error, dict) else None)
        if isinstance(message, str) and 'selected model is at capacity' in message.casefold():
            return True
    return False


def _job_directory(root, state, stage):
    """Keep a separate receipt and event stream for every same-stage CLI attempt."""
    parent = Path(root) / '.pilot/jobs'
    name = f"a{state['planAttempts']:03}-r{state['revision']:03}-{stage}"
    for attempt in range(1, 101):
        job = parent / (name if attempt == 1 else f'{name}-retry{attempt:02}')
        try:
            job.mkdir(parents=True, exist_ok=False)
            return job
        except FileExistsError:
            continue
    raise ValueError('Too many existing job attempts for one stage')


def _retry_files(workspace):
    """Cheap change detection before a retry; final worker import still hashes bytes."""
    files, total = {}, 0
    for directory, dirs, names in os.walk(workspace):
        for name in dirs + names:
            if name != '.git' and (Path(directory) / name).is_symlink():
                raise ValueError('Pilot snapshots do not support symlinks')
        dirs[:] = [name for name in dirs if name not in SKIP]
        for name in names:
            if name == '.git':
                continue
            path = Path(directory) / name
            stat = path.stat()
            if not path.is_file():
                raise ValueError('Retry workspace contains a non-file output')
            total += stat.st_size
            if stat.st_size > MAX_FILE or total > MAX_TOTAL or len(files) >= 10000:
                raise ValueError('Retry workspace exceeds snapshot bounds')
            files[path.relative_to(workspace).as_posix()] = (
                stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
                bool(stat.st_mode & 0o111))
    return files


def _integration_git_state(root):
    integration = Path(root) / 'workspace'
    head = integration / '.git/HEAD'
    return (git(integration, 'rev-parse', 'HEAD'),
            git(integration, 'ls-files', '--stage'),
            git(integration, 'status', '--porcelain'),
            hashlib.sha256(head.read_bytes()).hexdigest() if head.is_file() else None)


def _retry_git_metadata(root, workspace):
    """Detect worker/index and shared Git mutations before resuming."""
    integration = Path(root) / 'workspace'
    pointer = Path(workspace) / '.git'
    if pointer.is_symlink():
        raise ValueError('Worktree Git pointer is a symlink')
    # Git may refresh stat data in its index after legitimate owned file writes.
    # ls-files --stage checks staged contents without rejecting that refresh.
    paths = [integration / '.git' / name for name in ('config', 'info/exclude', 'info/attributes')]
    shared = tuple(hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
                   for path in paths)
    return {'worker': (git(workspace, 'rev-parse', 'HEAD'),
                       git(workspace, 'ls-files', '--stage'),
                       pointer.read_bytes() if pointer.is_file() else None),
            'integration': _integration_git_state(root), 'shared': shared}


def _retry_safety(root, workspace, stage, readonly, initial_files, initial_git,
                  authorized_integration=None):
    current_git = _retry_git_metadata(root, workspace)
    allowed = authorized_integration or (initial_git['integration'],)
    if (current_git['worker'] != initial_git['worker'] or
            current_git['shared'] != initial_git['shared'] or
            current_git['integration'] not in allowed):
        return False, 'Git metadata or HEAD changed during a failed model attempt', False
    current = _retry_files(workspace)
    edits = changed(initial_files, current)
    if readonly and edits:
        return False, 'Read-only stage changed workspace files', False
    role = stage.split('-', 1)[0]
    if edits and (role not in ROLES and role != 'solo' or any(not owns(role, name) for name in edits)):
        return False, 'Failed worker changed files outside its owned paths', False
    return True, None, bool(edits)


def _persist_attempt_receipt(root, receipt):
    """Keep the controller's retry decision in the same audited job receipt."""
    log_directory = receipt.get('logDirectory')
    if not isinstance(log_directory, str):
        return
    jobs = (Path(root) / '.pilot/jobs').resolve()
    file = (Path(root) / log_directory / 'receipt.json').resolve()
    if not file.is_relative_to(jobs) or not file.is_file():
        raise ValueError('Invalid job receipt path')
    write(file, receipt)


def codex(root, state, stage, model, workspace, prompt, schema, readonly):
    """One bounded CLI process. Preserve logs/usage even on model failure or timeout."""
    job = _job_directory(root, state, stage)
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
    interrupted = timed_out = False
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
        interrupted = isinstance(exc, KeyboardInterrupt)
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
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
               'failureKind': ('interrupted' if interrupted else 'timeout' if timed_out else
                               'model_capacity' if error and process is not None and
                               process.returncode not in (None, 0) and capacity_event(stdout) else None),
               'logDirectory': str(job.relative_to(root))}
    write(job / 'receipt.json', receipt)
    return receipt


def call(root, state, stage, workspace, prompt, schema, readonly, runner,
         authorized_integration=None, integration_lock=None, stage_deadline=None,
         on_timeout=None):
    model = state['leadModel'] if readonly else state['workerModel']
    board_stage = stage == 'board' and not readonly
    stage_budget = (max(state['timeoutSeconds'], BOARD_STAGE_WALL_SECONDS) if board_stage else
                    state['timeoutSeconds'] + TIMEOUT_CONTINUATION_SECONDS)
    max_attempts = (1 + len(CAPACITY_RETRY_DELAYS) + BOARD_MAX_TIMEOUT_CONTINUATIONS
                    if board_stage else MAX_STAGE_ATTEMPTS)
    began_at = _stage_clock()
    deadline = min(began_at + stage_budget, stage_deadline) if stage_deadline else began_at + stage_budget
    capacity_deadline = began_at + state['timeoutSeconds'] + TIMEOUT_CONTINUATION_SECONDS
    initial_files = _retry_files(workspace)
    # Stat changes guard ownership/Git retry safety; only changed bytes count as
    # fresh board progress, so a tool repeatedly touching the same file cannot
    # purchase another long continuation.
    previous_board_content = ({name: info['sha256'] for name, info in snapshot(workspace).items()}
                              if board_stage else None)
    if integration_lock is None:
        initial_git = _retry_git_metadata(root, workspace)
    else:
        with integration_lock:
            initial_git = _retry_git_metadata(root, workspace)
    capacity_retries = timeout_retries = 0
    short_timeout = False
    capacity_continuation = False
    continuation = None
    timeout_guidance = None
    previous_receipt = None
    for attempt in range(1, max_attempts + 1):
        remaining = (min(deadline, capacity_deadline) if capacity_continuation else
                     deadline) - _stage_clock()
        if remaining <= 0:
            if previous_receipt is not None:
                previous_receipt.pop('retryScheduled', None)
                previous_receipt['retrySuppressed'] = 'Stage execution budget exhausted'
                _persist_attempt_receipt(root, previous_receipt)
                return previous_receipt
            expired = {'stage': stage, 'model': model, 'startedAt': time.time(), 'seconds': 0,
                       'usage': None, 'result': None, 'error': 'Stage execution budget exhausted',
                       'failureKind': 'timeout', 'attempt': attempt,
                       'attemptTimeoutSeconds': 0}
            with ACTIVE_LOCK:
                state['jobs'].append(expired)
            return expired
        began = time.time()
        retry_prompt = prompt
        if continuation and not readonly:
            retry_prompt += ('\n\nThe previous attempt stopped because ' + continuation + '. '
                             'This is the same sealed stage and isolated worktree. Inspect any existing '
                             'role-owned files, continue the unfinished work, and return the required '
                             'stage result. Preserve completed work; do not change shared inputs.')
            if timeout_guidance is not None:
                retry_prompt += ('\nLEAD ADVICE ON THE PARTIAL WORK:\n' +
                                 json.dumps(timeout_guidance, sort_keys=True))
        attempt_limit = min(((state['timeoutSeconds'] if board_stage else
                              TIMEOUT_CONTINUATION_SECONDS) if short_timeout else
                             state['timeoutSeconds']), remaining)
        attempt_state = {**state, 'timeoutSeconds': attempt_limit}
        try:
            receipt = runner(root, attempt_state, stage, model, workspace,
                             retry_prompt, schema, readonly)
        except Exception as exc:  # preserve a failed client receipt; no silent missing-usage job
            receipt = {'stage': stage, 'model': model, 'startedAt': began, 'seconds': time.time() - began,
                       'usage': None, 'result': None, 'error': f'{type(exc).__name__}: {exc}'}
        receipt = dict(receipt)
        receipt['attempt'] = attempt
        receipt['attemptTimeoutSeconds'] = attempt_state['timeoutSeconds']
        # Record usage before any retry checks so interruption cannot discard a paid attempt.
        with ACTIVE_LOCK:
            state['jobs'].append(receipt)
        previous_receipt = receipt
        kind = receipt.get('failureKind') if receipt.get('error') else None
        may_retry_capacity = (kind == 'model_capacity' and
                              capacity_retries < len(CAPACITY_RETRY_DELAYS) and
                              attempt < max_attempts)
        may_retry_timeout = (kind == 'timeout' and not readonly and
                             timeout_retries < (BOARD_MAX_TIMEOUT_CONTINUATIONS if board_stage else 1) and
                             attempt < max_attempts)
        if board_stage and kind == 'timeout' and not may_retry_timeout:
            receipt['retrySuppressed'] = ('Board continuation attempt limit exhausted'
                                          if timeout_retries >= BOARD_MAX_TIMEOUT_CONTINUATIONS else
                                          'Stage attempt limit exhausted')
        if may_retry_capacity or may_retry_timeout:
            current_board_content = None
            try:
                if integration_lock is None:
                    safe, reason, partial = _retry_safety(
                        root, workspace, stage, readonly, initial_files, initial_git,
                        authorized_integration)
                else:
                    with integration_lock:
                        safe, reason, partial = _retry_safety(
                            root, workspace, stage, readonly, initial_files, initial_git,
                            authorized_integration)
                if board_stage and safe:
                    current_board_content = {name: info['sha256']
                                             for name, info in snapshot(workspace).items()}
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                safe, reason, partial = False, f'{type(exc).__name__}: {exc}', False
            fresh_progress = (any(
                previous_board_content.get(name) != current_board_content.get(name)
                for name in previous_board_content.keys() | current_board_content.keys()
                if owns('board', name))
                if board_stage and safe and current_board_content is not None else partial)
            if may_retry_timeout and safe and not fresh_progress:
                reason = 'Timed-out worker made no owned file progress'
            if safe and (may_retry_capacity or fresh_progress):
                delay = (CAPACITY_RETRY_DELAYS[capacity_retries]
                         if may_retry_capacity else 0)
                retry_deadline = capacity_deadline if may_retry_capacity else deadline
                if retry_deadline - _stage_clock() <= delay:
                    receipt['retrySuppressed'] = 'Stage execution budget exhausted'
                else:
                    receipt['retryScheduled'] = True
                    receipt['retryDelaySeconds'] = delay
                    receipt['continuedPartialWork'] = fresh_progress
                    if board_stage:
                        previous_board_content = current_board_content
                    if may_retry_capacity:
                        capacity_retries += 1
                        capacity_continuation = True
                        continuation = 'the model was temporarily at capacity'
                    else:
                        timeout_retries += 1
                        short_timeout = True
                        capacity_continuation = False
                        continuation = 'its execution time limit was reached after partial work'
                    receipt['retryReason'] = kind
            else:
                receipt['retrySuppressed'] = reason
                if not safe:
                    receipt['failureKind'] = ('unsafe_capacity_retry' if may_retry_capacity
                                              else 'unsafe_timeout_retry')
        _persist_attempt_receipt(root, receipt)
        if not receipt.get('retryScheduled'):
            return receipt
        if kind == 'timeout' and on_timeout is not None:
            try:
                new_guidance = on_timeout(receipt)
                if new_guidance is not None:
                    timeout_guidance = new_guidance
            except BaseException as exc:
                receipt.pop('retryScheduled', None)
                receipt['retrySuppressed'] = 'Lead timeout advice stopped continuation: ' + str(exc)
                _persist_attempt_receipt(root, receipt)
                raise
        if receipt['retryDelaySeconds']:
            try:
                time.sleep(receipt['retryDelaySeconds'])
            except KeyboardInterrupt:
                receipt.pop('retryScheduled', None)
                receipt['retrySuppressed'] = 'Operator interrupted before retry'
                _persist_attempt_receipt(root, receipt)
                raise
    raise AssertionError('Stage retry bound was exceeded')


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
    state.update(revision=revision, contractSha256=sha, contractFile=filename,
                 status='planned', findings=[], replanRequired=False)
    state.pop('artifactManifest', None)
    state.pop('requestedChangedFields', None)


def adopt_contract(directory, original_directory):
    """Carry the exact first contract into a follow-up run without replanning it."""
    root, state = load(directory)
    with locked(root):
        root, state = load(root)
        if state['status'] != 'initialized' or state['revision'] != 0:
            raise ValueError('Contract adoption needs a fresh pilot')
        source = Path(original_directory).resolve()
        original_state = json.loads((source / '.pilot/state.json').read_text())
        name = original_state.get('contractFile')
        if (original_state.get('revision') != 1 or not isinstance(name, str) or
                not (source / name).resolve().is_relative_to(source / '.pilot/contracts')):
            raise ValueError('Original pilot has no revision-one contract')
        contract = json.loads((source / name).read_text())
        sha = digest(contract)
        if (sha != original_state.get('contractSha256') or contract.get('revision') != 1 or
                contract.get('promptSha256') != state['promptSha256'] or
                digest((source / '.pilot/prompt.txt').read_text()) != state['promptSha256'] or
                json.loads((source / 'workspace/PILOT_CONTRACT.json').read_text()) != contract):
            raise ValueError('Original contract or frozen prompt changed')
        validate_contract(contract['interfaces'])
        destination = f'.pilot/contracts/r001-{sha}.json'
        write(root / destination, contract)
        write(root / 'workspace/PILOT_CONTRACT.json', contract)
        commit(root / 'workspace', ['PILOT_CONTRACT.json'], 'Carry original sealed contract')
        state.update(revision=1, contractSha256=sha, contractFile=destination,
                     contractOrigin=str(source), status='planned', findings=[],
                     replanRequired=False)
        write(root / '.pilot/state.json', state)
        write(root / '.pilot/metrics.json', metrics(state))
        return state


def _contract_field(interfaces, field):
    value = interfaces
    for key in field.split('.'):
        if not isinstance(value, dict) or key not in value:
            raise ValueError('Unknown shared contract field: ' + field)
        value = value[key]
    return value


def plan(directory, *, execute=False, revise=False, contract_from=None, runner=codex):
    if not execute and not contract_from:
        raise ValueError('Model planning requires explicit --execute')
    root, state = load(directory)
    with locked(root):
        root, state = load(root)
        if state['mode'] == 'teams' and revise:
            raise ValueError('The team base contract is immutable; route changes through a lead release note')
        if state['status'] not in ('initialized', 'planning_failed') and not revise:
            raise ValueError('A contract already exists; use plan --revise to invalidate all worker outputs')
        if state['status'] == 'running':
            raise ValueError('Cannot revise an active round')
        workspace = root / 'workspace'
        if git(workspace, 'status', '--porcelain'):
            raise ValueError('Integration workspace changed outside the pilot; resolve it before planning')
        state['planAttempts'] += 1
        began = time.time()
        task_order = (
            'Give disjoint board, firmware and full case tasks that can start concurrently '
            'from this interface contract. Define enough geometry, pins and power for '
            'firmware and case to work without a board handoff. All teams later reconcile '
            'with the actual native board during integration. '
            if state['mode'] == 'teams' else
            'Give disjoint board, firmware and case/frame tasks. Frame and device mounts first; '
            'outer covers follow integration. '
        )
        prompt = ('You are the LEAD planning an isolated native KiCad experiment. Read AGENTS.md and '
                  'the supplied product brief. Do not modify files or run hardware. Publish a complete '
                  'shared contract matching the schema: mm coordinate frame, exact board outline and '
                  'mounts, power rails, device pins/nets, actual module positions/envelopes, mechanical '
                  'envelope and firmware behavior. Use owned/reference components; mark unmeasured '
                  'dimensions and uncertain hardware in assumptions. ' + task_order +
                  'Never weaken native electrical/manufacturing checks. If revising, resolve prior '
                  'findings and preserve compatible work.\n\nFIELDS REQUIRING CHANGE:\n' +
                  json.dumps(state.get('requestedChangedFields', [])) + '\n\nPRODUCT:\n' +
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
                    if job.get('failureKind') == 'interrupted':
                        raise JobInterrupted(job_error(job))
                    if job.get('failureKind') == 'timeout':
                        raise ExecutionLimitExhausted(job_error(job))
                    if job.get('failureKind') == 'model_capacity':
                        raise ModelCapacityExhausted(job_error(job))
                    raise ValueError(job_error(job))
                interfaces = job['result']
            if snapshot(workspace) != before:
                raise ValueError('Read-only planner changed workspace')
            if revise and state.get('replanRequired'):
                old = json.loads((root / state['contractFile']).read_text())['interfaces']
                fields = state.get('requestedChangedFields', [])
                if not fields or interfaces == old or not all(
                        _contract_field(old, field) != _contract_field(interfaces, field)
                        for field in fields):
                    raise ValueError('Lead replan did not change a cited shared contract field')
            seal(root, state, interfaces)
        except (KeyboardInterrupt, JobInterrupted) as exc:
            state['status'], state['findings'] = 'interrupted', [str(exc) or 'Operator interrupted planning']
        except ExecutionLimitExhausted as exc:
            state['status'], state['findings'] = 'execution_limit', [str(exc)]
        except ModelCapacityExhausted as exc:
            state['status'], state['findings'] = 'model_unavailable', [str(exc)]
        except Exception as exc:  # a malformed result must not leave a runnable/approved plan
            # Keep the previously sealed revision readable; log directories identify every attempt.
            state['status'], state['findings'] = 'planning_failed', [str(exc)]
        finally:
            state['planningSeconds'] = state.get('planningSeconds', 0) + time.time() - began
            write(root / '.pilot/state.json', state)
            write(root / '.pilot/metrics.json', metrics(state))
        return state


def worker_prompt(state, role, stage):
    if state['mode'] == 'teams':
        task = {
            'board': 'Build and publish the native schematic, PCB, real netlist and board geometry.',
            'firmware': ('Build the complete firmware and firmware/WIRING.md from the initial interface '
                         'contract now. Check actual board outputs whenever available; report and repair '
                         'any mismatch during integration.'),
            'case': ('Build the functional case, mounts, covers, exports and checks from the initial '
                     'interface contract now. Check actual board geometry whenever available; report '
                     'and repair any mismatch during integration.'),
        }[role]
        return (
            'You are the BUILDER for the ' + role + ' team. Read AGENTS.md, PILOT_BRIEF.md, '
            'PILOT_CONTRACT.json and the KiCad tool card. The initial contract was sealed once; '
            'later lead release notes supplied below amend it in order and the newest applicable '
            'note wins for a changed field. Never edit the contract or release notes. Work only '
            f'inside {json.dumps(OWNED[role])}; other domains are read-only. Do not commit, push, '
            'delegate, order, print, flash, access hardware, alter gates or run the solo Stop loop. '
            'Your independent tester will inspect your actual files and may return concrete findings. '
            'Continue in the same worktree until the role task is complete or the bounded execution '
            'budget expires. Publish useful drafts early. A local pass is not final product readiness; '
            'the integration check reconciles all teams. If blocked, explain the exact unresolved '
            'issue and preserve partial files. ' + task +
            f'\nReturn stage={stage}, contractSha256={state["contractSha256"]}, concise summary and blockers.'
        )
    task = {'board': ('Build and publish the real native schematic/PCB and enclosure contract. '
                      'If a contract-level hardware or interface blocker cannot be resolved with '
                      'verified components and pins, return it promptly in blockers instead of '
                      'polishing a board that cannot pass native and lead gates.'),
            'firmware': 'Read the checked, published board and actual netlist first. Implement and build firmware '
                        'against its real pins and power rails and the shared contract; include firmware/WIRING.md.',
            'case': 'Use the checked, published board/STEP and its actual device placement to build the '
                    'functional frame/device/joint mounts. Keep covers for the integration stage.',
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
            'The solo KICAD_ADVISOR Stop hook is off; the team controller may send lead advice '
            'inside this same sealed stage. Do not modify the toolchain/shared knowledge or run knowledge learn. '
            'Normal native scratch stores (.kicadpy/.circuit/.harness) are allowed but never imported. '
            'All changes, scripts and persistent build outputs must be inside '
            f'these owned paths: {json.dumps(allowed)}. Other domains are read-only; do not write a '
            'top-level README, .git/config or other shared inputs. Record a blocker instead of silently '
            'changing an interface. Keep routine engineering choices autonomous and state assumptions. '
            'Native gates remain authoritative; CAD does not prove physical fit.\n' + task +
            f'\nReturn stage={stage}, contractSha256={state["contractSha256"]}, concise summary and blockers. '
            'An unresolved task is a blocker, never a claimed success.')


def _draft_source(role, name):
    """Only editable inputs; never carry a prior contract's reports or generated products."""
    path = PurePosixPath(name)
    parts = path.parts
    if any(part in {'out', 'build', 'reports', 'export', 'exports', 'dist', 'target',
                    'node_modules', '__pycache__', '.pio', '_fab', '_review',
                    'libraries', 'references', 'vendor'} for part in parts):
        return False
    tool_role = {'board': 'board', 'firmware': 'firmware', 'case': 'case'}.get(role)
    if (tool_role and len(parts) > 2 and parts[:2] == ('tools', tool_role) and
            path.suffix in {'.py', '.sh', '.ts', '.tsx', '.js', '.mjs', '.cjs',
                            '.c', '.h', '.cc', '.cpp', '.hpp', '.rs', '.swift', '.html'}):
        return True
    if role == 'board':
        return (parts[0] == 'design' and len(parts) > 1 and
                (path.name in {'sym-lib-table', 'fp-lib-table'} or
                 path.suffix in {'.kicad_pro', '.kicad_sch', '.kicad_pcb', '.kicad_sym',
                                 '.kicad_mod', '.kicad_wks'}))
    if role == 'firmware':
        return (parts[0] == 'firmware' and len(parts) > 1 and
                (path.name in {'Makefile', 'CMakeLists.txt', 'platformio.ini'} or
                 path.suffix in {'.c', '.h', '.cc', '.cpp', '.hpp', '.rs', '.ino', '.py',
                                 '.S', '.s', '.asm', '.ld', '.cmake'}))
    if role == 'case':
        return parts[0] == 'case' and len(parts) > 1 and path.suffix in {'.py', '.scad', '.cq'}
    return False


def _git_metadata_sha(workspace):
    gitdir = workspace / '.git'
    if gitdir.is_symlink():
        raise ValueError('Integration Git directory is a symlink')
    result = {}
    for name in ('config', 'info/exclude', 'info/attributes'):
        path = gitdir / name
        if path.is_symlink():
            raise ValueError('Shared Git metadata is a symlink')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return result


def _prior_draft(root, state, role, stage, current):
    """Read only source drafts from the immediately preceding sealed revision."""
    prior = state['revision'] - 1
    if prior < 1 or role == 'solo':
        return []
    record = root / f'.pilot/rounds/{prior}/{stage}-draft-base.json'
    old = root / 'workers' / f'r{prior:03}-{stage}'
    if not record.is_file() or not old.is_dir():
        return []
    try:
        base = json.loads(record.read_text())
        contracts = list((root / '.pilot/contracts').glob(f'r{prior:03}-*.json'))
        if len(contracts) != 1:
            return []
        contract = json.loads(contracts[0].read_text())
        if (base.get('revision') != prior or base.get('stage') != stage or
                base.get('role') != role or base.get('contractSha256') != digest(contract) or
                contract.get('revision') != prior or
                contract.get('promptSha256') != state['promptSha256'] or
                contract.get('instructionsSha256') != state['instructionsSha256'] or
                base.get('promptSha256') != state['promptSha256'] or
                base.get('instructionsSha256') != state['instructionsSha256'] or
                base.get('pipelineSha256') != state['pipelineSha256'] or
                base.get('gitMetadataSha256') != _git_metadata_sha(root / 'workspace') or
                not isinstance(base.get('baseline'), dict) or
                not isinstance(base.get('baseHead'), str) or
                not (old / '.git').is_file() or (old / '.git').is_symlink() or
                hashlib.sha256((old / '.git').read_bytes()).hexdigest() != base.get('pointerSha256') or
                git(old, 'rev-parse', 'HEAD') != base['baseHead'] or
                git(old, 'rev-parse', '--show-toplevel') != str(old.resolve()) or
                git(old, 'diff', '--cached', '--name-only') or
                digest(json.loads(git(old, 'show', f"{base['baseHead']}:PILOT_CONTRACT.json"))) !=
                base['contractSha256']):
            return []
        after = snapshot(old)  # bounds and symlink rejection apply to old drafts too
        members = changed(base['baseline'], after)
        if any(not owns(role, name) for name in members):
            return []
        draft = []
        for name in members:
            if name not in after or not _draft_source(role, name):
                continue
            # An integrated edit since the old dispatch takes precedence over an old draft.
            if current.get(name) != base['baseline'].get(name):
                continue
            source = old / name
            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != after[name]['sha256']:
                return []
            draft.append((name, data, after[name]))
        return draft
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return []


def _carried_run_draft(root, state, role, stage, current):
    """Resume only owned source from the immediately preceding team run."""
    run = state.get('draftOriginRun')
    if (state.get('mode') != 'teams' or not isinstance(run, str) or
            not run.startswith('r') or not run[1:].isdigit() or run == root.name):
        return []
    source = root.parent / run
    if source.parent != root.parent or not source.is_dir() or source.is_symlink():
        return []
    try:
        previous = json.loads((source / '.pilot/state.json').read_text())
        if previous.get('contractSha256') != state['contractSha256'] or previous.get('revision') != 1:
            return []
        records = sorted((source / '.pilot/rounds/1').glob(f'{stage}*-draft-base.json'),
                         key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for record in records:
            base = json.loads(record.read_text())
            label = record.name.removesuffix('-draft-base.json')
            old = source / 'workers' / f'r001-{label}'
            if (base.get('revision') != 1 or base.get('stage') != stage or
                    base.get('role') != role or
                    base.get('contractSha256') != state['contractSha256'] or
                    not old.is_dir() or old.is_symlink() or not (old / '.git').is_file() or
                    (old / '.git').is_symlink() or
                    hashlib.sha256((old / '.git').read_bytes()).hexdigest() !=
                    base.get('pointerSha256') or
                    git(old, 'rev-parse', 'HEAD') != base.get('baseHead') or
                    git(old, 'diff', '--cached', '--name-only') or
                    digest(json.loads(git(old, 'show', f"{base['baseHead']}:PILOT_CONTRACT.json"))) !=
                    state['contractSha256']):
                continue
            after = snapshot(old)
            edits = changed(base['baseline'], after)
            if any(not owns(role, name) for name in edits):
                continue
            draft = []
            for name in edits:
                if (name not in after or not _draft_source(role, name) or
                        current.get(name) != base['baseline'].get(name)):
                    continue
                data = (old / name).read_bytes()
                if hashlib.sha256(data).hexdigest() != after[name]['sha256']:
                    return []
                draft.append((name, data, after[name]))
            if draft:
                return draft
        return []
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return []


def advise(root, state, stage, worktree, problem, runner, *, stage_deadline=None,
           authorized_integration=None, integration_lock=None):
    """Ask the lead for bounded, read-only guidance on one worker's real files."""
    before = snapshot(worktree)
    before_files = _retry_files(worktree)
    if integration_lock is None:
        git_before = _retry_git_metadata(root, worktree)
    else:
        with integration_lock:
            git_before = _retry_git_metadata(root, worktree)
    prompt = (
        'You are the LEAD acting as an advisor for a scoped worker. Read the CURRENT sealed '
        'PILOT_CONTRACT.json and the actual files in this worktree. Do not edit files, run '
        'hardware, weaken native gates, or silently change the contract. Diagnose the specific '
        'blocker or native failure below. Choose repair when the worker can meet the SAME '
        'contract; give concrete file-cited actions and let that worker continue. Choose '
        'interface_change only when a named field of the shared contract must actually change; '
        'list those fields in changedFields. Choose external_blocker when required external '
        'facts or parts cannot currently be verified. For decisions other than interface_change, '
        'changedFields must be empty. Each action must cite an existing file in this worktree; '
        'for a failed final native check it may instead name the exact missing artifact inside '
        'the responsible worker-owned path. PILOT_CONTRACT.json is available when no '
        'implementation file exists yet. The worker '
        'still must pass native checks and lead review. '
        f'Return contractSha256={state["contractSha256"]}, stage={stage}.\n'
        'PROBLEM:\n' + json.dumps(problem, sort_keys=True))
    receipt = call(root, state, stage + '-advice', worktree, prompt, ADVICE_SCHEMA, True,
                   runner, authorized_integration=authorized_integration,
                   integration_lock=integration_lock, stage_deadline=stage_deadline)
    if receipt['error']:
        if receipt.get('failureKind') == 'interrupted':
            raise JobInterrupted(job_error(receipt))
        if receipt.get('failureKind') == 'timeout':
            raise ExecutionLimitExhausted(job_error(receipt))
        if receipt.get('failureKind') == 'model_capacity':
            raise ModelCapacityExhausted(job_error(receipt))
        raise ValueError(job_error(receipt))
    validate(receipt['result'], ADVICE_SCHEMA)
    advice = receipt['result']
    if advice['contractSha256'] != state['contractSha256'] or advice['stage'] != stage:
        raise ValueError('Lead advice answered for another contract/stage')
    if snapshot(worktree) != before:
        raise ValueError('Read-only lead advice changed worker files')
    if integration_lock is None:
        safe, reason, _ = _retry_safety(root, worktree, stage, True, before_files,
                                        git_before, authorized_integration)
    else:
        with integration_lock:
            safe, reason, _ = _retry_safety(root, worktree, stage, True, before_files,
                                            git_before, authorized_integration)
    if not safe:
        raise ValueError('Read-only lead advice changed Git metadata: ' + str(reason))
    if not advice['actions']:
        raise ValueError('Lead advice needs at least one file-cited action')
    for action in advice['actions']:
        relative = PurePosixPath(action['path'])
        path = (worktree / action['path']).resolve()
        if (relative.is_absolute() or '..' in relative.parts or
                not path.is_relative_to(worktree.resolve())):
            raise ValueError('Lead advice action path escapes worktree')
        missing_final_artifact = (problem.get('kind') == 'native_final_check' and
                                  not path.exists() and
                                  sum(owns(role, action['path']) for role in ROLES) == 1)
        if not path.is_file() and not missing_final_artifact:
            raise ValueError('Lead advice must cite an existing worker file')
    if advice['decision'] == 'interface_change':
        fields = SHARED_INTERFACE_FIELDS
        if not advice['changedFields'] or any(field.split('.', 1)[0] not in fields
                                              for field in advice['changedFields']):
            raise ValueError('Contract revision needs named shared interface fields')
        interfaces = json.loads((root / state['contractFile']).read_text())['interfaces']
        for field in advice['changedFields']:
            _contract_field(interfaces, field)
    elif advice['changedFields']:
        raise ValueError('Repair/external advice cannot change contract fields')
    load(root)
    return advice


def team_test(root, state, role, stage, worktree, checks, runner, *,
              stage_deadline=None, authorized_integration=None, integration_lock=None):
    """Give each builder an independent, read-only tester in its own team."""
    from .parallel_addendum import notes_for_role

    before = snapshot(worktree)
    before_files = _retry_files(worktree)
    if integration_lock is None:
        git_before = _retry_git_metadata(root, worktree)
    else:
        with integration_lock:
            git_before = _retry_git_metadata(root, worktree)
    prompt = (
        f'You are the independent TESTER for the {role} team. Read the actual role files, '
        'the original contract and the applicable numbered release notes. Newer release '
        'notes supersede the base only for their named fields. Do not edit files, approve '
        'missing outputs, claim physical tests or weaken native gates. Inspect concrete '
        'engineering evidence and return file-cited defects. For a missing expected output, '
        'cite its intended path inside this role. Approve only when this team task is done '
        'against the current instructions. The integration gate will reconcile other teams. '
        f'Return contractSha256={state["contractSha256"]}.\n'
        f'ROLE: {role}\nSTAGE: {stage}\nNATIVE CHECK: {json.dumps(checks, sort_keys=True)}\n'
        + notes_for_role(root, role)
    )
    # `call` selects the lead model for read-only jobs; the team's tester uses
    # the worker model and the same shared jobs list, independent of lead gates.
    tester_state = {**state, 'leadModel': state['workerModel']}
    receipt = call(root, tester_state, stage + '-test', worktree, prompt,
                   REVIEW_SCHEMA, True, runner,
                   authorized_integration=authorized_integration,
                   integration_lock=integration_lock,
                   stage_deadline=stage_deadline)
    if receipt['error']:
        if receipt.get('failureKind') == 'interrupted':
            raise JobInterrupted(job_error(receipt))
        if receipt.get('failureKind') == 'timeout':
            raise ExecutionLimitExhausted(job_error(receipt))
        if receipt.get('failureKind') == 'model_capacity':
            raise ModelCapacityExhausted(job_error(receipt))
        raise ValueError(job_error(receipt))
    result = receipt['result']
    validate(result, REVIEW_SCHEMA)
    if result['contractSha256'] != state['contractSha256'] or snapshot(worktree) != before:
        raise ValueError('Team tester changed files or reviewed another contract')
    if integration_lock is None:
        safe, reason, _ = _retry_safety(root, worktree, stage + '-test', True,
                                        before_files, git_before, authorized_integration)
    else:
        with integration_lock:
            safe, reason, _ = _retry_safety(root, worktree, stage + '-test', True,
                                            before_files, git_before, authorized_integration)
    if not safe:
        raise ValueError('Team tester changed Git metadata: ' + str(reason))
    if result['decision'] == 'approve' and result['findings']:
        raise ValueError('Team tester approved unresolved findings')
    if result['decision'] == 'revise' and not result['findings']:
        raise ValueError('Team tester rejected without file-cited findings')
    for finding in result['findings']:
        path = PurePosixPath(finding['path'])
        if (path.is_absolute() or '..' in path.parts or not owns(role, str(path)) or
                (worktree / path).is_symlink()):
            raise ValueError('Team tester finding must cite its team-owned path')
    return result


def dispatch(root, state, stages, runner, on_import=None, checker=None,
             stage_deadline=None, worktree_tag=None, guidance=None, protected_roles=(),
             on_progress=None):
    workspace = root / 'workspace'
    base = git(workspace, 'rev-parse', 'HEAD')
    baseline = snapshot(workspace)
    metadata = lambda: {name: (workspace / '.git' / name).read_bytes()
                        if (workspace / '.git' / name).exists() else None
                        for name in ('config', 'info/exclude', 'info/attributes')}
    git_before = metadata()
    jobs = []
    for role, stage in stages:
        if worktree_tag is not None and (not worktree_tag.replace('-', '').isalnum()
                                         or len(worktree_tag) > 64):
            raise ValueError('Invalid repair worktree tag')
        worktree_name = stage + (f'-{worktree_tag}' if worktree_tag else '')
        dest = root / 'workers' / f"r{state['revision']:03}-{worktree_name}"
        dest.parent.mkdir(exist_ok=True)
        if dest.exists():
            raise ValueError('Stage already attempted; revise the contract for a new bounded round')
        git(workspace, 'worktree', 'add', '--detach', str(dest), base)
        (dest / '.circuit').mkdir(exist_ok=True)
        pointer = (dest / '.git').read_bytes()
        write(root / f".pilot/rounds/{state['revision']}/{worktree_name}-draft-base.json", {
            'revision': state['revision'], 'stage': stage, 'role': role,
            'baseHead': base, 'baseline': baseline,
            'pointerSha256': hashlib.sha256(pointer).hexdigest(),
            'contractSha256': state['contractSha256'],
            'promptSha256': state['promptSha256'],
            'instructionsSha256': state['instructionsSha256'],
            'pipelineSha256': state['pipelineSha256'],
            'gitMetadataSha256': _git_metadata_sha(workspace),
        })
        drafts = _prior_draft(root, state, role, stage, baseline)
        if not drafts:
            drafts = _carried_run_draft(root, state, role, stage, baseline)
        for draft_name, data, info in drafts:
            target = dest / draft_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if info['executable'] else 0o644)
        provenance = {'revision': state['revision'], 'stage': stage,
                      'contractSha256': state['contractSha256'],
                      'priorRevision': state['revision'] - 1,
                      'originRun': state.get('draftOriginRun') if drafts else None,
                      'sources': [{'path': name, 'sha256': info['sha256']}
                                  for name, _, info in drafts]}
        write(root / f".pilot/drafts/r{state['revision']:03}-{worktree_name}.json", provenance)
        jobs.append((role, stage, dest, pointer, provenance))
    integration_lock = threading.Lock()
    authorized_integration = [_integration_git_state(root)]

    def one(job):
        role, stage, dest, pointer, provenance = job
        prompt = worker_prompt(state, role, stage)
        if state['mode'] == 'teams':
            from .parallel_addendum import notes_for_role
            prompt += '\n\n' + notes_for_role(root, role)
        if guidance is not None:
            prompt += ('\n\nLEAD REVIEW FINDINGS FOR SAME SEALED CONTRACT:\n' +
                       json.dumps(guidance, sort_keys=True) + '\nRepair the cited files in this '
                       'new worktree from the latest integrated checkpoint. Preserve the shared '
                       'interface and run native checks before reporting completion.')
        if provenance['sources']:
            origin = (f"previous team run {provenance['originRun']}" if provenance['originRun'] else
                      f"revision {provenance['priorRevision']}")
            contract_instruction = (
                'the original sealed contract, applicable release notes, actual board when available, '
                'and current task' if state['mode'] == 'teams' else
                'the CURRENT sealed contract and actual integration files')
            prompt += ('\nThe following source files are untrusted drafts copied from ' + origin +
                       f": {json.dumps(provenance['sources'])}. "
                       f'Reconcile every draft with {contract_instruction}. '
                       'Rebuild and recheck; do not treat prior outputs or verdicts as accepted.')
        budget = (max(state['timeoutSeconds'], BOARD_STAGE_WALL_SECONDS)
                  if stage == 'board' else state['timeoutSeconds'] + TIMEOUT_CONTINUATION_SECONDS)
        deadline = min(_stage_clock() + budget, stage_deadline) if stage_deadline else _stage_clock() + budget
        advice_rounds = 0
        timeout_advised = False

        def publish(status, findings=()):
            if on_progress is not None and state['mode'] == 'teams':
                # Preview publication is informational. Unsafe or incomplete
                # copies are rejected in the bridge, never imported as output.
                try:
                    normalized = [str(item.get('message', item) if isinstance(item, dict)
                                      else item)[:500] for item in findings][:20]
                    on_progress(role, stage, dest, status, normalized)
                except (OSError, ValueError):
                    pass

        def coach_timeout(receipt):
            nonlocal timeout_advised
            if timeout_advised:
                return None
            advice = advise(root, state, stage, dest,
                            {'kind': 'progressing_timeout',
                             'attempt': receipt['attempt'],
                             'seconds': receipt['seconds'],
                             'retryReason': receipt.get('retryReason')},
                            runner, stage_deadline=min(deadline,
                                                       _stage_clock() + TIMEOUT_ADVICE_SECONDS),
                            authorized_integration=authorized_integration,
                            integration_lock=integration_lock)
            timeout_advised = True
            with ACTIVE_LOCK:
                state['advisorCalls'] = state.get('advisorCalls', 0) + 1
            if advice['decision'] == 'interface_change':
                raise ContractRevisionRequired(advice['summary'] + ': ' +
                                               ', '.join(advice['changedFields']),
                                               advice['changedFields'])
            if advice['decision'] == 'external_blocker':
                raise ExternalBlocker(advice['summary'])
            return advice

        try:
            publish('queued')
            while True:
                with integration_lock:
                    initial_files = _retry_files(dest)
                    initial_git = _retry_git_metadata(root, dest)
                poll_done = threading.Event()
                watcher = None
                if state['mode'] == 'teams' and on_progress is not None:
                    def watch():
                        while not poll_done.wait(10):
                            publish('draft')
                    watcher = threading.Thread(target=watch, daemon=True)
                    watcher.start()
                try:
                    receipt = call(root, state, stage, dest, prompt, RESULT_SCHEMA, False, runner,
                                   authorized_integration=authorized_integration,
                                   integration_lock=integration_lock, stage_deadline=deadline,
                                   on_timeout=coach_timeout if stage == 'board' and
                                   state['mode'] != 'teams' else None)
                finally:
                    poll_done.set()
                    if watcher is not None:
                        watcher.join(timeout=1)
                if receipt['error']:
                    publish('blocked', [job_error(receipt)])
                    return role, stage, dest, pointer, receipt, None, None
                validate(receipt['result'], RESULT_SCHEMA)
                result = receipt['result']
                if result['contractSha256'] != state['contractSha256'] or result['stage'] != stage:
                    raise ValueError('Worker answered for another contract/stage')
                if (dest / '.git').read_bytes() != pointer:
                    raise ValueError('Worker changed its Git worktree pointer')
                if git(dest, 'rev-parse', 'HEAD') != base:
                    raise ValueError('Worker committed or moved HEAD')
                with integration_lock:
                    safe, reason, _ = _retry_safety(root, dest, stage, False, initial_files,
                                                    initial_git, authorized_integration)
                if not safe:
                    raise ValueError('Unsafe or unowned worker output: ' + str(reason))
                after_worker = snapshot(dest)
                if any(not owns(role, name) for name in changed(baseline, after_worker)):
                    raise ValueError('Worker changed a contract/shared/unowned file')
                if any(owns(protected, name) for protected in protected_roles
                       for name in changed(baseline, after_worker)):
                    raise ValueError('Late repair changed a protected board artifact')
                publish('draft', result['blockers'])
                problem = None
                checks = None
                if result['blockers']:
                    problem = {'kind': 'worker_blockers', 'blockers': result['blockers']}
                elif checker is not None and role in ('board', 'solo'):
                    publish('checking')
                    before_check = after_worker
                    with integration_lock:
                        check_files = _retry_files(dest)
                        check_git = _retry_git_metadata(root, dest)
                    checks = checker(dest, False)
                    after_check = snapshot(dest)
                    if any(not owns(role, name) for name in changed(before_check, after_check)):
                        raise ValueError('Native board check wrote outside board-owned artifacts')
                    with integration_lock:
                        safe, reason, _ = _retry_safety(root, dest, stage, False, check_files,
                                                        check_git, authorized_integration)
                    if not safe:
                        raise ValueError('Native board check changed Git metadata: ' + str(reason))
                    if not checks.get('passed'):
                        problem = {'kind': 'native_board_check', 'checks': checks}
                if state['mode'] == 'teams':
                    publish('checking', problem.get('blockers', ()) if problem else ())
                    test = team_test(root, state, role, stage, dest, checks or problem or {}, runner,
                                     authorized_integration=authorized_integration,
                                     integration_lock=integration_lock)
                    if test['decision'] == 'revise' and problem is None:
                        problem = {'kind': 'team_tester', 'review': test}
                    if problem is None:
                        publish('passed')
                        return role, stage, dest, pointer, receipt, checks, None
                    if advice_rounds >= MAX_TEAM_REPAIR_ROUNDS:
                        publish('needs_revision', [str(problem)])
                        raise ValueError('Team task remains open after bounded continuation: ' +
                                         json.dumps(problem, sort_keys=True))
                    advice_rounds += 1
                    publish('needs_revision', test['findings'] if test['findings'] else
                            [str(problem)])
                    prompt = (worker_prompt(state, role, stage) + '\n\n' +
                              notes_for_role(root, role) + '\n\nINDEPENDENT TESTER FINDINGS:\n' +
                              json.dumps(test, sort_keys=True) + '\nPREVIOUS PROBLEM:\n' +
                              json.dumps(problem, sort_keys=True) +
                              '\nContinue in this same worktree. Repair actual files and rerun '
                              'native checks. Preserve the original contract and release notes.')
                    continue
                if problem is None:
                    return role, stage, dest, pointer, receipt, checks, None
                if advice_rounds >= MAX_ADVICE_ROUNDS:
                    label = ('Native board check failed' if problem['kind'] == 'native_board_check'
                             else 'Worker blocker remained')
                    raise ValueError(f'{label} after {MAX_ADVICE_ROUNDS} lead advice rounds: ' +
                                     json.dumps(problem, sort_keys=True))
                advice = advise(root, state, stage, dest, problem, runner,
                                stage_deadline=deadline,
                                authorized_integration=authorized_integration,
                                integration_lock=integration_lock)
                advice_rounds += 1
                with ACTIVE_LOCK:
                    state['advisorCalls'] = state.get('advisorCalls', 0) + 1
                if advice['decision'] == 'interface_change':
                    raise ContractRevisionRequired(advice['summary'] + ': ' +
                                                   ', '.join(advice['changedFields']),
                                                   advice['changedFields'])
                if advice['decision'] == 'external_blocker':
                    raise ExternalBlocker(advice['summary'])
                prompt = (worker_prompt(state, role, stage) + '\n\nLEAD ADVICE FOR SAME SEALED CONTRACT:\n' +
                          json.dumps(advice, sort_keys=True) + '\nPREVIOUS PROBLEM:\n' +
                          json.dumps(problem, sort_keys=True) + '\nContinue in this SAME worktree. '
                          'Fix the cited issues, run the relevant native checks and report any remaining blocker. '
                          'Do not change the sealed contract.')
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return role, stage, dest, pointer, None, None, exc
    failures, capacity_failures, timeout_failures, interrupted_failures = [], [], [], []
    revision_failures, revision_fields, external_failures = [], [], []
    expected, expected_head = baseline, base

    def integrate_valid(stage, dest, names, after):
        nonlocal expected, expected_head
        # Serialize a host commit with sibling retry checks. A retry may accept
        # only an integration Git state the controller recorded after importing
        # a valid role; arbitrary worker changes remain unsafe.
        with integration_lock:
            load(root)  # reject changed frozen inputs before importing any worker output
            if snapshot(workspace) != expected or git(workspace, 'rev-parse', 'HEAD') != expected_head:
                raise ValueError('Integration workspace changed while workers ran')
            if metadata() != git_before or git(workspace, 'diff', '--cached', '--name-only'):
                raise ValueError('Worker changed shared Git metadata or the integration index')
            payloads = {}
            for name in names:
                data = (dest / name).read_bytes() if name in after else None
                if data is not None and hashlib.sha256(data).hexdigest() != after[name]['sha256']:
                    raise ValueError('Worker output changed after validation')
                payloads[name] = (data, (dest / name).stat().st_mtime_ns if data is not None else None)
            for name in names:
                target = workspace / name
                if name in after:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data, mtime = payloads[name]
                    target.write_bytes(data)
                    target.chmod(0o755 if after[name]['executable'] else 0o644)
                    # Firmware freshness uses source/binary mtimes; importing retains build order.
                    os.utime(target, ns=(mtime, mtime))
                else:
                    target.unlink()
            commit(workspace, names, 'Integrate ' + stage)
            expected, expected_head = snapshot(workspace), git(workspace, 'rev-parse', 'HEAD')
            authorized_integration.append(_integration_git_state(root))

    stage_checks = {}

    def accept(result):
        nonlocal expected, expected_head
        role, stage, dest, pointer, receipt, checks, worker_error = result
        if worker_error is not None:
            if isinstance(worker_error, ContractRevisionRequired):
                revision_failures.append(f'{stage}: {worker_error}')
                revision_fields.extend(worker_error.fields)
            elif isinstance(worker_error, ExternalBlocker):
                external_failures.append(f'{stage}: {worker_error}')
            elif isinstance(worker_error, JobInterrupted):
                interrupted_failures.append(stage)
                failures.append(f'{stage}: {worker_error}')
            elif isinstance(worker_error, ModelCapacityExhausted):
                capacity_failures.append(stage)
                failures.append(f'{stage}: {worker_error}')
            elif isinstance(worker_error, ExecutionLimitExhausted):
                timeout_failures.append(stage)
                failures.append(f'{stage}: {worker_error}')
            else:
                failures.append(f'{stage}: {worker_error}')
            return
        try:
            if receipt['error']:
                if receipt.get('failureKind') == 'interrupted':
                    interrupted_failures.append(stage)
                if receipt.get('failureKind') == 'timeout':
                    timeout_failures.append(stage)
                if receipt.get('failureKind') == 'model_capacity':
                    capacity_failures.append(stage)
                raise ValueError(job_error(receipt))
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
            if any(owns(protected, n) for protected in protected_roles for n in names):
                raise ValueError('Late repair changed a protected board artifact')
        except (OSError, ValueError) as exc:
            failures.append(f'{stage}: {exc}')
            return
        # Import each completed, valid role immediately. Siblings still work from the
        # same checked base; disjoint ownership makes their later imports independent.
        integrate_valid(stage, dest, names, after)
        if checks is not None:
            stage_checks[stage] = checks
        if on_import is not None:
            if state['mode'] == 'teams' and role == 'board':
                with integration_lock:
                    on_import(workspace, state, stage)
                    after_callback = snapshot(workspace)
                    callback_head = git(workspace, 'rev-parse', 'HEAD')
                    if any(not owns('board', name) for name in changed(expected, after_callback)):
                        raise ValueError('Board checkpoint changed another team artifact')
                    if metadata() != git_before or git(workspace, 'diff', '--cached', '--name-only'):
                        raise ValueError('Board checkpoint changed shared Git metadata or index')
                    expected, expected_head = after_callback, callback_head
                    authorized_integration.append(_integration_git_state(root))
            else:
                on_import(workspace, state, stage)
                if snapshot(workspace) != expected or git(workspace, 'rev-parse', 'HEAD') != expected_head:
                    raise ValueError('Checkpoint changed the integrated workspace')

    if state['mode'] == 'sequential' or len(jobs) == 1:
        for job in jobs:
            accept(one(job))
    else:
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(one, job) for job in jobs]
            try:
                for future in as_completed(futures):
                    accept(future.result())
            except BaseException:
                cancel_jobs(root)
                raise
    load(root)
    if snapshot(workspace) != expected or git(workspace, 'rev-parse', 'HEAD') != expected_head:
        raise ValueError('Integration workspace changed while workers ran')
    if metadata() != git_before or git(workspace, 'diff', '--cached', '--name-only'):
        raise ValueError('Worker changed shared Git metadata or the integration index')
    if failures:
        if interrupted_failures:
            raise JobInterrupted('\n'.join(failures))
        if capacity_failures:
            raise ModelCapacityExhausted('\n'.join(failures))
        if timeout_failures:
            raise ExecutionLimitExhausted('\n'.join(failures))
        raise ValueError('\n'.join(failures))
    if revision_failures:
        raise ContractRevisionRequired('\n'.join(revision_failures), sorted(set(revision_fields)))
    if external_failures:
        raise ExternalBlocker('\n'.join(external_failures))
    return stage_checks


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


def review(root, state, stage, checks, runner, *, allow_revise=False, stage_deadline=None):
    workspace = root / 'workspace'
    before = snapshot(workspace)
    before_files = _retry_files(workspace)
    git_before = _retry_git_metadata(root, workspace)
    drafts = []
    for path in sorted((root / '.pilot/drafts').glob(f"r{state['revision']:03}-*.json")):
        provenance = json.loads(path.read_text())
        if (provenance.get('revision') != state['revision'] or
                provenance.get('contractSha256') != state['contractSha256']):
            raise ValueError('Draft provenance changed before lead review')
        if provenance.get('sources'):
            drafts.append(provenance)
    focus = {
        'board-review': ('This is the board handoff gate. Inspect the actual native schematic, '
                         'netlist, PCB, published board/STEP, and native check results against the '
                         'sealed contract: outline, mounting coordinates, device placement, power '
                         'and protection, pins, connectors, and the mechanical interface. Find '
                         'missing or ambiguous details that firmware or case would have to guess. '
                         'If the sealed interface itself must change, cite PILOT_CONTRACT.json '
                         'so the controller can request a separate interface decision. '
                         'Approve only when those workers can build against the checked board. '
                         'Do not require firmware or case artifacts that have not been dispatched.'),
        'structure-review': ('Compare firmware definitions and WIRING with the actual netlist. '
                             'Verify the exposed device/frame/joint insertion, retention, tool '
                             'access and travel before covers against the published board/STEP.'),
        'final-review': ('Check the complete case, overview, arranged 3MF and reports against the '
                         'final board/STEP, fasteners and wires. Reconcile firmware behavior, '
                         'definitions and WIRING with the actual netlist and power rails.'),
    }[stage]
    prompt = ('You are the independent LEAD reviewer. Read PILOT_CONTRACT.json and actual artifacts, '
              'not raw worker transcripts. ' + focus + ' Report file-cited engineering defects only. '
              'Do not edit anything, run hardware or weaken gates. A model approval cannot override '
              'a failed native gate. Physical fit/strength remains untested. Approve only if there '
              'are no unresolved concerns; otherwise revise with existing file paths and concrete '
              'findings. Some workers may have started from untrusted source drafts from the prior '
              'revision. Inspect each named current source against the CURRENT sealed contract and '
              'actual checked outputs; prior source hashes are provenance, not validation. '
              f'Return contractSha256={state["contractSha256"]}.\nDRAFT PROVENANCE:\n' +
              json.dumps(drafts, sort_keys=True) + '\nNATIVE CHECKS:\n' + json.dumps(checks))
    job = call(root, state, stage, workspace, prompt, REVIEW_SCHEMA, True, runner,
               stage_deadline=stage_deadline)
    if job['error']:
        if job.get('failureKind') == 'interrupted':
            raise JobInterrupted(job_error(job))
        if job.get('failureKind') == 'timeout':
            raise ExecutionLimitExhausted(job_error(job))
        if job.get('failureKind') == 'model_capacity':
            raise ModelCapacityExhausted(job_error(job))
        raise ValueError(job_error(job))
    validate(job['result'], REVIEW_SCHEMA)
    r = job['result']
    if snapshot(workspace) != before or r['contractSha256'] != state['contractSha256']:
        raise ValueError('Review changed artifacts or used another contract')
    safe, reason, _ = _retry_safety(root, workspace, stage, True, before_files, git_before)
    if not safe:
        raise ValueError('Read-only review changed Git metadata: ' + str(reason))
    for finding in r['findings']:
        p = (workspace / finding['path']).resolve()
        if not p.is_relative_to(workspace.resolve()) or not p.is_file():
            raise ValueError('Review finding must cite an existing workspace file')
    if r['decision'] == 'approve' and r['findings']:
        raise ValueError('Approval with unresolved findings is invalid')
    if r['decision'] == 'revise' and not r['findings']:
        raise ValueError('Review revision needs file-cited findings')
    if r['decision'] != 'approve' and not allow_revise:
        raise ValueError(r['summary'] + ': ' + json.dumps(r['findings']))
    return r


def repair_stages(state, cited, *, final):
    """Route file-cited late findings only to unambiguous downstream owners."""
    roles = set()
    for finding in cited:
        owners = [role for role in ROLES if owns(role, finding['path'])]
        if len(owners) != 1:
            raise ValueError('Lead repair finding has no unambiguous worker owner: ' + finding['path'])
        if owners[0] == 'board':
            raise ValueError('Late board finding requires a new checked board and dependent rebuild: ' +
                             finding['path'])
        roles.add(owners[0])
    if not roles:
        raise ValueError('Lead repair needs file-cited worker actions')
    if state['mode'] == 'solo':
        return [('solo', 'solo-final' if final else 'solo')]
    return [(role, role + '-final' if final else role) for role in ('firmware', 'case')
            if role in roles]


def team_integration_test(root, state, checks, runner):
    """Read-only cross-team tester; the lead is free to handle user changes."""
    from .parallel_addendum import notes_for_role

    workspace = root / 'workspace'
    before = snapshot(workspace)
    before_files = _retry_files(workspace)
    git_before = _retry_git_metadata(root, workspace)
    notes = '\n\n'.join(notes_for_role(root, role) for role in ROLES)
    prompt = (
        'You are the independent INTEGRATION TESTER for the board, firmware and case teams. '
        'Read their actual integrated files, native board/netlist/geometry, original contract '
        'and ordered release notes. Do not edit anything. Check pin and power compatibility, '
        'mechanical interfaces, firmware build/wiring, printable case and native checks. '
        'A native failure cannot be overruled by a model approval. For each defect cite a '
        'specific role-owned file or the intended missing path. Cite PILOT_CONTRACT.json only '
        'when the original interface cannot be satisfied without a new user instruction. '
        f'Return contractSha256={state["contractSha256"]}.\n'
        f'NATIVE CHECKS: {json.dumps(checks, sort_keys=True)}\n{notes}'
    )
    tester_state = {**state, 'leadModel': state['workerModel']}
    receipt = call(root, tester_state, 'integration-test', workspace, prompt,
                   REVIEW_SCHEMA, True, runner)
    if receipt['error']:
        if receipt.get('failureKind') == 'interrupted':
            raise JobInterrupted(job_error(receipt))
        if receipt.get('failureKind') == 'timeout':
            raise ExecutionLimitExhausted(job_error(receipt))
        if receipt.get('failureKind') == 'model_capacity':
            raise ModelCapacityExhausted(job_error(receipt))
        raise ValueError(job_error(receipt))
    result = receipt['result']
    validate(result, REVIEW_SCHEMA)
    if result['contractSha256'] != state['contractSha256'] or snapshot(workspace) != before:
        raise ValueError('Integration tester changed files or reviewed another contract')
    safe, reason, _ = _retry_safety(root, workspace, 'integration-test', True,
                                    before_files, git_before)
    if not safe:
        raise ValueError('Integration tester changed Git metadata: ' + str(reason))
    if result['decision'] == 'approve' and result['findings']:
        raise ValueError('Integration tester approved unresolved findings')
    if result['decision'] == 'revise' and not result['findings']:
        raise ValueError('Integration tester rejected without cited findings')
    for finding in result['findings']:
        path = PurePosixPath(finding['path'])
        if (path.is_absolute() or '..' in path.parts or
                (not owns('solo', str(path)) and str(path) != 'PILOT_CONTRACT.json') or
                (workspace / path).is_symlink()):
            raise ValueError('Integration finding needs a team-owned path')
    return result


def native_failure_roles(checks):
    """Route known native gate findings without replaying unrelated teams."""
    roles = set()
    for finding in checks.get('findings', []):
        message = str(finding).casefold()
        if 'firmware' in message or 'wiring' in message:
            roles.add('firmware')
        elif 'case' in message or 'print project' in message or 'slicer' in message:
            roles.add('case')
        else:
            roles.add('board')
    return roles


def run_teams(directory, *, runner=codex, checker=native_check, on_checkpoint=None,
              on_progress=None):
    """Run independent builder/tester teams, then reconcile their interfaces."""
    from .parallel_addendum import _addendum_lock, load_addenda

    root, state = load(directory)
    with locked(root):
        root, state = load(root)
        if state['status'] != 'planned' or state['mode'] != 'teams':
            raise ValueError('A sealed team contract must be planned before run')
        workspace = root / 'workspace'
        assigned = state.get('assignedRoles', list(ROLES))
        if not isinstance(assigned, list) or not assigned or any(role not in ROLES for role in assigned):
            raise ValueError('Invalid lead team assignment')
        tip = load_addenda(root, state['contractSha256'])['tipSha256']
        state['status'], state['roundStartedAt'] = 'running', time.time()
        state['releaseNoteTipSha256'] = tip
        state['replanRequired'] = False
        write(root / '.pilot/state.json', state)

        def publish_native(complete):
            before = snapshot(workspace)
            head = git(workspace, 'rev-parse', 'HEAD')
            verdict = checker(workspace, complete)
            names = changed(before, snapshot(workspace))
            allowed = 'solo' if complete else 'board'
            if any(not owns(allowed, name) for name in names):
                raise ValueError('Native checker wrote outside owned artifacts')
            load(root)
            if git(workspace, 'rev-parse', 'HEAD') != head or git(workspace, 'diff', '--cached', '--name-only'):
                raise ValueError('Native checker changed integrated Git metadata')
            commit(workspace, names, 'Record team native checks' if complete else
                   'Publish checked team board')
            stage = 'final' if complete else 'board'
            write(root / f".pilot/rounds/{state['revision']}/{stage}-check.json", verdict)
            if on_checkpoint is not None:
                on_checkpoint(workspace, state, stage)
            return verdict

        try:
            stages = [(role, role) for role in ROLES if role in assigned]
            board_checks = None
            if 'board' not in assigned:
                board_checks = publish_native(False)
                if not board_checks.get('passed'):
                    raise ValueError('Seeded board failed native check: ' +
                                     json.dumps(board_checks.get('findings', [])))

            def accepted(_source, _current, stage):
                nonlocal board_checks
                if stage == 'board':
                    board_checks = publish_native(False)
                    if not board_checks.get('passed'):
                        raise ValueError('Integrated board native check failed: ' +
                                         json.dumps(board_checks.get('findings', [])))
                elif board_checks is not None and on_checkpoint is not None:
                    on_checkpoint(workspace, state, stage)

            dispatch(root, state, stages, runner, checker=checker,
                     on_progress=on_progress, on_import=accepted)
            if board_checks is None:
                raise ValueError('Board team produced no checked native handoff')
            checks = publish_native(True)
            integration = team_integration_test(root, state, checks, runner)
            for repair_round in range(1, MAX_TEAM_INTEGRATION_ROUNDS + 1):
                if checks.get('passed') and integration['decision'] == 'approve':
                    break
                findings = integration['findings']
                if any(item['path'] == 'PILOT_CONTRACT.json' for item in findings):
                    raise ValueError('The teams found an unresolved base interface conflict: ' +
                                     json.dumps(findings, sort_keys=True))
                roles = {role for item in findings for role in ROLES
                         if owns(role, item['path'])}
                if not checks.get('passed'):
                    roles.update(native_failure_roles(checks))
                    if not roles:
                        roles = set(ROLES)
                if not roles:
                    raise ValueError('Integration tester did not identify a repair owner')
                dispatch(root, state, [(role, role) for role in ROLES if role in roles],
                         runner, checker=checker, on_progress=on_progress,
                         worktree_tag=f'integration-{repair_round}',
                         guidance={'native': checks, 'integration': integration})
                if 'board' in roles:
                    board_checks = publish_native(False)
                    if not board_checks.get('passed'):
                        raise ValueError('Repaired board failed integrated native check: ' +
                                         json.dumps(board_checks.get('findings', [])))
                checks = publish_native(True)
                integration = team_integration_test(root, state, checks, runner)
            if not checks.get('passed') or integration['decision'] != 'approve':
                raise ValueError('Cross-team integration remains open after bounded repairs: ' +
                                 json.dumps({'native': checks, 'review': integration}, sort_keys=True))
            with _addendum_lock(root):
                if load_addenda(root, state['contractSha256'])['tipSha256'] != tip:
                    raise ValueError('A newer lead release note arrived during this run; resume affected teams')
                load(root)
                state['artifactManifest'] = snapshot(workspace)
                state['status'], state['findings'] = 'candidate_ready', []
                # Publish terminal state while the lead's append lock is held.
                # A concurrent note either arrives first and invalidates this
                # run, or sees candidate_ready and is sent to the next run.
                write(root / '.pilot/state.json', state)
        except JobInterrupted as exc:
            state['status'], state['findings'] = 'interrupted', [str(exc)]
        except ExecutionLimitExhausted as exc:
            state['status'], state['findings'] = 'execution_limit', [str(exc)]
        except ModelCapacityExhausted as exc:
            state['status'], state['findings'] = 'model_unavailable', [str(exc)]
        except Exception as exc:
            state['status'], state['findings'] = 'needs_revision', [f'{type(exc).__name__}: {exc}']
        except KeyboardInterrupt:
            state['status'], state['findings'] = 'interrupted', ['Operator interrupted the teams']
        finally:
            state['roundFinishedAt'] = time.time()
            state['rounds'].append({'revision': state['revision'], 'status': state['status'],
                                    'seconds': state['roundFinishedAt'] - state['roundStartedAt']})
            write(root / '.pilot/state.json', state)
            write(root / '.pilot/metrics.json', metrics(state))
        return state


def run(directory, *, execute=False, runner=codex, checker=native_check, on_checkpoint=None,
        on_progress=None):
    if not execute:
        raise ValueError('Paid/model execution requires explicit --execute')
    root, state = load(directory)
    if state['mode'] == 'teams':
        return run_teams(root, runner=runner, checker=checker,
                         on_checkpoint=on_checkpoint, on_progress=on_progress)
    with locked(root):
        root, state = load(root)
        if state['status'] != 'planned':
            raise ValueError('Plan first; a failed/completed round needs plan --revise before retry')
        workspace = root / 'workspace'
        state['status'], state['roundStartedAt'] = 'running', time.time()
        state['replanRequired'] = False
        write(root / '.pilot/state.json', state)
        try:
            def publish_checked_board():
                # Private preflight already passed. Re-publish on the integration
                # branch so the viewer gets the checker's ignored native verdict.
                before = snapshot(workspace)
                board_head = git(workspace, 'rev-parse', 'HEAD')
                verdict = checker(workspace, False)
                names = changed(before, snapshot(workspace))
                if any(not owns(board_stage, name) for name in names):
                    raise ValueError('Integrated native board check wrote outside board-owned artifacts')
                load(root)
                if git(workspace, 'rev-parse', 'HEAD') != board_head:
                    raise ValueError('Integrated native board check moved HEAD')
                if git(workspace, 'diff', '--cached', '--name-only'):
                    raise ValueError('Integrated native board check staged Git changes')
                commit(workspace, names, 'Record native board publication')
                if git(workspace, 'rev-parse', 'HEAD') == board_head:
                    git(workspace, '-c', 'user.name=KiCad Pilot', '-c', 'user.email=pilot@localhost',
                        'commit', '--allow-empty', '-qm', 'Record native board publication')
                write(root / f".pilot/rounds/{state['revision']}/board-check.json", verdict)
                if on_checkpoint is not None:
                    on_checkpoint(workspace, state, 'board')
                if not verdict.get('passed'):
                    raise ValueError('Native board check failed after private preflight: ' +
                                     str(verdict.get('findings')))
                return verdict

            def publish_final_checks():
                before = snapshot(workspace)
                final_head = git(workspace, 'rev-parse', 'HEAD')
                verdict = checker(workspace, True)
                names = changed(before, snapshot(workspace))
                if any(not owns('solo', name) for name in names):
                    raise ValueError('Native final check wrote outside owned artifacts')
                load(root)
                if git(workspace, 'rev-parse', 'HEAD') != final_head:
                    raise ValueError('Native final check moved HEAD')
                if git(workspace, 'diff', '--cached', '--name-only'):
                    raise ValueError('Native final check staged Git changes')
                commit(workspace, names, 'Record final native checks')
                write(root / f".pilot/rounds/{state['revision']}/final-check.json", verdict)
                if on_checkpoint is not None:
                    on_checkpoint(workspace, state, 'final')
                return verdict

            # Board outputs are the concrete input to both other roles. Import and check
            # them first so firmware and case never build against an imaginary placement.
            board_stage = 'solo' if state['mode'] == 'solo' else 'board'
            board_checks = dispatch(root, state, [(board_stage, board_stage)], runner,
                                    checker=checker)
            if not board_checks[board_stage].get('passed'):
                raise ValueError('Private native board check did not pass')
            checks = publish_checked_board()
            board_review = review(root, state, 'board-review', checks, runner,
                                  allow_revise=True)
            review_deadline = None
            for repair_round in range(1, MAX_ADVICE_ROUNDS + 1):
                if board_review['decision'] == 'approve':
                    break
                if review_deadline is None:
                    review_deadline = (_stage_clock() + state['timeoutSeconds'] +
                                       TIMEOUT_CONTINUATION_SECONDS)
                guidance = board_review
                if any(item['path'] == 'PILOT_CONTRACT.json' for item in board_review['findings']):
                    advice = advise(root, state, board_stage, workspace,
                                    {'kind': 'lead_board_review', 'review': board_review},
                                    runner, stage_deadline=review_deadline)
                    with ACTIVE_LOCK:
                        state['advisorCalls'] = state.get('advisorCalls', 0) + 1
                    if advice['decision'] == 'interface_change':
                        raise ContractRevisionRequired(advice['summary'] + ': ' +
                                                       ', '.join(advice['changedFields']),
                                                       advice['changedFields'])
                    if advice['decision'] == 'external_blocker':
                        raise ExternalBlocker(advice['summary'])
                    guidance = {'review': board_review, 'advice': advice}
                repaired = dispatch(root, state, [(board_stage, board_stage)], runner,
                                    checker=checker, stage_deadline=review_deadline,
                                    worktree_tag=f'review-repair-{repair_round}',
                                    guidance=guidance)
                if not repaired[board_stage].get('passed'):
                    raise ValueError('Private board repair check did not pass')
                checks = publish_checked_board()
                board_review = review(root, state, 'board-review', checks, runner,
                                      allow_revise=True, stage_deadline=review_deadline)
            if board_review['decision'] != 'approve':
                raise ValueError('Lead board review still rejected after bounded repairs: ' +
                                 json.dumps(board_review['findings']))
            write(root / f".pilot/rounds/{state['revision']}/board-review.json", board_review)
            if on_checkpoint is not None:
                on_checkpoint(workspace, state, 'board-approved')
            if state['mode'] != 'solo':
                # These two roles share one checked board base and have disjoint outputs.
                dispatch(root, state, [('firmware', 'firmware'), ('case', 'case')], runner,
                         on_import=on_checkpoint)
            structure_review = review(root, state, 'structure-review', checks, runner,
                                      allow_revise=True)
            structure_deadline = None
            for repair_round in range(1, MAX_ADVICE_ROUNDS + 1):
                if structure_review['decision'] == 'approve':
                    break
                if structure_deadline is None:
                    structure_deadline = (_stage_clock() + state['timeoutSeconds'] +
                                          TIMEOUT_CONTINUATION_SECONDS)
                roles = repair_stages(state, structure_review['findings'], final=False)
                dispatch(root, state, roles, runner, on_import=on_checkpoint,
                         stage_deadline=structure_deadline,
                         worktree_tag=f'structure-repair-{repair_round}',
                         guidance=structure_review, protected_roles=('board',))
                structure_review = review(root, state, 'structure-review', checks, runner,
                                          allow_revise=True, stage_deadline=structure_deadline)
            if structure_review['decision'] != 'approve':
                raise ValueError('Lead structure review still rejected after bounded repairs: ' +
                                 json.dumps(structure_review['findings']))
            # Both finishers now read the actual integrated PCB/netlist/STEP. Covers come last.
            final = ([('solo', 'solo-final')] if state['mode'] == 'solo' else
                     [('firmware', 'firmware-final'), ('case', 'case-final')])
            dispatch(root, state, final, runner,
                     on_import=on_checkpoint if state['mode'] != 'solo' else None)
            checks = publish_final_checks()
            final_review = review(root, state, 'final-review', checks, runner,
                                  allow_revise=True)
            final_deadline = None
            for repair_round in range(1, MAX_ADVICE_ROUNDS + 1):
                if checks.get('passed') and final_review['decision'] == 'approve':
                    break
                if final_deadline is None:
                    final_deadline = (_stage_clock() + state['timeoutSeconds'] +
                                      TIMEOUT_CONTINUATION_SECONDS)
                if not checks.get('passed'):
                    advice = advise(root, state, 'final', workspace,
                                    {'kind': 'native_final_check', 'checks': checks,
                                     'review': final_review}, runner,
                                    stage_deadline=final_deadline)
                    with ACTIVE_LOCK:
                        state['advisorCalls'] = state.get('advisorCalls', 0) + 1
                    if advice['decision'] == 'interface_change':
                        raise ContractRevisionRequired(advice['summary'] + ': ' +
                                                       ', '.join(advice['changedFields']),
                                                       advice['changedFields'])
                    if advice['decision'] == 'external_blocker':
                        raise ExternalBlocker(advice['summary'])
                    guidance = advice
                    roles = repair_stages(state, advice['actions'], final=True)
                else:
                    guidance = final_review
                    roles = repair_stages(state, final_review['findings'], final=True)
                dispatch(root, state, roles, runner,
                         on_import=on_checkpoint if state['mode'] != 'solo' else None,
                         stage_deadline=final_deadline,
                         worktree_tag=f'final-repair-{repair_round}', guidance=guidance,
                         protected_roles=('board',))
                checks = publish_final_checks()
                final_review = review(root, state, 'final-review', checks, runner,
                                      allow_revise=True, stage_deadline=final_deadline)
            if not checks.get('passed'):
                raise ValueError('Native final check failed after bounded repairs: ' +
                                 str(checks.get('findings')))
            if final_review['decision'] != 'approve':
                raise ValueError('Lead final review still rejected after bounded repairs: ' +
                                 json.dumps(final_review['findings']))
            load(root)  # reject edits to frozen input snapshots and sealed contract
            state['artifactManifest'] = snapshot(workspace)
            state['status'], state['findings'] = 'candidate_ready', []
        except JobInterrupted as exc:
            state['status'], state['findings'] = 'interrupted', [str(exc)]
        except ExecutionLimitExhausted as exc:
            state['status'], state['findings'] = 'execution_limit', [str(exc)]
        except ModelCapacityExhausted as exc:
            state['status'], state['findings'] = 'model_unavailable', [str(exc)]
        except ContractRevisionRequired as exc:
            state['status'], state['findings'] = 'needs_revision', [str(exc)]
            state['replanRequired'] = True
            state['requestedChangedFields'] = list(exc.fields)
        except ExternalBlocker as exc:
            state['status'], state['findings'] = 'needs_revision', [str(exc)]
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
            'advisorCalls': state.get('advisorCalls', 0),
            'wallSeconds': state.get('planningSeconds', 0) + sum(r['seconds'] for r in state['rounds']),
            'elapsedSinceInitSeconds': state.get('roundFinishedAt', time.time()) - state['createdAt'],
            'rounds': state['rounds'], 'planAttempts': state['planAttempts'],
            'planReusedFrom': state.get('planReusedFrom'),
            'agentSeconds': sum(j['seconds'] for j in jobs), 'reportedUsage': totals,
            'usageComplete': bool(jobs) and all(j.get('usage') is not None for j in jobs),
            'leadJobs': [j for j in jobs if j['stage'] in ('plan', 'board-review', 'structure-review',
                                                        'final-review') or j['stage'].endswith('-advice')],
            'workerJobs': [j for j in jobs if j['stage'] not in ('plan', 'board-review',
                                                                'structure-review', 'final-review') and
                           not j['stage'].endswith('-advice')],
            'approvalCalls': 0, 'actualBilledUsd': None, 'estimatedCostUsd': None,
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
    init.add_argument('--mode', choices=['parallel', 'sequential', 'solo', 'teams'], default='parallel')
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
        failed = result.get('status') in ('needs_revision', 'interrupted', 'planning_failed',
                                          'model_unavailable', 'execution_limit', 'stale')
        print(json.dumps({'ok': not failed, **result}))
        return 2 if failed else 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
