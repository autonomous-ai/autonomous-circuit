"""Run the isolated KiCad team behind a normal Harness prompt and viewer workspace.

The pilot remains the source of truth in ``.circuit/team-runs``. Only checked,
integrated artifacts are copied into the workspace watched by the Harness viewer.
This module never starts a model without an explicit ``--execute``.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

from . import parallel
from .parallel_contract import OWNED, owns


STATE_NAME = 'team-bridge.json'
RUN_NAME = re.compile(r'r[0-9]{3,}$')
VERDICT = '.harness/verdict.json'
MAX_FILE = parallel.MAX_FILE
MAX_TOTAL = parallel.MAX_TOTAL
CHECKPOINT_STAGES = frozenset(('board', 'board-approved', 'firmware', 'case',
                               'firmware-final', 'case-final', 'final'))
SHA256 = re.compile(r'[0-9a-f]{64}')
GIT_HEAD = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _relative(name, *, verdict=False):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('Invalid mirrored path')
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')) or str(path) != name:
        raise ValueError('Invalid mirrored path')
    if not owns('solo', name) and not (verdict and name == VERDICT):
        raise ValueError('Path is outside team-owned artifacts')
    return path


def _safe_target(root, name, *, verdict=False):
    relative = _relative(name, verdict=verdict)
    target = root
    for part in relative.parts:
        target = target / part
        if target.is_symlink():
            raise ValueError(f'Symlink in workspace artifact path: {name}')
    return target


def _git_metadata_digest(source):
    """Fingerprint the pilot's shared Git control files, not just tracked worktree files."""
    gitdir = source / '.git'
    if gitdir.is_symlink() or not gitdir.is_dir():
        raise ValueError('Integrated pilot Git directory changed')
    for name in ('config', 'info/exclude', 'info/attributes'):
        file = gitdir / name
        if file.is_symlink() or (file.exists() and not file.is_file()):
            raise ValueError('Integrated pilot Git metadata changed')
    return parallel.digest(parallel._git_metadata_sha(source))


def _file_info(path, *, maximum=MAX_FILE):
    if path.is_symlink():
        raise ValueError(f'Symlink is not an artifact: {path}')
    mode = path.stat()
    if not stat.S_ISREG(mode.st_mode) or mode.st_size > maximum:
        raise ValueError(f'Invalid or oversized artifact: {path}')
    return {'sha256': _sha(path), 'executable': bool(mode.st_mode & 0o111), 'bytes': mode.st_size}


def _tree_files(root, directory):
    """Walk one project-owned directory, rejecting links at every depth."""
    base = root
    for part in PurePosixPath(directory).parts:
        base = base / part
        if base.is_symlink():
            raise ValueError(f'Symlink in project-owned directory: {directory}')
    if not base.exists():
        return
    if not base.is_dir():
        raise ValueError(f'Expected directory: {base}')
    for current, dirs, files in os.walk(base, followlinks=False):
        for name in dirs + files:
            if (Path(current) / name).is_symlink():
                raise ValueError(f'Symlink in project-owned directory: {directory}')
        for name in files:
            file = Path(current) / name
            if not file.is_file():
                raise ValueError(f'Non-file project artifact: {file}')
            yield file


def _owned_snapshot(root):
    result, total = {}, 0
    prefixes = {prefix for role in OWNED.values() for prefix in role}
    for prefix in sorted(prefixes):
        if prefix.endswith('/'):
            files = _tree_files(root, prefix[:-1])
        else:
            file = _safe_target(root, prefix)
            files = (file,) if file.exists() else ()
        for file in files:
            name = file.relative_to(root).as_posix()
            _relative(name)
            info = _file_info(file)
            total += info['bytes']
            if total > MAX_TOTAL or len(result) >= 10000:
                raise ValueError('Visible artifacts exceed pilot bounds')
            result[name] = info
    return result


def _asset_snapshot(workspace):
    result, total = {}, 0
    for directory in ('prompts/assets', 'references'):
        for file in _tree_files(workspace, directory):
            name = file.relative_to(workspace).as_posix()
            info = _file_info(file)
            total += info['bytes']
            if total > MAX_TOTAL or len(result) >= 10000:
                raise ValueError('Reference assets exceed pilot bounds')
            result[name] = info
    return result


def _state_file(workspace):
    return workspace / '.circuit' / STATE_NAME


def _control_path(workspace, *members):
    target = workspace / '.circuit'
    if target.is_symlink():
        raise ValueError('Team control directory is a symlink')
    for member in members:
        target = target / member
        if target.is_symlink():
            raise ValueError('Team control path is a symlink')
    return target


def _read_state(workspace):
    file = _state_file(workspace)
    if not file.exists():
        return {'schema': 1, 'nextRun': 1, 'status': 'idle', 'lastMirroredRun': None,
                'activeRun': None, 'stage': None, 'files': {}, 'teamReady': False}
    if file.is_symlink():
        raise ValueError('Team bridge state is a symlink')
    state = json.loads(file.read_text())
    if state.get('schema') != 1 or not isinstance(state.get('files'), dict):
        raise ValueError('Invalid team bridge state')
    for name, info in state['files'].items():
        _relative(name, verdict=True)
        if not isinstance(info, dict) or not re.fullmatch('[0-9a-f]{64}', str(info.get('sha256', ''))):
            raise ValueError('Invalid team bridge manifest')
    for key in ('lastMirroredRun', 'activeRun'):
        value = state.get(key)
        if value is not None and (not isinstance(value, str) or not RUN_NAME.fullmatch(value)):
            raise ValueError('Invalid team run identifier')
    for key, pattern in (('sourceHead', GIT_HEAD),
                         ('sourceGitMetadataSha256', SHA256)):
        value = state.get(key)
        if value is not None and (not isinstance(value, str) or not pattern.fullmatch(value)):
            raise ValueError('Invalid team source checkpoint')
    return state


def _save_state(workspace, state):
    state['updatedAt'] = time.time()
    parallel.write(_state_file(workspace), state)


@contextlib.contextmanager
def _locked(workspace):
    circuit = workspace / '.circuit'
    if circuit.is_symlink():
        raise ValueError('.circuit must be a real directory')
    circuit.mkdir(exist_ok=True)
    lockfile = circuit / 'team-bridge.lock'
    if lockfile.is_symlink():
        raise ValueError('Team bridge lock is a symlink')
    with lockfile.open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError('Another team operation is running') from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _visible_unchanged(workspace, state):
    expected = state['files']
    visible = _owned_snapshot(workspace)
    expected_owned = {name: info for name, info in expected.items() if name != VERDICT}
    for name in visible.keys() | expected_owned.keys():
        if name not in expected_owned and not state.get('lastMirroredRun') and name == 'product.json':
            template = Path(__file__).resolve().parents[4] / 'harness/kicad/template/product.json'
            if visible[name]['sha256'] == _sha(template):
                continue
        if name not in visible or name not in expected_owned or visible[name]['sha256'] != expected_owned[name]['sha256']:
            raise ValueError(f'Visible project artifact changed outside the team: {name}')
    if VERDICT in expected:
        target = _safe_target(workspace, VERDICT, verdict=True)
        if not target.is_file() or _file_info(target)['sha256'] != expected[VERDICT]['sha256']:
            raise ValueError('Visible native verdict changed outside the team')
    elif state.get('initialVerdictSha256'):
        target = _safe_target(workspace, VERDICT, verdict=True)
        if not target.is_file() or _file_info(target)['sha256'] != state['initialVerdictSha256']:
            raise ValueError('Initial native verdict changed outside the team')


def _copy_assets(workspace, pilot_workspace, asset_manifest):
    names = []
    for name, info in sorted(asset_manifest.items()):
        source = workspace / name
        if _file_info(source)['sha256'] != info['sha256']:
            raise ValueError(f'Reference changed while capturing brief: {name}')
        target = pilot_workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if _file_info(target)['sha256'] != info['sha256']:
            raise ValueError(f'Reference copy failed: {name}')
        names.append(name)
    if names:
        parallel.commit(pilot_workspace, names, 'Capture project reference assets')


def _pilot_artifacts(pilot_workspace):
    artifacts = {name: info for name, info in parallel.snapshot(pilot_workspace).items()
                 if owns('solo', name)}
    verdict = pilot_workspace / VERDICT
    if not verdict.is_file() or verdict.is_symlink():
        raise ValueError('Native checker did not publish a verdict')
    artifacts[VERDICT] = _file_info(verdict, maximum=16 * 1024 * 1024)
    try:
        data = json.loads(verdict.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('Native verdict is not valid JSON') from exc
    if not isinstance(data, dict) or not isinstance(data.get('ready'), bool):
        raise ValueError('Native verdict is malformed')
    return artifacts


def _journal_file(workspace):
    return workspace / '.circuit/team-mirror-journal.json'


def _read_journal(workspace):
    file = _journal_file(workspace)
    if not file.exists():
        return None
    if file.is_symlink():
        raise ValueError('Team mirror journal is a symlink')
    journal = json.loads(file.read_text())
    if journal.get('schema') != 1 or not RUN_NAME.fullmatch(str(journal.get('run', ''))):
        raise ValueError('Invalid team mirror journal')
    if journal.get('stage') not in CHECKPOINT_STAGES or not isinstance(journal.get('revision'), int):
        raise ValueError('Invalid team mirror checkpoint')
    if not GIT_HEAD.fullmatch(str(journal.get('sourceHead', ''))):
        raise ValueError('Invalid team mirror source commit')
    metadata = journal.get('sourceGitMetadataSha256')
    if metadata is not None and not SHA256.fullmatch(str(metadata)):
        raise ValueError('Invalid team mirror Git metadata')
    for field in ('previous', 'desired'):
        if not isinstance(journal.get(field), dict):
            raise ValueError('Invalid team mirror manifest')
        for name, info in journal[field].items():
            _relative(name, verdict=True)
            if not isinstance(info, dict) or not re.fullmatch('[0-9a-f]{64}', str(info.get('sha256', ''))):
                raise ValueError('Invalid team mirror manifest entry')
    return journal


def _current_sha(target):
    return _file_info(target)['sha256'] if target.exists() else None


def _permitted_sha(workspace, state, journal, name, current):
    previous = journal['previous'].get(name)
    desired = journal['desired'].get(name)
    if current == (desired or {}).get('sha256'):
        return True
    if previous is not None:
        return current == previous['sha256']
    if current is None:
        return True
    if name == 'product.json' and not state.get('lastMirroredRun'):
        template = Path(__file__).resolve().parents[4] / 'harness/kicad/template/product.json'
        return current == _sha(template)
    if name == VERDICT:
        return current == state.get('initialVerdictSha256')
    return False


def _apply_journal(workspace, state, journal):
    """Replay only this committed native checkpoint, accepting old or already copied bytes."""
    if state.get('activeRun') != journal['run']:
        raise ValueError('Mirror journal belongs to another team run')
    desired, previous = journal['desired'], journal['previous']
    source = _control_path(workspace, 'team-runs', journal['run'], 'workspace')
    if not source.is_dir() or parallel.git(source, 'rev-parse', 'HEAD') != journal['sourceHead']:
        raise ValueError('Integrated pilot revision changed since checkpoint')
    if (journal.get('sourceGitMetadataSha256') is not None and
            _git_metadata_digest(source) != journal['sourceGitMetadataSha256']):
        raise ValueError('Integrated pilot Git metadata changed since checkpoint')
    # The lead's approval can be a metadata-only checkpoint: the board bytes
    # stay identical while the reviewed stage advances. A prior replay may
    # also have saved the state just before unlinking this journal.
    if (state['files'] == desired and state.get('lastCheckpointStage') == journal['stage']
            and state.get('revision') == journal['revision']):
        _visible_unchanged(workspace, state)
        _journal_file(workspace).unlink()
        return state
    if state['files'] != previous:
        raise ValueError('Bridge manifest changed since mirror began')
    pilot_root = _control_path(workspace, 'team-runs', journal['run'])
    if parallel.git(source, 'status', '--porcelain'):
        raise ValueError('Integrated pilot has uncommitted changes')
    if _pilot_artifacts(source) != desired:
        raise ValueError('Integrated pilot artifacts changed since checkpoint')
    observed = _owned_snapshot(workspace)
    for name in observed.keys() - (desired.keys() | previous.keys()):
        if name != 'product.json' or state.get('lastMirroredRun'):
            raise ValueError(f'Unexpected visible project artifact: {name}')
    for name in desired.keys() | previous.keys():
        target = _safe_target(workspace, name, verdict=True)
        if not _permitted_sha(workspace, state, journal, name, _current_sha(target)):
            raise ValueError(f'Visible artifact changed during mirror: {name}')
    ordered = sorted((desired.keys() | previous.keys()) - {VERDICT}) + [VERDICT]
    for name in ordered:
        target = _safe_target(workspace, name, verdict=True)
        current = _current_sha(target)
        if not _permitted_sha(workspace, state, journal, name, current):
            raise ValueError(f'Visible artifact changed during mirror: {name}')
        if name not in desired:
            if current is not None:
                target.unlink()
            continue
        if current == desired[name]['sha256']:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.team-mirror-', dir=pilot_root)
        os.close(fd)
        temporary = Path(temporary)
        try:
            shutil.copy2(source / name, temporary)
            if _file_info(temporary)['sha256'] != desired[name]['sha256']:
                raise ValueError(f'Integrated artifact changed while copying: {name}')
            os.replace(temporary, target)
            target.chmod(0o755 if desired[name]['executable'] else 0o644)
        finally:
            temporary.unlink(missing_ok=True)
    for name, info in desired.items():
        if _current_sha(_safe_target(workspace, name, verdict=True)) != info['sha256']:
            raise ValueError(f'Mirror did not complete: {name}')
    state.update(lastMirroredRun=journal['run'], activeRun=journal['run'],
                 stage=journal['stage'], revision=journal['revision'],
                 lastCheckpointStage=journal['stage'],
                 contractSha256=journal.get('contractSha256'), files=desired,
                 sourceHead=journal['sourceHead'],
                 sourceGitMetadataSha256=journal.get('sourceGitMetadataSha256'),
                 nativeBoardReady=bool(json.loads((source / VERDICT).read_text())['ready']),
                 teamReady=False)
    _save_state(workspace, state)
    _journal_file(workspace).unlink()
    return state


def _mirror(workspace, pilot_root, pilot_workspace, state, pilot_state, stage):
    if stage not in CHECKPOINT_STAGES:
        raise ValueError('Unknown team checkpoint')
    if _read_journal(workspace) is not None:
        raise ValueError('Previous mirror checkpoint must be synced first')
    _visible_unchanged(workspace, state)
    artifacts = _pilot_artifacts(pilot_workspace)
    if parallel.git(pilot_workspace, 'status', '--porcelain'):
        raise ValueError('Native checkpoint is not a clean integrated Git revision')
    metadata = _git_metadata_digest(pilot_workspace)
    if (pilot_state.get('gitMetadataSha256') is not None and
            metadata != pilot_state['gitMetadataSha256']):
        raise ValueError('Pilot shared Git metadata changed before checkpoint')
    journal = {'schema': 1, 'run': pilot_root.name, 'stage': stage,
               'revision': pilot_state['revision'], 'contractSha256': pilot_state.get('contractSha256'),
               'sourceHead': parallel.git(pilot_workspace, 'rev-parse', 'HEAD'),
               'sourceGitMetadataSha256': metadata,
               'previous': state['files'], 'desired': artifacts}
    parallel.write(_journal_file(workspace), journal)
    _apply_journal(workspace, state, journal)


@contextlib.contextmanager
def _cancel_on_signal(pilot_root):
    old = {}
    cancelled = {'signal': None}

    def interrupt(number, _frame):
        cancelled['signal'] = number
        parallel.cancel_jobs(pilot_root)
        raise KeyboardInterrupt

    for number in (signal.SIGINT, signal.SIGTERM):
        old[number] = signal.getsignal(number)
        signal.signal(number, interrupt)
    try:
        yield cancelled
    finally:
        for number, handler in old.items():
            signal.signal(number, handler)


@contextlib.contextmanager
def _prevent_idle_sleep():
    """Keep a foreground macOS team run awake, with the parent PID as a fail-safe."""
    if sys.platform != 'darwin':
        yield
        return
    try:
        process = subprocess.Popen(
            ['/usr/bin/caffeinate', '-i', '-w', str(os.getpid())],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise RuntimeError(f'Unable to start macOS idle-sleep assertion: {exc}') from exc
    try:
        if process.poll() is not None:
            raise RuntimeError('Unable to hold macOS idle-sleep assertion')
        yield
    finally:
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass  # caffeinate exited between poll and terminate
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass  # caffeinate exited between wait and kill
            process.wait(timeout=5)


def start(workspace, prompt_file, *, execute=False, runner=None, checker=None, max_rounds=2):
    """Run one complete prompt in an isolated team and expose its checked artifacts."""
    if not execute:
        raise ValueError('Team model execution requires explicit --execute')
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or not 1 <= max_rounds <= 3:
        raise ValueError('max_rounds must be between 1 and 3')
    workspace = Path(workspace).resolve(strict=True)
    prompt_file = Path(prompt_file)
    if not workspace.is_dir() or not prompt_file.is_absolute() or not prompt_file.is_file():
        raise ValueError('Expected an existing workspace and an absolute prompt file')
    prompt = prompt_file.read_text()
    if not prompt.strip() or len(prompt) > 100000:
        raise ValueError('Expected a bounded nonempty product prompt')
    with _locked(workspace):
        state = _read_state(workspace)
        pending = _read_journal(workspace)
        if pending is not None:
            _apply_journal(workspace, state, pending)
        if state.get('status') == 'running':
            raise ValueError('Previous team run is still active or orphaned; inspect it before starting another')
        if not state.get('lastMirroredRun') and not state.get('initialVerdictSha256'):
            initial_verdict = _safe_target(workspace, VERDICT, verdict=True)
            if initial_verdict.exists():
                state['initialVerdictSha256'] = _file_info(initial_verdict)['sha256']
        _visible_unchanged(workspace, state)
        assets = _asset_snapshot(workspace)
        number = state['nextRun']
        if not isinstance(number, int) or number < 1:
            raise ValueError('Invalid next team run number')
        run_id = f'r{number:03}'
        pilot_root = _control_path(workspace, 'team-runs', run_id)
        if pilot_root.exists():
            raise ValueError('Team run directory already exists')
        seed = None
        if state.get('lastMirroredRun'):
            seed = _control_path(workspace, 'team-runs', state['lastMirroredRun'], 'workspace')
            if not seed.is_dir() or parallel.git(seed, 'status', '--porcelain'):
                raise ValueError('Prior integrated pilot is not a clean Git seed')
            if (state.get('sourceHead') is not None and
                    parallel.git(seed, 'rev-parse', 'HEAD') != state['sourceHead']):
                raise ValueError('Prior integrated pilot commit changed since checkpoint')
            if (state.get('sourceGitMetadataSha256') is not None and
                    _git_metadata_digest(seed) != state['sourceGitMetadataSha256']):
                raise ValueError('Prior integrated pilot Git metadata changed since checkpoint')
        inputs = _control_path(workspace, 'team-inputs')
        inputs.mkdir(exist_ok=True)
        frozen_prompt = inputs / f'{run_id}.txt'
        if frozen_prompt.exists():
            raise ValueError('Team input already exists')
        if assets:
            mappings = [f'{workspace / name} -> {name}' for name in sorted(assets)]
            prompt += ('\n\nPROJECT REFERENCE ASSETS: These files are copied into this pilot '
                       'workspace. Use the pilot-local path on the right in place of an original '
                       'absolute attachment path:\n' + '\n'.join(mappings) + '\n')
        if len(prompt) > 100000:
            raise ValueError('Prompt and asset index exceed pilot prompt limit')
        frozen_prompt.write_text(prompt)
        state.update(nextRun=number + 1, activeRun=run_id, status='running',
                     stage='initializing', error=None, maxRounds=max_rounds,
                     roundsAttempted=0, teamReady=False)
        _save_state(workspace, state)
        try:
            with _cancel_on_signal(pilot_root) as cancelled, _prevent_idle_sleep():
                parallel.initialize(pilot_root, frozen_prompt, seed=seed)
                pilot_workspace = pilot_root / 'workspace'
                _copy_assets(workspace, pilot_workspace, assets)
                for attempt in range(max_rounds):
                    state.update(status='running', stage=f'planning-{attempt + 1}', error=None)
                    _save_state(workspace, state)
                    options = {'execute': True, 'revise': attempt > 0}
                    if runner is not None:
                        options['runner'] = runner
                    planned = parallel.plan(pilot_root, **options)
                    if cancelled['signal'] is not None:
                        state.update(status='interrupted', stage='interrupted',
                                     error='Team run interrupted during planning', teamReady=False)
                        return state
                    if planned['status'] != 'planned':
                        terminal_stage = {'model_unavailable': 'model-unavailable',
                                          'execution_limit': 'execution-limit'}
                        state.update(status=planned['status'],
                                     stage=terminal_stage.get(planned['status'], state['stage']),
                                     error='; '.join(planned.get('findings', [])), teamReady=False)
                        return state
                    state.update(roundsAttempted=attempt + 1,
                                 stage=f'building-{attempt + 1}', error=None)
                    _save_state(workspace, state)
                    mirror_errors = []

                    def checkpoint(source, current, stage):
                        try:
                            _mirror(workspace, pilot_root, source, state, current, stage)
                        except Exception as exc:
                            mirror_errors.append(str(exc))
                            raise

                    options = {'execute': True, 'on_checkpoint': checkpoint}
                    if runner is not None:
                        options['runner'] = runner
                    if checker is not None:
                        options['checker'] = checker
                    finished = parallel.run(pilot_root, **options)
                    if cancelled['signal'] is not None:
                        state.update(status='interrupted', stage='interrupted',
                                     error='Team run interrupted during build', teamReady=False)
                        return state
                    state.update(status=finished['status'],
                                 stage='model-unavailable' if finished['status'] == 'model_unavailable'
                                 else 'execution-limit' if finished['status'] == 'execution_limit'
                                 else 'complete',
                                 error='; '.join(finished.get('findings', [])),
                                 teamReady=finished['status'] == 'candidate_ready')
                    if finished['status'] in ('model_unavailable', 'execution_limit'):
                        return state  # capacity or time limits cannot be repaired by replanning
                    if finished['status'] == 'candidate_ready' or mirror_errors or attempt + 1 == max_rounds:
                        return state
                    if finished['status'] != 'needs_revision':
                        return state
                    state.update(status='running', stage='revising', error=None)
                    _save_state(workspace, state)
                return state
        except KeyboardInterrupt:
            parallel.cancel_jobs(pilot_root)
            state.update(status='interrupted', stage='interrupted', error='Team run interrupted')
            state['teamReady'] = False
            return state
        except Exception as exc:
            parallel.cancel_jobs(pilot_root)
            state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            state['teamReady'] = False
            return state
        finally:
            _save_state(workspace, state)


def status(workspace):
    workspace = Path(workspace).resolve(strict=True)
    state = _read_state(workspace)
    state = {**state, 'mirrorPending': _read_journal(workspace) is not None}
    run_id = state.get('activeRun')
    if run_id:
        pilot_root = _control_path(workspace, 'team-runs', run_id)
        receipt = pilot_root / '.pilot/state.json'
        if receipt.is_file() and not receipt.is_symlink():
            state = {**state, 'pilot': json.loads(receipt.read_text())}
    return state


def sync(workspace):
    """Finish a partly copied, hash-bound checkpoint without changing pilot approval."""
    workspace = Path(workspace).resolve(strict=True)
    with _locked(workspace):
        state = _read_state(workspace)
        journal = _read_journal(workspace)
        if journal is not None:
            _apply_journal(workspace, state, journal)
        return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    start_cmd = commands.add_parser('start')
    start_cmd.add_argument('workspace')
    start_cmd.add_argument('--prompt', required=True)
    start_cmd.add_argument('--execute', action='store_true')
    start_cmd.add_argument('--max-rounds', type=int, choices=(1, 2, 3), default=2)
    status_cmd = commands.add_parser('status')
    status_cmd.add_argument('workspace')
    sync_cmd = commands.add_parser('sync')
    sync_cmd.add_argument('workspace')
    args = parser.parse_args(argv)
    try:
        if args.command == 'start':
            result = start(args.workspace, args.prompt, execute=args.execute,
                           max_rounds=args.max_rounds)
        elif args.command == 'sync':
            result = sync(args.workspace)
        else:
            result = status(args.workspace)
        ok = (args.command == 'sync' or
              result['status'] in ('candidate_ready', 'initialized', 'planned', 'idle'))
        print(json.dumps({'ok': ok, **result}, sort_keys=True))
        return 0 if ok else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
