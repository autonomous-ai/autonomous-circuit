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
import re
import signal
import subprocess
import tarfile
import threading
import time

from .parallel_contract import (ADVICE_SCHEMA, CONTRACT_SCHEMA, RECHECK_SCHEMA, RESULT_SCHEMA, REVIEW_SCHEMA, ROLES,
                                DEVIATIONS, HANDOFF, OWNED, digest, encoded, owns, validate, validate_contract)

LEAD = 'gpt-6-astra'
WORKER = 'gpt-6.1-sol'
#: One project's lead/tester override, so two runs of one prompt can be compared.
TEAM_MODELS = Path('.circuit') / 'team-models.json'
# Match the existing tile: native KiCad/pcbnew/routing cannot use workspace-write (README).
WORKER_SANDBOX = 'danger-full-access'
SKIP = {'.git', '.circuit', '.harness', '.kicadpy', '__pycache__', 'node_modules'}
MAX_FILE = 128 * 1024 * 1024
# Board review snapshots and slicer/check outputs pushed real case worktrees past 1 GB.
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_FILES = 20000
ACTIVE = {}
ACTIVE_LOCK = threading.Lock()
CAPACITY_RETRY_DELAYS = (5, 15)
MAX_STAGE_ATTEMPTS = 4
TIMEOUT_CONTINUATION_SECONDS = 600
BOARD_STAGE_WALL_SECONDS = 3 * 60 * 60
BOARD_MAX_TIMEOUT_CONTINUATIONS = 6
LONG_STAGES = ('board', 'case')
# Board and case builders reread the whole project after every cut, so give each attempt an hour.
LONG_STAGE_ATTEMPT_SECONDS = 60 * 60
# No new CLI event for this long means the job hung. Measured quiet gaps in crab runs stay under 7 minutes.
STALL_SECONDS = 15 * 60
# One machine-wide firmware toolchain cache; per-worktree PlatformIO/SDK installs filled the disk.
FIRMWARE_CACHE = Path(os.environ.get('CIRCUIT_FIRMWARE_CACHE',
                                     Path.home() / '.cache/autonomous-circuit/firmware'))
FIRMWARE_CACHE_NOTE = (' Reuse the shared toolchain cache: keep the provided PLATFORMIO_CORE_DIR, '
                       'ARDUINO_DIRECTORIES_DATA and PIP_CACHE_DIR; do not install PlatformIO cores, '
                       'SDKs or toolchains inside the worktree.')
MAX_ADVICE_ROUNDS = 2
# Teams loop until the tester approves; stagnation goes to the lead once, these are ceilings.
MAX_TEAM_REPAIR_ROUNDS = 8
TEAM_ROLE_WALL_SECONDS = 6 * 60 * 60
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
            except (ProcessLookupError, PermissionError):  # macOS: EPERM for an exited, unreaped group
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


def _is_browser_profile(workspace, path):
    """A leftover browser may keep writing its build/ profile after its job, so never hash one."""
    return (path.relative_to(workspace).parts[0] == 'build' and not path.is_symlink() and
            (os.path.lexists(path / 'SingletonLock') or os.path.lexists(path / 'Local State')))


def _drop_scratch_links(workspace, directory, dirs, files):
    """Tools (headless Chrome profiles) leave symlinks in build/ scratch; skip those, reject the rest."""
    kept = ([], [])
    for bucket, names in zip(kept, (dirs, files)):
        for name in names:
            path = Path(directory) / name
            if bucket is kept[0] and _is_browser_profile(workspace, path):
                continue
            if name != '.git' and path.is_symlink():
                if path.relative_to(workspace).parts[0] != 'build':
                    raise ValueError('Pilot snapshots do not support symlinks')
                continue
            bucket.append(name)
    return kept


def snapshot(workspace):
    """Hash real outputs, rejecting links and limiting accidental build-directory explosions."""
    result, total = {}, 0
    for directory, dirs, files in os.walk(workspace):
        dirs[:], files[:] = _drop_scratch_links(workspace, directory, dirs, files)
        dirs[:] = [d for d in dirs if d not in SKIP]
        for name in sorted(files):
            if name == '.git':
                continue
            file = Path(directory) / name
            size = file.stat().st_size
            total += size
            if size > MAX_FILE or total > MAX_TOTAL or len(result) >= MAX_FILES:
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
               lead_model=LEAD, worker_model=WORKER, tester_model=None, timeout=1800,
               builder_models=None, tester_models=None, integration_tester_model=None,
               advisor_model=None):
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
                if member.isfile() and (owns('solo', str(name)) or str(name) in ('README.md', 'project.json',
                                                                                 'print/events.jsonl')):
                    total += member.size
                    if member.size > MAX_FILE or total > MAX_TOTAL or len(seed_files) >= MAX_FILES:
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
             'workerModel': worker_model, 'testerModel': tester_model or worker_model,
             'builderModels': dict(builder_models or {}), 'testerModels': dict(tester_models or {}),
             'integrationTesterModel': integration_tester_model, 'advisorModel': advisor_model,
             'timeoutSeconds': timeout, 'revision': 0,
             'workerSandbox': WORKER_SANDBOX,
             'seedCommit': seed_commit, 'baseCommit': base, 'jobs': [], 'createdAt': time.time(),
             'planAttempts': 0, 'rounds': [],
             'instructionsSha256': digest(texts), 'promptSha256': digest(prompt),
             'pipelineSha256': pipeline_sha(tool_root),
             'gitMetadataSha256': digest(_git_metadata_sha(workspace)),
             'toolchainRoot': str(tool_root), 'hardwareTested': False}
    write(control / 'state.json', state)
    return state


def load(root, *, same_pipeline=True):
    """An earlier run read only for its records passes `same_pipeline=False`; it is not resumed."""
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
    if same_pipeline and pipeline_sha(state['toolchainRoot']) != state['pipelineSha256']:
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
        dirs[:], names[:] = _drop_scratch_links(workspace, directory, dirs, names)
        dirs[:] = [name for name in dirs if name not in SKIP]
        for name in names:
            if name == '.git':
                continue
            path = Path(directory) / name
            stat = path.stat()
            if not path.is_file():
                raise ValueError('Retry workspace contains a non-file output')
            total += stat.st_size
            if stat.st_size > MAX_FILE or total > MAX_TOTAL or len(files) >= MAX_FILES:
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


SLICER_RECEIPT = 'result.json'
SLICER_RECEIPT_KEYS = {'error_string', 'return_code'}


def _drop_slicer_receipt(workspace, baseline):
    """Bambu/Orca CLI writes result.json into its cwd; it is a tool receipt, not work."""
    receipt = Path(workspace) / SLICER_RECEIPT
    if SLICER_RECEIPT in baseline or receipt.is_symlink() or not receipt.is_file():
        return
    try:
        data = json.loads(receipt.read_text())
    except (OSError, ValueError):
        return
    if isinstance(data, dict) and SLICER_RECEIPT_KEYS <= data.keys():
        receipt.unlink()


def _retry_safety(root, workspace, stage, readonly, initial_files, initial_git,
                  authorized_integration=None):
    _drop_slicer_receipt(workspace, initial_files)
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


def _reap_worktree_processes(root, worktree):
    """Chrome and BambuStudio detach into their own process group, but their arguments name the worktree.

    Only a job's private worker worktree: the integration workspace and the visible
    project are also named by the controller, the lead tab and the viewer.
    """
    if not Path(worktree).is_relative_to(Path(root) / 'workers'):
        return
    pattern = re.escape(str(worktree)) + r'(/|[[:space:]]|$)'
    found = subprocess.run(['pgrep', '-f', pattern], capture_output=True, text=True).stdout
    for pid in found.split():
        if int(pid) != os.getpid():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def team_models(workspace):
    """Models for one project: `lead`, `tester`, `integrationTester`, `advisor`, and per-role `builders`/`testers`."""
    defaults = {'lead': LEAD, 'tester': WORKER, 'integrationTester': None, 'advisor': None,
                'builders': {}, 'testers': {}}
    path = Path(workspace) / TEAM_MODELS
    if not path.is_file():
        return defaults
    chosen = json.loads(path.read_text())
    model = lambda value: isinstance(value, str) and bool(value.strip())
    by_role = lambda value: isinstance(value, dict) and set(value) <= set(ROLES) and all(map(model, value.values()))
    if (not isinstance(chosen, dict) or set(chosen) - set(defaults) or
            not all(model(chosen[key]) for key in ('lead', 'tester', 'integrationTester', 'advisor')
                    if key in chosen) or
            not all(by_role(chosen[key]) for key in ('builders', 'testers') if key in chosen)):
        raise ValueError(f'{TEAM_MODELS} takes string lead, tester, integrationTester and advisor models, '
                         'and builders/testers maps from board, firmware or case to a model')
    return {**defaults, **chosen}


def _cli_env(root, state, workspace):
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
               CIRCUIT_SKILLS_DIR=str(Path(root) / '.pilot/instructions/skills'),
               CIRCUIT_FIRMWARE_CACHE=str(FIRMWARE_CACHE),
               PLATFORMIO_CORE_DIR=str(FIRMWARE_CACHE / 'platformio'),
               ARDUINO_DIRECTORIES_DATA=str(FIRMWARE_CACHE / 'arduino'),
               PIP_CACHE_DIR=str(FIRMWARE_CACHE / 'pip'))
    return env


def codex(root, state, stage, model, workspace, prompt, schema, readonly):
    """One bounded CLI process. Preserve logs/usage even on model failure or timeout."""
    job = _job_directory(root, state, stage)
    write(job / 'schema.json', schema)
    (job / 'prompt.txt').write_text(prompt)
    output = job / 'answer.json'
    # Read-only lead views live outside Git; writable workers always run in a worktree.
    command = ['codex', 'exec', '--ignore-user-config', *(['--skip-git-repo-check'] if readonly else []),
               '--ephemeral', '--json', '-m', model, '-c', 'approval_policy="never"',
               '-c', 'model_reasoning_effort="medium"' if readonly else 'model_reasoning_effort="high"',
               '-s', 'read-only' if readonly else WORKER_SANDBOX, '-C', str(workspace),
               '--output-schema', str(job / 'schema.json'), '-o', str(output), '-']
    return _bounded_cli(root, state, stage, model, workspace, prompt, schema, job, command,
                        _cli_env(root, state, workspace), 'Codex',
                        lambda stdout: json.loads(output.read_text()), usage_of, capacity_event)


def _claude_sandbox(workspace, root=None):
    """Seatbelt profile standing in for Codex's sandbox: a reader writes only Claude's own state.

    A builder (given `root`) may also write its own worktree and the shared toolchain caches,
    but never the integration workspace or the controller's files.
    """
    home = Path.home()
    # A refreshed login must reach the keychain: the old token stops working once a new one is issued.
    writable = ['/private/tmp', '/private/var/folders', '/dev', home / '.claude', home / 'Library/Caches',
                home / 'Library/Keychains']
    if root is not None:
        # Headless Chrome keeps crash and profile state here even with its own --user-data-dir.
        writable += [Path(workspace).resolve(), FIRMWARE_CACHE, home / '.cache',
                     home / 'Library/Application Support/Google']
    allowed = ' '.join(f'(subpath "{path}")' for path in writable)
    protected = ([Path(workspace).resolve()] if root is None else
                 [Path(root).resolve() / 'workspace', Path(root).resolve() / '.pilot'])
    # The last matching rule wins, so a view under a temp directory is still protected.
    return ('(version 1)(allow default)(deny file-write*)'
            f'(allow file-write* {allowed} (regex "^{re.escape(str(home))}/\\.claude\\.json"))'
            + ''.join(f'(deny file-write* (subpath "{path}"))' for path in protected))


def _claude_result(text):
    """The CLI's final result event, never text echoed from a prompt/tool."""
    for line in reversed(text.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('type') == 'result':
            return event
    return None


def _session_of(stdout):
    """The CLI session a job ran in, so the next job for the same team can resume it."""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get('session_id'), str):
            return event['session_id']
    return None


def _claude_answer(stdout):
    event = _claude_result(stdout)
    if event is None or event.get('is_error') or not isinstance(event.get('structured_output'), dict):
        raise ValueError('Claude returned no structured answer: ' +
                         str((event or {}).get('result') or (event or {}).get('subtype'))[:300])
    return event['structured_output']


def claude_usage(text):
    """Same fields as Codex: `input_tokens` counts everything sent, cached part included."""
    event = _claude_result(text)
    models = event.get('modelUsage') if event else None
    if not isinstance(models, dict) or not models:
        return None
    read = sum(m.get('cacheReadInputTokens', 0) for m in models.values())
    return {'input_tokens': read + sum(m.get('inputTokens', 0) + m.get('cacheCreationInputTokens', 0)
                                       for m in models.values()),
            'cached_input_tokens': read,
            'output_tokens': sum(m.get('outputTokens', 0) for m in models.values()),
            'reasoning_output_tokens': sum(m.get('thinkingTokens', 0) for m in models.values()),
            'cost_usd': event.get('total_cost_usd')}


def claude_capacity(text):
    """Busy or logged out: the model is unavailable, and the design is not at fault."""
    event = _claude_result(text)
    if not event or not event.get('is_error'):
        return False
    return (event.get('api_error_status') in (401, 429, 500, 503, 529) or
            str(event.get('result')).startswith('Failed to authenticate'))


def _claude_exit_detail(text):
    event = _claude_result(text)
    return f": {str(event.get('result'))[:300]}" if event and event.get('is_error') else ''


# macOS refuses a second sandbox inside sandbox-exec, so Chrome's own sandbox cannot start.
# v17d/v17e: an Opus case builder at high effort spent 34 minutes in one think and wrote no CAD
# before the 60-minute limit, so Claude builders run at medium and are told to build in small steps.
CLAUDE_BUILDER_NOTE = ('\n\nYour shell runs inside a macOS sandbox. Writes outside this worktree, temp '
                       'folders and the toolchain caches are refused. Start headless Chrome with '
                       '--no-sandbox, because its own sandbox cannot start inside this one.\n'
                       '\nWork in small steps: write a first runnable version within your first ten '
                       'minutes, run it, look at the result, then fix. Do not plan the whole design in '
                       'one long think. Do not read the harness source code unless one of its commands '
                       'fails and you need to know why.\n')
CLAUDE_BUILDER_EFFORT = 'medium'


def claude(root, state, stage, model, workspace, prompt, schema, readonly):
    """A lead, tester or builder job on the Claude CLI, always inside a write-limiting sandbox."""
    if not Path('/usr/bin/sandbox-exec').is_file():
        raise ValueError('Claude team jobs need sandbox-exec to limit what they write')
    if not readonly and Path(workspace).resolve().parent != (Path(root) / 'workers').resolve():
        raise ValueError('A Claude builder writes only in its own worker worktree')
    job = _job_directory(root, state, stage)
    write(job / 'schema.json', schema)
    (job / 'prompt.txt').write_text(prompt)
    tools = 'Read,Grep,Glob,Bash' if readonly else 'Read,Edit,Write,Grep,Glob,Bash'
    task = prompt
    # A builder keeps its session so the team's next job (timeout retry, tester repair, integration
    # repair) resumes with the code it wrote in memory instead of reading the whole worktree again.
    resume = None if readonly else state.get('resumeSession')
    if not readonly:
        prompt += CLAUDE_BUILDER_NOTE
    sandbox = _claude_sandbox(workspace) if readonly else _claude_sandbox(workspace, root)
    # Empty setting sources and the two switches keep this machine's hooks, memory and CLAUDE.md out.
    command = ['/usr/bin/sandbox-exec', '-p', sandbox, 'claude', '-p',
               '--model', model, '--effort', 'medium' if readonly else CLAUDE_BUILDER_EFFORT,
               '--output-format', 'stream-json', '--verbose',
               *(['--no-session-persistence'] if readonly else []),
               *(['--resume', resume] if resume else []),
               '--setting-sources', '', '--strict-mcp-config',
               '--tools', tools, '--allowedTools', tools, '--permission-mode', 'dontAsk',
               '--json-schema', json.dumps(schema)]
    env = {k: v for k, v in _cli_env(root, state, workspace).items()
           if k not in ('CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT')}
    env.update(CLAUDE_CODE_DISABLE_AUTO_MEMORY='1', CLAUDE_CODE_DISABLE_CLAUDE_MDS='1')
    receipt = _bounded_cli(root, state, stage, model, workspace, prompt, schema, job, command, env, 'Claude',
                           _claude_answer, claude_usage, claude_capacity, _claude_exit_detail)
    if resume and receipt['error'] and 'No conversation found' in receipt['error']:
        # The saved session is gone; start fresh and let the handoff note carry the work.
        return claude(root, {**state, 'resumeSession': None}, stage, model, workspace, task, schema,
                      readonly)
    return receipt


def agent(root, state, stage, model, workspace, prompt, schema, readonly):
    """Route a job to the CLI that serves its model."""
    run_job = claude if model.startswith('claude-') else codex
    return run_job(root, state, stage, model, workspace, prompt, schema, readonly)


class _CommandClock(threading.Thread):
    """Time each tool call from the live event file; neither CLI reports a duration."""

    def __init__(self, events, log):
        super().__init__(daemon=True)
        self.events, self.log = Path(events), Path(log)
        self.open = {}
        self.count = 0
        self.seconds = 0.0
        self.offset = 0
        self.done = threading.Event()

    def _record(self, command, began, now, exit_code=None, unfinished=False):
        seconds = round(now - began, 1)
        self.count += 1
        self.seconds += seconds
        entry = {'command': command[:300], 'startedAt': began, 'seconds': seconds, 'exitCode': exit_code}
        with self.log.open('a') as stream:
            stream.write(json.dumps({**entry, 'unfinished': True} if unfinished else entry) + '\n')

    def feed(self, line, now):
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                return
            item = event.get('item')
            if isinstance(item, dict) and item.get('type') == 'command_execution':  # Codex
                if event.get('type') == 'item.started':
                    self.open[item['id']] = (item.get('command') or '', now)
                elif event.get('type') == 'item.completed' and item['id'] in self.open:
                    command, began = self.open.pop(item['id'])
                    self._record(command, began, now, item.get('exit_code'))
            message = event.get('message')
            for block in (message.get('content') if isinstance(message, dict) else None) or ():  # Claude
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'tool_use':
                    tool_input = block.get('input') or {}
                    self.open[block['id']] = (tool_input.get('command') or
                                              f"{block.get('name')} {tool_input.get('file_path', '')}".strip(), now)
                elif block.get('type') == 'tool_result' and block.get('tool_use_id') in self.open:
                    command, began = self.open.pop(block['tool_use_id'])
                    self._record(command, began, now)
        except (ValueError, KeyError, TypeError, AttributeError):
            return

    def _read(self):
        if not self.events.is_file():
            return
        with self.events.open(errors='replace') as stream:
            stream.seek(self.offset)
            while True:
                position = stream.tell()
                line = stream.readline()
                if not line.endswith('\n'):
                    self.offset = position
                    return
                self.feed(line, time.time())

    def run(self):
        while not self.done.wait(1):
            self._read()

    def finish(self):
        """A tool still open at the end is what a timeout was waiting on, so keep it."""
        self.done.set()
        self.join(5)
        self._read()
        now = time.time()
        for command, began in self.open.values():
            self._record(command, began, now, unfinished=True)
        self.open = {}
        return {'count': self.count, 'seconds': round(self.seconds, 1)}


class JobStalled(subprocess.TimeoutExpired):
    """A hung job counts as a timeout, so the usual continuation applies."""

    def __str__(self):
        return f'Stalled: no CLI output for {int(self.timeout)} s'


def _wait_while_active(process, events, timeout):
    deadline = time.monotonic() + timeout
    last_size, last_change = -1, time.monotonic()
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            return process.wait(timeout=min(1.0, deadline - now))
        except subprocess.TimeoutExpired:
            pass
        size = events.stat().st_size if events.is_file() else 0
        if size != last_size:
            last_size, last_change = size, time.monotonic()
        elif time.monotonic() - last_change > STALL_SECONDS:
            raise JobStalled(process.args, STALL_SECONDS)


def _bounded_cli(root, state, stage, model, workspace, prompt, schema, job, command, env, name,
                 answer, usage, capacity, exit_detail=lambda stdout: ''):
    began = time.time()
    stdout, result, error = '', None, None
    process = None
    io_complete = False
    interrupted = timed_out = False
    # The CLI writes events straight to the job folder, so a running job can be inspected.
    events = job / 'events.jsonl'
    clock = _CommandClock(events, job / 'commands.jsonl')
    clock.start()
    try:
        # stderr goes to a file: an undrained pipe would block the CLI and look like a stall.
        with events.open('w') as event_stream, (job / 'stderr.log').open('w') as error_stream:
            process = subprocess.Popen(command, cwd=workspace, stdin=subprocess.PIPE, stdout=event_stream,
                                       stderr=error_stream, text=True, env=env, start_new_session=True)
        with ACTIVE_LOCK:
            ACTIVE.setdefault(str(root), set()).add(process.pid)
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except BrokenPipeError:
            pass
        _wait_while_active(process, events, state['timeoutSeconds'])
        io_complete = True
        stdout = events.read_text(errors='replace')
        if process.returncode:
            raise ValueError(f'{name} exited {process.returncode}{exit_detail(stdout)}')
        result = answer(stdout)
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
            except (ProcessLookupError, PermissionError):
                pass
            _reap_worktree_processes(root, workspace)
            if not io_complete:
                try:
                    process.stdin.close()
                except (OSError, ValueError):
                    pass
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    error = (error or '') + '; job did not exit after termination'
            with ACTIVE_LOCK:
                ACTIVE.get(str(root), set()).discard(process.pid)
        if not io_complete and events.is_file():
            stdout = events.read_text(errors='replace')
        commands = clock.finish()
    receipt = {'stage': stage, 'model': model, 'startedAt': began, 'seconds': time.time() - began,
               'usage': usage(stdout), 'commands': commands, 'result': result, 'error': error,
               'sessionId': _session_of(stdout),
               'failureKind': ('interrupted' if interrupted else 'timeout' if timed_out else
                               'model_capacity' if error and process is not None and
                               process.returncode not in (None, 0) and capacity(stdout) else None),
               'logDirectory': str(job.relative_to(root))}
    write(job / 'receipt.json', receipt)
    return receipt


def _builder_session(state, stage):
    """The team's last Claude builder session in this contract revision, if any."""
    saved = (state.get('builderSessions') or {}).get(_stage_role(stage))
    if isinstance(saved, dict) and saved.get('revision') == state['revision']:
        return saved.get('sessionId')
    return None


def _handoff_first(role):
    if role not in HANDOFF:
        return ''
    return (f' Read {HANDOFF[role]} first if it exists; open other files only when you need them.')


CITED_LINES = re.compile(r'([A-Za-z0-9_][\w./-]*\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?')


def _cited_code(worktree, findings, *, limit=6, margin=8, max_lines=80):
    """Paste the lines a finding cites, so a repair job starts at the fault instead of re-reading."""
    worktree = Path(worktree).resolve()
    blocks, seen = [], set()
    for name, first, last in CITED_LINES.findall(json.dumps(findings)):
        if len(blocks) >= limit or (name, first, last) in seen:
            continue
        seen.add((name, first, last))
        path = (worktree / name).resolve()
        if '..' in PurePosixPath(name).parts or not path.is_relative_to(worktree) or not path.is_file():
            continue
        try:
            lines = path.read_text(errors='replace').splitlines()
        except OSError:
            continue
        start = max(int(first) - margin, 1)
        end = min(int(last or first) + margin, len(lines), start + max_lines - 1)
        if start > end:
            continue
        body = '\n'.join(f'{number:>5}  {lines[number - 1]}' for number in range(start, end + 1))
        blocks.append(f'--- {name}:{start}-{end}\n{body}')
    return ('\nCITED CODE (current worktree):\n' + '\n'.join(blocks) + '\n') if blocks else ''


def _repair_context(worktree, role, findings):
    return ('\nWhere to start:' + _handoff_first(role) + ' The finding cites the code below.' +
            _cited_code(worktree, findings))


def call(root, state, stage, workspace, prompt, schema, readonly, runner,
         authorized_integration=None, integration_lock=None, stage_deadline=None,
         on_timeout=None):
    model = (state['leadModel'] if readonly else
             (state.get('builderModels') or {}).get(_stage_role(stage)) or state['workerModel'])
    # Board and case both iterate on heavy geometry/checks; give them long continuations.
    long_stage = stage in LONG_STAGES and not readonly
    stage_budget = (max(state['timeoutSeconds'], BOARD_STAGE_WALL_SECONDS) if long_stage else
                    state['timeoutSeconds'] + TIMEOUT_CONTINUATION_SECONDS)
    max_attempts = (1 + len(CAPACITY_RETRY_DELAYS) + BOARD_MAX_TIMEOUT_CONTINUATIONS
                    if long_stage else MAX_STAGE_ATTEMPTS)
    began_at = _stage_clock()
    deadline = min(began_at + stage_budget, stage_deadline) if stage_deadline else began_at + stage_budget
    capacity_deadline = began_at + state['timeoutSeconds'] + TIMEOUT_CONTINUATION_SECONDS
    initial_files = _retry_files(workspace)
    # Stat changes guard ownership/Git retry safety; only changed bytes count as
    # fresh owned progress, so a tool repeatedly touching the same file cannot
    # purchase another long continuation.
    previous_owned_content = ({name: info['sha256'] for name, info in snapshot(workspace).items()}
                              if long_stage else None)
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
                             'stage result. Preserve completed work; do not change shared inputs.' +
                             _handoff_first(_stage_role(stage)))
            if timeout_guidance is not None:
                retry_prompt += ('\nLEAD ADVICE ON THE PARTIAL WORK:\n' +
                                 json.dumps(timeout_guidance, sort_keys=True))
        full_attempt = LONG_STAGE_ATTEMPT_SECONDS if long_stage else state['timeoutSeconds']
        attempt_limit = min(((full_attempt if long_stage else
                              TIMEOUT_CONTINUATION_SECONDS) if short_timeout else
                             full_attempt), remaining)
        attempt_state = {**state, 'timeoutSeconds': attempt_limit,
                         'resumeSession': None if readonly else _builder_session(state, stage)}
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
            if not readonly and model.startswith('claude-') and receipt.get('sessionId'):
                state.setdefault('builderSessions', {})[_stage_role(stage)] = {
                    'sessionId': receipt['sessionId'], 'revision': state['revision']}
        previous_receipt = receipt
        kind = receipt.get('failureKind') if receipt.get('error') else None
        may_retry_capacity = (kind == 'model_capacity' and
                              capacity_retries < len(CAPACITY_RETRY_DELAYS) and
                              attempt < max_attempts)
        may_retry_timeout = (kind == 'timeout' and not readonly and
                             timeout_retries < (BOARD_MAX_TIMEOUT_CONTINUATIONS if long_stage else 1) and
                             attempt < max_attempts)
        if long_stage and kind == 'timeout' and not may_retry_timeout:
            receipt['retrySuppressed'] = (f'{stage.capitalize()} continuation attempt limit exhausted'
                                          if timeout_retries >= BOARD_MAX_TIMEOUT_CONTINUATIONS else
                                          'Stage attempt limit exhausted')
        if may_retry_capacity or may_retry_timeout:
            current_owned_content = None
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
                if long_stage and safe:
                    current_owned_content = {name: info['sha256']
                                             for name, info in snapshot(workspace).items()}
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                safe, reason, partial = False, f'{type(exc).__name__}: {exc}', False
            fresh_progress = (any(
                previous_owned_content.get(name) != current_owned_content.get(name)
                for name in previous_owned_content.keys() | current_owned_content.keys()
                if owns(stage, name))
                if long_stage and safe and current_owned_content is not None else partial)
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
                    if long_stage:
                        previous_owned_content = current_owned_content
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


def _shared_field_root(field):
    if field.startswith('interfaces.'):
        field = field[len('interfaces.'):]
    return field.split('.', 1)[0].split('[', 1)[0]


def _contract_field(interfaces, field):
    value = interfaces
    for key in field.split('.'):
        if not isinstance(value, dict) or key not in value:
            raise ValueError('Unknown shared contract field: ' + field)
        value = value[key]
    return value


def plan(directory, *, execute=False, revise=False, contract_from=None, runner=agent):
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
            'with the actual native board during integration. This contract is sealed once '
            'and never replanned, so fix only what crosses teams: MCU/header/connector pins '
            'firmware or wiring uses, rails, outline, mounts and module envelopes. Leave '
            'pins internal to one board circuit (regulator, protection, MOSFET, IC pads) '
            'and numeric targets you chose yourself to the owning team; describe them in '
            'assumptions as targets, not sealed limits. '
            if state['mode'] == 'teams' else
            'Give disjoint board, firmware and case/frame tasks. Frame and device mounts first; '
            'add covers only if the brief asks for them. '
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
                         'any mismatch during integration.' + FIRMWARE_CACHE_NOTE),
            'case': ('Build the open skeleton frame, mounts, exports and checks from the initial '
                     'interface contract now. Check actual board geometry whenever available; report '
                     'and repair any mismatch during integration. Read print/events.jsonl when present '
                     'as the read-only record of actual print confirmations and failures before '
                     'repairing parts or assigning print plates. Keep case/PRINTED-INVENTORY.md '
                     'as a case-owned derived human-readable summary; never infer a print from '
                     'a printer job marked complete.'),
        }[role]
        return (
            'You are the BUILDER for the ' + role + ' team. Read AGENTS.md, PILOT_BRIEF.md, '
            'PILOT_CONTRACT.json and the KiCad tool card. The initial contract was sealed once; '
            'later lead release notes supplied below amend it in order and the newest applicable '
            'note wins for a changed field. Never edit the contract or release notes. Work only '
            f'inside {json.dumps(OWNED[role])}; other domains are read-only. Do not commit, push, '
            'delegate, order, print, flash, access hardware, alter gates or run the solo Stop loop. '
            'Your independent tester will inspect your actual files and may return concrete findings. '
            + (SKELETON_RULE + CASE_MEASURE_RULE if role == 'case' else '') +
            'Continue in the same worktree until the role task is complete or the bounded execution '
            'budget expires. Publish useful drafts early. Make slow checks you write use every CPU '
            'core and skip work when their inputs have not changed. While repairing, run only the '
            'checks that cover what you changed, at a coarser sampling when that still shows the '
            'repair works, then your full check suite once before you finish; do not '
            'rebuild a binary whose sources did not change. A local pass is not final product readiness; '
            'the integration check reconciles all teams. If a sealed detail inside your own domain '
            'is wrong or infeasible (a datasheet contradicts an internal pin, an agent-chosen '
            'size or limit cannot be met), choose the closest verified V0 option, record contract '
            f'text, change, evidence and cross-team impact in {DEVIATIONS[role]}, and continue. '
            f'Keep {HANDOFF[role]} current in under 60 lines: what each file you own does, where '
            'the key features live (function names and line ranges), the fastest check command '
            'for each, and open assumptions. A later repair job may start without your memory '
            'and reads it first. '
            'Keep cross-team pins, rails, outline and mounts unless no option exists. Report a '
            'blocker only for what needs the user (money, owned hardware, safety); preserve '
            'partial files. ' + task +
            f'\nReturn stage={stage}, contractSha256={state["contractSha256"]}, concise summary and blockers.'
        )
    task = {'board': ('Build and publish the real native schematic/PCB and enclosure contract. '
                      'If a contract-level hardware or interface blocker cannot be resolved with '
                      'verified components and pins, return it promptly in blockers instead of '
                      'polishing a board that cannot pass native and lead gates.'),
            'firmware': 'Read the checked, published board and actual netlist first. Implement and build firmware '
                        'against its real pins and power rails and the shared contract; include firmware/WIRING.md.'
                        + FIRMWARE_CACHE_NOTE,
            'case': 'Use the checked, published board/STEP and its actual device placement to build the '
                    'open skeleton frame and device/joint mounts. Add no covers at this stage.',
            'solo': 'Build board, firmware and open skeleton frame in that order. ' + SKELETON_RULE + CASE_MEASURE_RULE}[role]
    if role == 'solo' and stage.endswith('-final'):
        task = ('Reconcile firmware/WIRING against the actual published netlist and rebuild. Then '
                'add removable covers only if the brief or a release note explicitly asks; complete '
                'full-travel checks, interactive overview and arranged '
                'production/TEST slicer projects from the actual board/STEP. Preserve board interfaces; '
                'run native firmware and case checks.')
    elif stage.endswith('-final'):
        task = ('Reconcile firmware pins/power/behavior and WIRING against the actual published netlist and rebuild.'
                if role == 'firmware' else
                'Use the actual published board/STEP and checked skeleton frame. Add removable covers '
                'only if the brief or a release note explicitly asks. Check full travel with all ribs/hardware/wires, generate the interactive overview and '
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
            _drop_slicer_receipt(old, base['baseline'])
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
        'facts or parts cannot currently be verified. Only for a team_dispute (builder and '
        'tester stuck on the same files) you may choose waive when the tester\'s finding is '
        'unreasonable for this contract; cite each waived file as an action with the reason. '
        'Never waive a native, safety or polarity defect. For decisions other than interface_change, '
        'changedFields must be empty. Each action must cite an existing file in this worktree; '
        'for a failed final native check it may instead name the exact missing artifact inside '
        'the responsible worker-owned path. PILOT_CONTRACT.json is available when no '
        'implementation file exists yet. The worker '
        'still must pass native checks and lead review. '
        f'Return contractSha256={state["contractSha256"]}, stage={stage}.\n'
        'PROBLEM:\n' + json.dumps(problem, sort_keys=True))
    # `call` selects the lead model for read-only jobs; an advisor model, when the
    # project names one, answers in the lead's place on the same shared jobs list.
    advisor_state = {**state, 'leadModel': state.get('advisorModel') or state['leadModel']}
    receipt = call(root, advisor_state, stage + '-advice', worktree, prompt, ADVICE_SCHEMA, True,
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
    if advice['decision'] == 'waive' and problem.get('kind') != 'team_dispute':
        raise ValueError('Lead may waive only a stuck tester finding')
    if advice['decision'] == 'interface_change':
        # Leads cite paths such as "interfaces.pins[8].pin"; replan by top-level field.
        fields = list(dict.fromkeys(_shared_field_root(field) for field in advice['changedFields']))
        if not fields or any(field not in SHARED_INTERFACE_FIELDS for field in fields):
            raise ValueError('Contract revision needs named shared interface fields')
        interfaces = json.loads((root / state['contractFile']).read_text())['interfaces']
        for field in fields:
            _contract_field(interfaces, field)
        advice['changedFields'] = fields
    elif advice['changedFields']:
        raise ValueError('Repair/external advice cannot change contract fields')
    load(root)
    return advice


def _waivers_text(waivers):
    if not waivers:
        return ''
    return ('\nLEAD WAIVERS (the lead ruled these findings unreasonable; do not raise them '
            'again unless the cited file regressed):\n' + json.dumps(waivers, sort_keys=True))


def load_waivers(root):
    waivers = []
    for file in sorted((Path(root) / '.pilot/waivers').glob('*.json')):
        waivers.extend(json.loads(file.read_text()))
    return waivers


def _file_sha(worktree, name):
    path = Path(worktree) / name
    if path.is_symlink() or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _apply_waivers(worktree, review, waivers):
    """Drop tester findings the lead waived while the cited file is still unchanged."""
    waived = {(item['path'], item['sha256']) for item in waivers}
    findings = [item for item in review['findings']
                if (item['path'], _file_sha(worktree, item['path'])) not in waived]
    if review['decision'] != 'revise' or len(findings) == len(review['findings']):
        return review
    return {**review, 'findings': findings, 'decision': 'revise' if findings else 'approve'}


def _stage_role(stage):
    return stage.removesuffix('-final')


def _tester_model(state, role=None):
    return ((state.get('testerModels') or {}).get(role) or state.get('testerModel') or
            state['workerModel'])


CASE_MEASURE_RULE = ('In case/interfaces.json declare every joint (originMm, axis, rangeDeg, moves, clearOf, minClearanceMm), '
                     'every wire with at least two routes, and every fit a person must make with the coupon part that '
                     'tests it, using [] where there are none. The test kit must cover every mate, including how each '
                     'device is held. The checker measures these on the exported files and reports pass, review, '
                     'unverified or fail: fix every fail and unverified, and look at each review. Export STL at about '
                     'a tenth of the smallest clearance. ')
SKELETON_RULE = ('Build the structure like bones: an open skeleton of braced bars and cross-members, not a closed '
                 'box, closed cups or sheet walls. Carry load through short, braced bars with no thin part hanging '
                 'free. If one wire route is blocked another must be open, so leave several open routes that pass '
                 'the real connector housings. Every device must be held by the frame alone. Add skin, covers or '
                 'an enclosure only if the brief or a release note explicitly asks. ')
TESTER_RULES = (
    'Block only on defects that stop the product: it cannot be assembled, wired or printed; firmware '
    'cannot build, flash or do the core job in the brief; an electrical fault can damage hardware or '
    'is unsafe, such as reversed battery polarity; a limit in the brief on size, part count, '
    'screw count, print time or weight is exceeded; moving parts collide or jam, such as a leg hitting '
    'the body or a wheel that cannot turn, anywhere in their required travel; or a thin part hanging '
    'free will clearly break under load; or the structure is a closed box, closed cups or sheet walls '
    'instead of an open skeleton of braced bars, unless the brief or a release note asks for an enclosure. '
    'Do not block on small '
    'dimensional misses a person can fix with pliers, a file or glue, or on labels, documentation, '
    'overview drawings, report coverage or spare hardware the person can buy. List those as NOTES in '
    'the summary instead. This is your one full review: inspect everything now and report every '
    'blocker at once, because later rounds only re-check what you list here. ')


def _recheck_text(findings):
    numbered = [{'number': number, **finding} for number, finding in enumerate(findings, 1)]
    return ('RE-TEST: this is not a new review. Check whether each numbered finding below is fixed in '
            'the actual files. Return revise with one entry in findings per finding still open, '
            'recheckOf set to its number; cite the file that holds the defect now, even if it differs '
            'from the file listed. Approve when all are fixed. Any other defect you notice goes in '
            'newIssues, never in findings: it does not block this re-test.\nFINDINGS TO RE-CHECK:\n' +
            json.dumps(numbered, sort_keys=True) + '\n')


def _save_tester_notes(root, stage, result):
    """The first review's summary holds what a person can fix by hand; the lead reports it."""
    write(Path(root) / '.pilot/tester-notes' / f'{stage}.json', {'stage': stage, 'summary': result['summary']})


def _cited_files(result):
    """Testers may cite file:line, but lines move between rounds; everything downstream keys on the file."""
    findings = []
    for finding in result['findings']:
        path, line = re.fullmatch(r'(.*?)(:\d+(?:[-:]\d+)?)?', finding['path']).groups()
        message = f"(line {line[1:]}) {finding['message']}" if line else finding['message']
        findings.append({**finding, 'path': path, 'message': message})
    return {**result, 'findings': findings}


def _only_rechecked(result, findings):
    """A re-test may not grow the list, but a finding still open stays open wherever it now lives."""
    kept, notes = [], list(result['newIssues'])
    for finding in result['findings']:
        cited = {'path': finding['path'], 'message': finding['message']}
        if 1 <= finding['recheckOf'] <= len(findings):
            kept.append(cited)
        else:
            notes.append(cited)
    return {**result, 'findings': kept, 'newIssues': notes, 'decision': 'revise' if kept else 'approve'}


def _save_retest_notes(root, stage, result):
    """What a re-test noticed but may not block on, kept across rounds for the lead to report."""
    path = Path(root) / '.pilot/tester-notes' / f'{stage}-retest.json'
    issues = json.loads(path.read_text())['newIssues'] if path.exists() else []
    for issue in result['newIssues']:
        if issue not in issues:
            issues.append(issue)
    write(path, {'stage': stage, 'newIssues': issues})


def _finish_recheck(root, stage, result, findings):
    result = _only_rechecked(result, findings)
    _save_retest_notes(root, stage, result)
    return result


def team_test(root, state, role, stage, worktree, checks, runner, *,
              stage_deadline=None, authorized_integration=None, integration_lock=None,
              waivers=(), recheck=None):
    """Give each builder an independent, read-only tester in its own team."""
    from .parallel_addendum import notes_for_role

    if recheck is not None and not recheck:
        return {'contractSha256': state['contractSha256'], 'decision': 'approve',
                'summary': 'No tester findings to re-check', 'findings': []}
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
        f'A deviation recorded in {DEVIATIONS[role]} with evidence and no unrecorded '
        'cross-team impact is not a defect; check that the files follow it. '
        + (_recheck_text(recheck) if recheck else TESTER_RULES) +
        f'Return contractSha256={state["contractSha256"]}.\n'
        f'ROLE: {role}\nSTAGE: {stage}\nNATIVE CHECK: {json.dumps(checks, sort_keys=True)}\n'
        + notes_for_role(root, role) + _waivers_text(list(waivers))
    )
    # `call` selects the lead model for read-only jobs; the team's tester uses
    # its own model and the same shared jobs list, independent of lead gates.
    tester_state = {**state, 'leadModel': _tester_model(state, role)}
    schema = RECHECK_SCHEMA if recheck else REVIEW_SCHEMA
    receipt = call(root, tester_state, stage + '-test', worktree, prompt,
                   schema, True, runner,
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
    validate(result, schema)
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
    result = _cited_files(result)
    if result['decision'] == 'approve' and result['findings']:
        raise ValueError('Team tester approved unresolved findings')
    if result['decision'] == 'revise' and not result['findings']:
        raise ValueError('Team tester rejected without file-cited findings')
    for finding in result['findings']:
        path = PurePosixPath(finding['path'])
        if (path.is_absolute() or '..' in path.parts or not owns(role, str(path)) or
                (worktree / path).is_symlink()):
            raise ValueError('Team tester finding must cite its team-owned path')
    if recheck:
        return _finish_recheck(root, stage, result, recheck)
    _save_tester_notes(root, stage, result)
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
                       'interface and run native checks before reporting completion.' +
                       (_repair_context(dest, role, guidance) if state['mode'] == 'teams' else ''))
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
                  if stage in LONG_STAGES else state['timeoutSeconds'] + TIMEOUT_CONTINUATION_SECONDS)
        deadline = min(_stage_clock() + budget, stage_deadline) if stage_deadline else _stage_clock() + budget
        advice_rounds = 0
        # An integration repair re-checks only what the integration tester cited for this team.
        recheck = ([finding for finding in guidance['integration']['findings'] if owns(role, finding['path'])]
                   if isinstance(guidance, dict) and 'integration' in guidance else None)
        timeout_advised = False
        team_started = _stage_clock()
        waivers = [item for item in load_waivers(root) if item.get('stage') == stage]
        last_cited, arbitrated, history = {}, set(), []

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
                                     integration_lock=integration_lock, waivers=waivers,
                                     recheck=recheck)
                    test = _apply_waivers(dest, test, waivers)
                    recheck = test['findings']
                    if test['decision'] == 'revise' and problem is None:
                        problem = {'kind': 'team_tester', 'review': test}
                    if problem is None:
                        publish('passed')
                        return role, stage, dest, pointer, receipt, checks, None
                    if (advice_rounds >= MAX_TEAM_REPAIR_ROUNDS or
                            _stage_clock() - team_started > TEAM_ROLE_WALL_SECONDS):
                        publish('needs_revision', [str(problem)])
                        raise ValueError('Team task remains open after bounded continuation: ' +
                                         json.dumps(problem, sort_keys=True))
                    arbitration = None
                    if problem['kind'] == 'team_tester':
                        # Stuck = the tester cites the same file again and the builder left it
                        # byte-identical. Messages are reworded every round, so paths decide.
                        cited = {item['path']: _file_sha(dest, item['path'])
                                 for item in test['findings']}
                        stuck = sorted(path for path, sha in cited.items()
                                       if path in last_cited and last_cited[path] == sha)
                        last_cited = cited
                        if stuck:
                            if any(path in arbitrated for path in stuck):
                                publish('needs_revision', [f'Still stuck after lead ruling: {stuck}'])
                                raise ValueError('Team remains stuck after lead arbitration on ' +
                                                 ', '.join(stuck) + ': ' +
                                                 json.dumps(test, sort_keys=True))
                            arbitrated.update(stuck)
                            arbitration = advise(
                                root, state, stage, dest,
                                {'kind': 'team_dispute', 'stuck': stuck, 'review': test,
                                 'history': history[-3:]},
                                runner, stage_deadline=_stage_clock() + TIMEOUT_ADVICE_SECONDS,
                                authorized_integration=authorized_integration,
                                integration_lock=integration_lock)
                            with ACTIVE_LOCK:
                                state['advisorCalls'] = state.get('advisorCalls', 0) + 1
                            if arbitration['decision'] == 'interface_change':
                                raise ContractRevisionRequired(
                                    arbitration['summary'] + ': ' +
                                    ', '.join(arbitration['changedFields']),
                                    arbitration['changedFields'])
                            if arbitration['decision'] == 'external_blocker':
                                raise ExternalBlocker(arbitration['summary'])
                            if arbitration['decision'] == 'waive':
                                waivers.extend({'stage': stage, 'path': action['path'],
                                                'sha256': _file_sha(dest, action['path']),
                                                'reason': action['message']}
                                               for action in arbitration['actions'])
                                write(root / '.pilot/waivers' / f'{stage}.json', waivers)
                                test = _apply_waivers(dest, test, waivers)
                                if test['decision'] == 'approve':
                                    publish('passed')
                                    return role, stage, dest, pointer, receipt, checks, None
                                problem = {'kind': 'team_tester', 'review': test}
                        history.append({'round': advice_rounds + 1, 'summary': test['summary'],
                                        'paths': sorted(cited)})
                    advice_rounds += 1
                    # A tester-requested repair is new work; do not bill it to the build clock.
                    deadline = (min(_stage_clock() + budget, stage_deadline) if stage_deadline
                                else _stage_clock() + budget)
                    publish('needs_revision', test['findings'] if test['findings'] else
                            [str(problem)])
                    prompt = (worker_prompt(state, role, stage) + '\n\n' +
                              notes_for_role(root, role) + '\n\nINDEPENDENT TESTER FINDINGS:\n' +
                              json.dumps(test, sort_keys=True) + '\nPREVIOUS PROBLEM:\n' +
                              json.dumps(problem, sort_keys=True) +
                              ('\nLEAD RULING ON THE STUCK FINDINGS (follow it):\n' +
                               json.dumps(arbitration, sort_keys=True) if arbitration else '') +
                              '\nContinue in this same worktree. Repair actual files and rerun '
                              'native checks. Preserve the original contract and release notes.' +
                              _repair_context(dest, role, test['findings']))
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
            from .firmware import record_build
            proof = record_build(workspace, dest) if any(owns('firmware', name) for name in names) else None
            commit(workspace, [*names, *([proof] if proof else [])], 'Integrate ' + stage)
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
    (Path(workspace) / '.circuit').mkdir(exist_ok=True)
    passed, findings = inspect_board(workspace)
    if complete:
        if not firmware_written(workspace) or not firmware_built(workspace):
            findings.append('Firmware is missing or its compiled binary is stale')
        if not (Path(workspace) / 'firmware/WIRING.md').is_file():
            findings.append('firmware/WIRING.md is missing')
        case_state, reason = final_case_status(workspace)
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


def final_case_status(workspace):
    """A handwired build has no PCB enclosure to fit: use the Case tab's print receipt status."""
    from .case import status
    try:
        manifest = json.loads((Path(workspace) / 'case/out/parts.json').read_text())
        handwired = manifest['verification']['nativePCBCheckerApplies'] is False
    except (OSError, ValueError, KeyError, TypeError):
        handwired = False
    if not handwired:
        return status(workspace)
    module = Path(__file__).resolve().parents[4] / 'viewer/src/server/circuit/catalog.mjs'
    script = """import {pathToFileURL} from 'node:url';
const {casePrintStatus} = await import(pathToFileURL(process.argv[1]).href);
console.log(JSON.stringify(casePrintStatus(process.argv[2], 'main')));"""
    process = subprocess.run(['node', '--input-type=module', '-e', script, str(module), str(workspace)],
                             text=True, capture_output=True, timeout=30)
    if process.returncode:
        return 'stale', 'handwired print status failed: ' + process.stderr[-400:]
    report = json.loads(process.stdout)
    return report['state'], report['reason']


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


def _without_lead_documents(root, result):
    """No team owns a top-level document, so one must not end a run; keep the finding for the lead."""
    is_document = lambda finding: ('/' not in finding['path'] and
                                   PurePosixPath(finding['path']).suffix.casefold() == '.md')
    documents = [finding for finding in result['findings'] if is_document(finding)]
    if not documents:
        return result
    write(Path(root) / '.pilot/lead-document-findings.json', documents)
    kept = [finding for finding in result['findings'] if not is_document(finding)]
    return {**result, 'findings': kept, 'decision': 'revise' if kept else 'approve'}


def team_integration_test(root, state, checks, runner, recheck=None):
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
        'specific role-owned file or the intended missing path. Recorded team deviations '
        f'({", ".join(DEVIATIONS[role] for role in ROLES)}) supersede a sealed detail when all '
        'affected teams follow them; cite the lagging team, not the contract. '
        'Top-level documents (README.md, BOM.md and the like) are written by the lead after '
        'this gate, so never cite them. Cite PILOT_CONTRACT.json only '
        'when the original interface cannot be satisfied without a new user instruction. '
        + (_recheck_text(recheck) if recheck else TESTER_RULES) +
        f'Return contractSha256={state["contractSha256"]}.\n'
        f'NATIVE CHECKS: {json.dumps(checks, sort_keys=True)}\n{notes}'
        + _waivers_text(load_waivers(root))
    )
    tester_state = {**state, 'leadModel': state.get('integrationTesterModel') or _tester_model(state)}
    schema = RECHECK_SCHEMA if recheck else REVIEW_SCHEMA
    receipt = call(root, tester_state, 'integration-test', workspace, prompt,
                   schema, True, runner)
    if receipt['error']:
        if receipt.get('failureKind') == 'interrupted':
            raise JobInterrupted(job_error(receipt))
        if receipt.get('failureKind') == 'timeout':
            raise ExecutionLimitExhausted(job_error(receipt))
        if receipt.get('failureKind') == 'model_capacity':
            raise ModelCapacityExhausted(job_error(receipt))
        raise ValueError(job_error(receipt))
    result = receipt['result']
    validate(result, schema)
    if result['contractSha256'] != state['contractSha256'] or snapshot(workspace) != before:
        raise ValueError('Integration tester changed files or reviewed another contract')
    safe, reason, _ = _retry_safety(root, workspace, 'integration-test', True,
                                    before_files, git_before)
    if not safe:
        raise ValueError('Integration tester changed Git metadata: ' + str(reason))
    result = _cited_files(result)
    if result['decision'] == 'approve' and result['findings']:
        raise ValueError('Integration tester approved unresolved findings')
    if result['decision'] == 'revise' and not result['findings']:
        raise ValueError('Integration tester rejected without cited findings')
    result = _without_lead_documents(root, result)
    for finding in result['findings']:
        path = PurePosixPath(finding['path'])
        if (path.is_absolute() or '..' in path.parts or
                (not owns('solo', str(path)) and str(path) != 'PILOT_CONTRACT.json') or
                (workspace / path).is_symlink()):
            raise ValueError('Integration finding needs a team-owned path')
    if recheck:
        return _finish_recheck(root, 'integration', result, recheck)
    _save_tester_notes(root, 'integration', result)
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


def _undelivered_roles(workspace):
    # Top-level product.json comes from the project template, so only team directories count.
    names = [name for name in snapshot(workspace) if '/' in name]
    return {role for role in ROLES if not any(owns(role, name) for name in names)}


def run_teams(directory, *, runner=agent, checker=native_check, on_checkpoint=None,
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
        # A note routed to one team on a new project cannot skip teams with nothing delivered yet.
        missing = _undelivered_roles(workspace)
        assigned = state['assignedRoles'] = [role for role in ROLES if role in assigned or role in missing]
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
                if 'board' in roles:
                    board_checks = None  # no sibling checkpoint may mirror an unchecked repaired board
                dispatch(root, state, [(role, role) for role in ROLES if role in roles],
                         runner, checker=checker, on_progress=on_progress, on_import=accepted,
                         worktree_tag=f'integration-{repair_round}',
                         guidance={'native': checks, 'integration': integration})
                if board_checks is None:
                    raise ValueError('Repaired board produced no checked native handoff')
                checks = publish_native(True)
                integration = team_integration_test(root, state, checks, runner,
                                                     recheck=integration['findings'] or None)
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


def run(directory, *, execute=False, runner=agent, checker=native_check, on_checkpoint=None,
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
            'workerModel': state['workerModel'], 'testerModel': _tester_model(state),
            'builderModels': state.get('builderModels') or {}, 'testerModels': state.get('testerModels') or {},
            'integrationTesterModel': state.get('integrationTesterModel') or _tester_model(state),
            'advisorModel': state.get('advisorModel') or state['leadModel'],
            'contractSha256': state.get('contractSha256'),
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
    init.add_argument('--tester-model')
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
                                tester_model=args.tester_model, timeout=args.timeout)
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
