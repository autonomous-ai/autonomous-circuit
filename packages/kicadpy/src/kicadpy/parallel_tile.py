"""Run the isolated KiCad team behind a normal Harness prompt and viewer workspace.

The pilot remains the source of truth in ``.circuit/team-runs``. Checked,
integrated artifacts are copied into the workspace watched by the Harness viewer.
Worker previews live separately under ``.circuit/team-previews`` and never
change accepted artifacts or the native verdict.
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
import tarfile
import tempfile
import threading
import time

from . import parallel
from .parallel_contract import OWNED, STRING, obj, owns, validate


STATE_NAME = 'team-bridge.json'
RUN_NAME = re.compile(r'r[0-9]{3,}$')
VERDICT = '.harness/verdict.json'
MAX_FILE = parallel.MAX_FILE
MAX_TOTAL = parallel.MAX_TOTAL
CHECKPOINT_STAGES = frozenset(('board', 'board-approved', 'firmware', 'case',
                               'firmware-final', 'case-final', 'final'))
PREVIEW_NAME = 'team-preview.json'
# A full team run uses several GB (worktrees, exports, firmware builds); ENOSPC stalled crab-v7.
MIN_FREE_GB = 8
PREVIEW_ROLES = frozenset(('board', 'firmware', 'case'))
PREVIEW_STATUSES = frozenset(('queued', 'draft', 'checking', 'passed',
                              'needs_revision', 'blocked'))
PREVIEW_MEDIA_SUFFIXES = frozenset(('.svg', '.png'))
PREVIEW_MAX_FILES = 8
PREVIEW_MAX_FILE = 4 * 1024 * 1024
PREVIEW_MAX_TOTAL = 16 * 1024 * 1024
SHA256 = re.compile(r'[0-9a-f]{64}')
GIT_HEAD = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')
QUESTION_SUFFIXES = frozenset(('.json', '.jsonl', '.md', '.txt', '.kicad_sch', '.kicad_pcb',
                               '.kicad_pro', '.c', '.h', '.cpp', '.ino', '.py',
                               '.scad', '.cq', '.svg', '.png', '.jpg', '.jpeg',
                               '.webp', '.pdf'))
QUESTION_MAX_FILE = 2 * 1024 * 1024
QUESTION_MAX_TOTAL = 24 * 1024 * 1024
QUESTION_MAX_FILES = 128
_PREVIEW_CACHE = {}
_PREVIEW_CACHE_LOCK = threading.Lock()


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


def _owned_snapshot(root, role=None):
    result, total = {}, 0
    prefixes = (set(OWNED[role]) if role in OWNED else
                {prefix for group in OWNED.values() for prefix in group})
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


def _preview_snapshot(root, role):
    """Hash only files whose filesystem identity changed since the last preview.

    A worker can update its own draft many times during a long tool call. The
    periodic watcher still walks names and stats every file, but avoids hashing
    a whole board or case tree every ten seconds when nothing changed.
    """
    key = (str(root.resolve(strict=True)), role)
    with _PREVIEW_CACHE_LOCK:
        previous = _PREVIEW_CACHE.get(key, {})
    result, cached, total = {}, {}, 0
    for prefix in sorted(OWNED[role]):
        if prefix.endswith('/'):
            files = _tree_files(root, prefix[:-1])
        else:
            file = _safe_target(root, prefix)
            files = (file,) if file.exists() else ()
        for file in files:
            name = file.relative_to(root).as_posix()
            _relative(name)
            mode = file.stat()
            if not stat.S_ISREG(mode.st_mode) or mode.st_size > MAX_FILE:
                raise ValueError(f'Invalid or oversized preview artifact: {file}')
            total += mode.st_size
            if total > MAX_TOTAL or len(result) >= 10000:
                raise ValueError('Preview artifacts exceed pilot bounds')
            signature = (mode.st_dev, mode.st_ino, mode.st_size,
                         mode.st_mtime_ns, mode.st_ctime_ns)
            old = previous.get(name)
            if old is not None and old[0] == signature:
                info = old[1]
            else:
                info = _file_info(file)
                after = file.stat()
                if signature != (after.st_dev, after.st_ino, after.st_size,
                                 after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError(f'Preview artifact changed while hashing: {name}')
            result[name] = info
            cached[name] = (signature, info)
    with _PREVIEW_CACHE_LOCK:
        _PREVIEW_CACHE[key] = cached
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
    for key in ('lastMirroredRun', 'activeRun', 'contractRun'):
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


def _preview_file(workspace):
    return _control_path(workspace, PREVIEW_NAME)


def _write_preview(workspace, data):
    destination = _preview_file(workspace)
    fd, temporary_name = tempfile.mkstemp(prefix='.team-preview-', dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, sort_keys=True, separators=(',', ':'))
            stream.write('\n')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _new_preview(run_id):
    return {'schema': 1, 'run': run_id, 'runStatus': 'running',
            'updatedAt': time.time(),
            'teams': {role: {'stage': None, 'status': 'queued', 'verified': False,
                             'files': [], 'media': [], 'findings': []}
                      for role in sorted(PREVIEW_ROLES)}}


def _read_preview(workspace, run_id):
    file = _preview_file(workspace)
    if file.is_symlink():
        raise ValueError('Team preview manifest is a symlink')
    if not file.exists():
        return _new_preview(run_id)
    data = json.loads(file.read_text())
    if data.get('schema') != 1 or data.get('run') != run_id or not isinstance(data.get('teams'), dict):
        return _new_preview(run_id)
    return data


def _preview_progress(workspace, pilot_root, role, stage, source_path, status, findings):
    """Publish bounded worker evidence as an explicitly unverified viewer preview.

    This is separate from the hash-bound accepted checkpoint mirror. Worker
    files never enter the project artifact tree or native verdict through here.
    """
    if role not in PREVIEW_ROLES or status not in PREVIEW_STATUSES:
        raise ValueError('Invalid preview role or status')
    if not isinstance(stage, str) or not stage or len(stage) > 80:
        raise ValueError('Invalid preview stage')
    if not isinstance(findings, list) or len(findings) > 20:
        raise ValueError('Invalid preview findings')
    if any(not isinstance(item, str) or len(item) > 500 for item in findings):
        raise ValueError('Invalid preview finding')
    source = Path(source_path)
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Preview source must be a real directory')
    root = pilot_root.resolve(strict=True)
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError('Preview source is outside the active pilot')
    current = root
    for part in resolved.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink in preview source path')
    # Report filenames for this role's current work, not seed files in the
    # integrated baseline. Copy images so their bytes stay stable as a worker
    # continues editing its private worktree.
    candidate = _preview_snapshot(resolved, role)
    baseline = _preview_snapshot(root / 'workspace', role)
    changed = sorted(name for name, info in candidate.items()
                     if owns(role, name) and baseline.get(name, {}).get('sha256') != info['sha256'])
    media = []
    total = 0
    for name in changed:
        info = candidate[name]
        if Path(name).suffix.lower() not in PREVIEW_MEDIA_SUFFIXES or info['bytes'] > PREVIEW_MAX_FILE:
            continue
        if len(media) >= PREVIEW_MAX_FILES:
            break
        if total + info['bytes'] > PREVIEW_MAX_TOTAL:
            continue
        source_file = _safe_target(resolved, name)
        target_name = f'.circuit/team-previews/current/{role}/{name}'
        target = workspace / target_name
        current = workspace
        parts = PurePosixPath(target_name).parts
        for part in parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ValueError('Symlink in preview destination path')
            if current.exists() and not current.is_dir():
                raise ValueError('Non-directory preview destination path')
            current.mkdir(exist_ok=True)
        if target.is_symlink():
            raise ValueError('Symlink in preview destination path')
        fd, temporary_name = tempfile.mkstemp(prefix='.team-preview-', dir=target.parent)
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source_file, temporary)
            if _file_info(temporary, maximum=PREVIEW_MAX_FILE)['sha256'] != info['sha256']:
                raise ValueError('Worker preview changed while copying')
            os.replace(temporary, target)
            target.chmod(0o644)
        finally:
            temporary.unlink(missing_ok=True)
        media.append({'file': name, 'path': target_name,
                      'sha256': info['sha256'], 'bytes': info['bytes']})
        total += info['bytes']
    data = _read_preview(workspace, pilot_root.name)
    previous = data['teams'].get(role, {})
    retain_previous = status in ('checking', 'passed')
    data['teams'][role] = {'stage': stage, 'status': status, 'verified': False,
                           'files': changed[:60] or (previous.get('files', []) if retain_previous else []),
                           'media': media or (previous.get('media', []) if retain_previous else []),
                           'findings': findings, 'updatedAt': time.time()}
    data['updatedAt'] = time.time()
    _write_preview(workspace, data)
    _prune_preview_media(workspace, role, data['teams'][role]['media'])
    return data


def _prune_preview_media(workspace, role, retained):
    """Discard images omitted by the current manifest without following links."""
    base = _control_path(workspace, 'team-previews', 'current', role)
    if not base.exists():
        return
    if not base.is_dir():
        raise ValueError('Preview media directory is not a directory')
    keep = {item['path'] for item in retained}
    for current, dirs, files in os.walk(base, topdown=False, followlinks=False):
        for name in files:
            file = Path(current) / name
            if file.is_symlink():
                raise ValueError('Symlink in preview media tree')
            relative = file.relative_to(workspace).as_posix()
            if relative not in keep:
                file.unlink()
        for name in dirs:
            directory = Path(current) / name
            if directory.is_symlink():
                raise ValueError('Symlink in preview media tree')
            if not any(directory.iterdir()):
                directory.rmdir()


def _clear_preview_media(workspace):
    base = _control_path(workspace, 'team-previews', 'current')
    if not base.exists():
        return
    if not base.is_dir():
        raise ValueError('Preview media directory is not a directory')
    for current, dirs, files in os.walk(base, followlinks=False):
        if any((Path(current) / name).is_symlink() for name in dirs + files):
            raise ValueError('Symlink in preview media tree')
    shutil.rmtree(base)


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


def _checkpoint_tree_matches_mirror(seed, commit, files):
    """Verify the recorded Git checkpoint still has exactly the mirrored owned bytes."""
    expected = {name: info for name, info in files.items() if name != VERDICT}
    seen = set()
    process = subprocess.Popen(['git', 'archive', commit], cwd=seed,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
            for member in archive:
                if member.issym() or member.islnk():
                    raise ValueError('Checkpoint Git archive contains a symlink')
                if not member.isfile() or not owns('solo', member.name):
                    continue
                info = expected.get(member.name)
                if info is None or member.name in seen or \
                        (info.get('bytes') is not None and member.size != info['bytes']) or \
                        bool(member.mode & 0o111) != info.get('executable'):
                    raise ValueError('Checkpoint Git tree differs from mirrored artifacts')
                digest = hashlib.sha256()
                with archive.extractfile(member) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
                if digest.hexdigest() != info['sha256']:
                    raise ValueError('Checkpoint Git tree differs from mirrored artifacts')
                seen.add(member.name)
        process.stdout.close()
        error = process.stderr.read()
        if process.wait() != 0 or seen != expected.keys():
            raise ValueError('Checkpoint Git tree differs from mirrored artifacts: ' +
                             error.decode(errors='replace')[-200:])
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.stdout.close()
        process.stderr.close()
        process.wait()
        raise
    finally:
        process.stderr.close()


def _checkpoint_seed_commit(seed, state, seed_run):
    """Use only a recorded checkpoint when a failed team repair left later imports."""
    error = 'Prior integrated pilot commit changed since checkpoint'
    if (state.get('status') != 'needs_revision' or state.get('activeRun') != seed_run or
            state.get('lastMirroredRun') != seed_run or not state.get('contractRun') or
            state.get('lastCheckpointStage') not in CHECKPOINT_STAGES):
        raise ValueError(error)
    pilot_file = seed.parent / '.pilot/state.json'
    if pilot_file.is_symlink() or not pilot_file.is_file():
        raise ValueError(error)
    pilot = json.loads(pilot_file.read_text())
    if pilot.get('status') != 'needs_revision' or pilot.get('mode') != 'teams':
        raise ValueError(error)
    checkpoint = state['sourceHead']
    head = parallel.git(seed, 'rev-parse', 'HEAD')
    if (parallel.git(seed, 'status', '--porcelain') or
            _git_metadata_digest(seed) != state.get('sourceGitMetadataSha256') or
            subprocess.run(['git', 'merge-base', '--is-ancestor', checkpoint, head],
                           cwd=seed, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL).returncode != 0):
        raise ValueError(error)
    commits = parallel.git(seed, 'rev-list', '--reverse', f'{checkpoint}..{head}').splitlines()
    if not 1 <= len(commits) <= 16:
        raise ValueError(error)
    parent = checkpoint
    for commit in commits:
        if parallel.git(seed, 'rev-list', '--parents', '-n', '1', commit).split() != [commit, parent]:
            raise ValueError(error)
        subject = parallel.git(seed, 'log', '-1', '--format=%s', commit)
        if subject not in ('Integrate board', 'Integrate firmware', 'Integrate case'):
            raise ValueError(error)
        role = subject.removeprefix('Integrate ')
        changed = subprocess.check_output(
            ['git', 'diff-tree', '--no-commit-id', '--name-only', '-z', '-r', commit], cwd=seed)
        names = [os.fsdecode(name) for name in changed.split(b'\0') if name]
        if not names or any(not owns(role, name) for name in names):
            raise ValueError(error)
        parent = commit
    _checkpoint_tree_matches_mirror(seed, checkpoint, state['files'])
    return checkpoint


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

    if threading.current_thread() is not threading.main_thread():
        # Embedded callers may run the bridge on a worker thread. Python only
        # permits signal handlers on the main thread; job cancellation remains
        # available through the controller's explicit cancel path.
        yield cancelled
        return

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


def start(workspace, prompt_file, *, execute=False, runner=None, checker=None,
          max_rounds=2, teams=False, resume_notes=False):
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
    need = float(os.environ.get('CIRCUIT_TEAM_MIN_FREE_GB', MIN_FREE_GB))
    free = shutil.disk_usage(workspace).free / 1024 ** 3
    if free < need:
        raise ValueError(f'Not enough free disk for a team run: {free:.1f} GB free, '
                         f'need {need:g} GB; free space and retry')
    with _locked(workspace):
        state = _read_state(workspace)
        previous_status = state.get('status')
        previous_ready = state.get('teamReady') is True
        pending_print_shas = list(state.get('pendingPrintNotes', []))
        if any(not isinstance(sha, str) or not SHA256.fullmatch(sha)
               for sha in pending_print_shas):
            raise ValueError('Invalid pending print note identifier')
        prior_run = state.get('activeRun')
        contract_run = state.get('contractRun') if teams else None
        if resume_notes and not contract_run:
            raise ValueError('Resume needs the original sealed team contract')
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
        seed_commit = None
        recovered_head = None
        seed_run = prior_run if teams and contract_run else state.get('lastMirroredRun')
        if seed_run:
            seed = _control_path(workspace, 'team-runs', seed_run, 'workspace')
            if not seed.is_dir() or parallel.git(seed, 'status', '--porcelain'):
                raise ValueError('Prior integrated pilot is not a clean Git seed')
            if (seed_run == state.get('lastMirroredRun') and state.get('sourceGitMetadataSha256') is not None and
                    _git_metadata_digest(seed) != state['sourceGitMetadataSha256']):
                raise ValueError('Prior integrated pilot Git metadata changed since checkpoint')
            if (seed_run == state.get('lastMirroredRun') and state.get('sourceHead') is not None and
                    parallel.git(seed, 'rev-parse', 'HEAD') != state['sourceHead']):
                seed_commit = _checkpoint_seed_commit(seed, state, seed_run)
                recovered_head = parallel.git(seed, 'rev-parse', 'HEAD')
        inputs = _control_path(workspace, 'team-inputs')
        inputs.mkdir(exist_ok=True)
        frozen_prompt = inputs / f'{run_id}.txt'
        if frozen_prompt.exists():
            raise ValueError('Team input already exists')
        if assets and not contract_run:
            mappings = [f'{workspace / name} -> {name}' for name in sorted(assets)]
            prompt += ('\n\nPROJECT REFERENCE ASSETS: These files are copied into this pilot '
                       'workspace. Use the pilot-local path on the right in place of an original '
                       'absolute attachment path:\n' + '\n'.join(mappings) + '\n')
        if len(prompt) > 100000:
            raise ValueError('Prompt and asset index exceed pilot prompt limit')
        if contract_run:
            original = _control_path(workspace, 'team-runs', contract_run)
            frozen_prompt.write_bytes((original / '.pilot/prompt.txt').read_bytes())
        else:
            frozen_prompt.write_text(prompt)
        _clear_preview_media(workspace)
        with _PREVIEW_CACHE_LOCK:
            _PREVIEW_CACHE.clear()
        state.pop('recoveredFromCheckpoint', None)
        state.update(nextRun=number + 1, activeRun=run_id, status='running',
                     stage='initializing', error=None, maxRounds=max_rounds,
                     roundsAttempted=0, replanRequired=False,
                     teamReady=previous_ready if teams and contract_run else False,
                     printTeam=None)
        _save_state(workspace, state)
        _write_preview(workspace, _new_preview(run_id))
        try:
            with _cancel_on_signal(pilot_root) as cancelled, _prevent_idle_sleep():
                parallel.initialize(pilot_root, frozen_prompt, seed=seed,
                                    seed_commit=seed_commit,
                                    mode='teams' if teams else 'parallel')
                if seed_commit is not None:
                    recovery = {'sourceRun': seed_run, 'checkpointHead': seed_commit,
                                'uncheckpointedHead': recovered_head}
                    parallel.write(pilot_root / '.pilot/recovery.json', recovery)
                    state['recoveredFromCheckpoint'] = recovery
                    _save_state(workspace, state)
                pilot_workspace = pilot_root / 'workspace'
                _copy_assets(workspace, pilot_workspace, assets)
                for attempt in range(1 if teams else max_rounds):
                    print_notes = []
                    state.update(status='running', stage=f'planning-{attempt + 1}', error=None)
                    _save_state(workspace, state)
                    if teams and contract_run:
                        from .parallel_addendum import (carry_addenda, load_addenda,
                                                        record_addendum)
                        original = _control_path(workspace, 'team-runs', contract_run)
                        planned = parallel.adopt_contract(pilot_root, original)
                        if prior_run:
                            previous_pilot = json.loads((
                                _control_path(workspace, 'team-runs', prior_run) /
                                '.pilot/state.json').read_text())
                            planned['draftOriginRun'] = previous_pilot.get('draftOriginRun', prior_run) \
                                if previous_pilot.get('status') == 'print_only' else prior_run
                            carry_addenda(_control_path(workspace, 'team-runs', prior_run),
                                           pilot_root)
                        if resume_notes:
                            previous = json.loads((_control_path(workspace, 'team-runs', prior_run) /
                                                   '.pilot/state.json').read_text())
                            chain = load_addenda(pilot_root)
                            prior_tip = previous.get('releaseNoteTipSha256', planned['contractSha256'])
                            unseen = []
                            seen = prior_tip == planned['contractSha256']
                            for entry in chain['entries']:
                                if seen:
                                    unseen.append(entry)
                                if entry['sha256'] == prior_tip:
                                    seen = True
                            if not seen:
                                raise ValueError('Prior release note tip is absent from carried chain')
                            roles = {role for entry in unseen for role in entry['routes']}
                            print_notes = [entry for entry in unseen if 'print' in entry['routes']]
                            planned['assignedRoles'] = [role for role in parallel.ROLES
                                                        if role in (roles if unseen or
                                                                    (previous_ready and pending_print_shas)
                                                                    else set(previous.get(
                                                                        'assignedRoles', parallel.ROLES)))]
                        else:
                            addendum = record_addendum(pilot_root, prompt_file, runner=runner,
                                                       source_assets=workspace)
                            planned['assignedRoles'] = [role for role in parallel.ROLES
                                                        if role in addendum['routes']]
                            if 'print' in addendum['routes']:
                                print_notes = [addendum]
                        if pending_print_shas:
                            from .parallel_print import initial_print_note
                            chain = load_addenda(pilot_root)
                            pending_entries = {entry['sha256']: entry for entry in chain['entries']}
                            pending_entries[planned['contractSha256']] = initial_print_note(pilot_root)
                            if any(sha not in pending_entries for sha in pending_print_shas):
                                raise ValueError('Pending print note absent from sealed history')
                            print_notes = list({entry['sha256']: entry for entry in
                                                [*(pending_entries[sha] for sha in pending_print_shas),
                                                 *print_notes]}.values())
                        parallel.write(pilot_root / '.pilot/state.json', planned)
                    else:
                        options = {'execute': True, 'revise': attempt > 0}
                        if runner is not None:
                            options['runner'] = runner
                        planned = parallel.plan(pilot_root, **options)
                        if teams and planned['status'] == 'planned':
                            state['contractRun'] = run_id
                            _save_state(workspace, state)
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
                    if teams:
                        from .parallel_print import copy_ledger_to_pilot, run_print_team
                        copy_ledger_to_pilot(workspace, pilot_root)
                        if contract_run and not planned.get('assignedRoles'):
                            # The lead assigned only print work. Preserve the last
                            # checked design verdict while the print team runs.
                            state.update(status=previous_status, stage='print',
                                         teamReady=previous_ready, error=None)
                            planned['status'] = 'print_only'
                            planned['releaseNoteTipSha256'] = load_addenda(
                                pilot_root, planned['contractSha256'])['tipSha256']
                            # An incomplete design can still be resumed later.
                            if not previous_ready and prior_run:
                                previous_pilot = json.loads((
                                    _control_path(workspace, 'team-runs', prior_run) /
                                    '.pilot/state.json').read_text())
                                planned['assignedRoles'] = previous_pilot.get(
                                    'assignedRoles', list(parallel.ROLES))
                            parallel.write(pilot_root / '.pilot/state.json', planned)
                            results = []
                            failed = []
                            for entry in print_notes:
                                try:
                                    results.append(run_print_team(workspace, pilot_root, entry,
                                                                  runner=runner))
                                except Exception as exc:
                                    results.append({'status': 'failed', 'error':
                                                    f'{type(exc).__name__}: {exc}'})
                                    failed.append(entry['sha256'])
                            state['pendingPrintNotes'] = failed
                            state['printTeam'] = {'ok': not failed, 'results': results, 'run': run_id}
                            return state
                        state['teamReady'] = False
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

                    preview_lock = threading.Lock()

                    def progress(role, stage, source_path, status, findings):
                        with preview_lock:
                            try:
                                _preview_progress(workspace, pilot_root, role, stage,
                                                  source_path, status, findings)
                            except (OSError, ValueError) as exc:
                                # A viewer preview is best effort. It must not
                                # change the build verdict or stop a worker.
                                data = _read_preview(workspace, run_id)
                                data['previewError'] = f'{type(exc).__name__}: {exc}'[:500]
                                data['updatedAt'] = time.time()
                                _write_preview(workspace, data)

                    options = {'execute': True, 'on_checkpoint': checkpoint,
                               'on_progress': progress}
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
                                 replanRequired=finished.get('replanRequired') is True,
                                 teamReady=finished['status'] == 'candidate_ready')
                    if teams and print_notes and finished['status'] == 'candidate_ready':
                        results = []
                        failed = []
                        for entry in print_notes:
                            try:
                                results.append(run_print_team(workspace, pilot_root, entry,
                                                              runner=runner))
                            except Exception as exc:
                                results.append({'status': 'failed', 'error':
                                                f'{type(exc).__name__}: {exc}'})
                                failed.append(entry['sha256'])
                        state['pendingPrintNotes'] = failed
                        state['printTeam'] = {'ok': not failed, 'results': results, 'run': run_id}
                    elif teams and print_notes:
                        state['pendingPrintNotes'] = [entry['sha256'] for entry in print_notes]
                        state['printTeam'] = {'ok': False, 'run': run_id,
                                              'results': [{'status': 'pending',
                                                           'summary': 'Design changes must pass before print coordination'}]}
                    elif teams and not contract_run and finished['status'] == 'candidate_ready':
                        from .parallel_print import _case_snapshot, initial_print_note
                        try:
                            if _case_snapshot(workspace) is None:
                                result = {'status': 'unavailable',
                                          'summary': 'Verified print project is not available yet'}
                            else:
                                result = run_print_team(workspace, pilot_root,
                                                        initial_print_note(pilot_root), runner=runner)
                        except Exception as exc:
                            result = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}
                            state['pendingPrintNotes'] = [finished['contractSha256']]
                        state['printTeam'] = {'ok': result['status'] != 'failed',
                                              'results': [result], 'run': run_id}
                    if finished['status'] in ('model_unavailable', 'execution_limit'):
                        return state  # capacity or time limits cannot be repaired by replanning
                    if finished['status'] == 'candidate_ready' or mirror_errors or attempt + 1 == max_rounds:
                        return state
                    if finished['status'] != 'needs_revision' or finished.get('replanRequired') is not True:
                        return state
                    state.update(status='running', stage='revising', error=None,
                                 replanRequired=False)
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
            preview = _read_preview(workspace, run_id)
            preview['runStatus'] = state['status']
            preview['updatedAt'] = time.time()
            _write_preview(workspace, preview)


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


def _question_evidence(workspace, view, pilot=None, pilot_state=None, _attempt=0):
    """Give the lead a bounded, read-only snapshot of real project evidence."""
    source = pilot / 'workspace' if pilot is not None and (pilot / 'workspace').is_dir() else workspace
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Question source workspace is not a real directory')
    committed = source != workspace and (source / '.git').exists()
    head_before = parallel.git(source, 'rev-parse', 'HEAD') if committed else None
    dirty_before = bool(parallel.git(source, 'status', '--porcelain')) if committed else False
    prefixes = {prefix for group in OWNED.values() for prefix in group}
    candidates = []
    for prefix in sorted(prefixes | {'prompts/assets/', 'references/'}):
        if prefix.endswith('/'):
            files = _tree_files(source, prefix[:-1])
        else:
            file = _safe_target(source, prefix)
            files = (file,) if file.exists() else ()
        for file in files:
            candidates.append((file.relative_to(source).as_posix(), file))
            if len(candidates) > 10000:
                raise ValueError('Question evidence has too many project files')
    if source != workspace:
        for directory in ('prompts/assets', 'references'):
            for file in _tree_files(workspace, directory):
                candidates.append(('current-attachments/' +
                                   file.relative_to(workspace).as_posix(), file))
                if len(candidates) > 10000:
                    raise ValueError('Question evidence has too many project files')
    # Physical confirmations are appended to the visible project after the
    # integrated design checkpoint. Prefer that canonical ledger over a prior
    # pilot's copied input, and verify its chain before showing it to the lead.
    ledger = workspace / 'print/events.jsonl'
    if ledger.exists() and isinstance(pilot_state, dict) and pilot_state.get('contractSha256'):
        from .parallel_print import _read_ledger
        _read_ledger(workspace, pilot_state['contractSha256'])
        candidates.append(('print/events.jsonl', ledger))
    if pilot is not None and isinstance(pilot_state, dict) and pilot_state.get('contractSha256'):
        from .parallel_addendum import _asset_directory, load_addenda
        chain = load_addenda(pilot, pilot_state['contractSha256'])
        for entry in chain['entries']:
            for relative in entry.get('assets', {}):
                file = _asset_directory(pilot, entry['sequence'], entry['sha256']) / relative
                candidates.append((f"sealed-note-assets/n{entry['sequence']:04}/{relative}", file))
                if len(candidates) > 10000:
                    raise ValueError('Question evidence has too many project files')
    priority = ('print/', 'design/', 'firmware/', 'case/', 'engineering/', 'sealed-note-assets/',
                'current-attachments/', 'references/', 'prompts/assets/', 'boards/',
                'tools/', 'build/')
    def evidence_order(item):
        name = item[0]
        group = next((index for index, prefix in enumerate(priority)
                      if name.startswith(prefix)), len(priority))
        note = -int(name.split('/')[1][1:]) if name.startswith('sealed-note-assets/') else 0
        return group, note, name
    names = sorted(dict(candidates).items(),
                   key=evidence_order)
    manifest = {'source': 'active-pilot' if source != workspace else 'visible-workspace',
                'files': [], 'omitted': 0, 'racing': [],
                'limits': {'maxFiles': QUESTION_MAX_FILES,
                           'maxFileBytes': QUESTION_MAX_FILE,
                           'maxTotalBytes': QUESTION_MAX_TOTAL}}
    total = 0
    for name, file in names:
        try:
            if file.is_symlink() or not file.is_file() or file.stat().st_nlink != 1:
                raise ValueError(f'Invalid question evidence file: {name}')
            size = file.stat().st_size
        except FileNotFoundError:
            manifest['racing'].append(name)
            continue
        if (Path(name).suffix.lower() not in QUESTION_SUFFIXES or
                size > QUESTION_MAX_FILE or total + size > QUESTION_MAX_TOTAL or
                len(manifest['files']) >= QUESTION_MAX_FILES):
            manifest['omitted'] += 1
            continue
        destination = view / 'PROJECT_ARTIFACTS' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            before = file.stat()
            shutil.copyfile(file, destination)
            after = file.stat()
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                     after.st_ctime_ns) or _sha(file) != _sha(destination)):
                raise ValueError('Changed during snapshot')
        except (OSError, ValueError):
            destination.unlink(missing_ok=True)
            manifest['racing'].append(name)
            continue
        manifest['files'].append({'path': name, 'bytes': size, 'sha256': _sha(destination)})
        total += size
    verdict = _safe_target(source, VERDICT, verdict=True)
    if verdict.exists():
        try:
            info = _file_info(verdict, maximum=QUESTION_MAX_FILE)
            data = json.loads(verdict.read_text())
            if _sha(verdict) != info['sha256']:
                raise ValueError('Native verdict changed while reading')
            parallel.write(view / 'NATIVE_VERDICT.json', data)
        except (OSError, ValueError, json.JSONDecodeError):
            manifest['racing'].append(VERDICT)
    if pilot is not None:
        checks = []
        rounds = pilot / '.pilot/rounds'
        if rounds.is_symlink():
            raise ValueError('Pilot round checks path is a symlink')
        if rounds.is_dir():
            files = sorted(rounds.glob('*/*-check.json'))[-16:]
            for file in files:
                if file.is_symlink() or file.parent.is_symlink():
                    raise ValueError('Invalid native check evidence')
                try:
                    if file.stat().st_size > QUESTION_MAX_FILE:
                        raise ValueError('Oversized native check evidence')
                    checks.append({'path': file.relative_to(pilot).as_posix(),
                                   'result': json.loads(file.read_text())})
                except (FileNotFoundError, json.JSONDecodeError):
                    manifest['racing'].append(file.relative_to(pilot).as_posix())
        parallel.write(view / 'NATIVE_CHECKS.json', checks)
        if isinstance(pilot_state, dict):
            jobs = pilot_state.get('jobs', [])
            if isinstance(jobs, list):
                recent = []
                for job in jobs[-20:]:
                    if not isinstance(job, dict):
                        continue
                    recent.append({key: job.get(key) for key in
                                   ('stage', 'model', 'seconds', 'error', 'failureKind')})
                parallel.write(view / 'RECENT_JOBS.json', recent)
    head_after = parallel.git(source, 'rev-parse', 'HEAD') if committed else None
    dirty_after = bool(parallel.git(source, 'status', '--porcelain')) if committed else False
    unstable = head_before != head_after or dirty_before or dirty_after
    if unstable and _attempt == 0:
        shutil.rmtree(view / 'PROJECT_ARTIFACTS', ignore_errors=True)
        for name in ('PROJECT_EVIDENCE.json', 'NATIVE_VERDICT.json',
                     'NATIVE_CHECKS.json', 'RECENT_JOBS.json'):
            (view / name).unlink(missing_ok=True)
        return _question_evidence(workspace, view, pilot, pilot_state, _attempt=1)
    if unstable:
        shutil.rmtree(view / 'PROJECT_ARTIFACTS', ignore_errors=True)
        (view / 'NATIVE_VERDICT.json').unlink(missing_ok=True)
        (view / 'NATIVE_CHECKS.json').unlink(missing_ok=True)
        (view / 'RECENT_JOBS.json').unlink(missing_ok=True)
        manifest['files'] = []
        manifest['unstableSource'] = True
    else:
        manifest['sourceHead'] = head_after
    parallel.write(view / 'PROJECT_EVIDENCE.json', manifest)


def ask(workspace, prompt_file, *, execute=False, runner=None):
    """Route a read-only project question to the lead, even before planning."""
    if not execute:
        raise ValueError('Lead model execution requires explicit --execute')
    workspace = Path(workspace).resolve(strict=True)
    prompt_file = Path(prompt_file)
    if not workspace.is_dir() or not prompt_file.is_absolute() or not prompt_file.is_file():
        raise ValueError('Expected an existing workspace and an absolute question file')
    question = prompt_file.read_text()
    if not question.strip() or len(question) > 100000:
        raise ValueError('Expected a bounded nonempty question')
    bridge = _read_state(workspace)
    questions = _control_path(workspace, 'team-questions')
    questions.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='lead-', dir=questions))
    control = root / '.pilot'
    control.mkdir()
    (control / 'question.txt').write_text(question)
    tool_root = Path(__file__).resolve().parents[4]
    skills = tool_root / 'harness/kicad/skills'
    shutil.copytree(skills, control / 'instructions/skills')
    view = root / 'view'
    view.mkdir()
    (view / 'AGENTS.md').write_text((tool_root / 'harness/kicad/AGENTS.md').read_text())
    for name in ('product.json', 'project.json'):
        file = workspace / name
        if file.is_file() and not file.is_symlink() and file.stat().st_size <= 1_000_000:
            shutil.copy2(file, view / name)
    context = {'bridge': {key: bridge.get(key) for key in
                          ('status', 'stage', 'activeRun', 'contractRun', 'teamReady',
                           'error', 'lastCheckpointStage', 'printTeam',
                           'pendingPrintNotes')}}
    run_id = bridge.get('activeRun')
    pilot = None
    pilot_state = None
    if run_id:
        pilot = _control_path(workspace, 'team-runs', run_id)
        state_file = pilot / '.pilot/state.json'
        if state_file.is_file() and not state_file.is_symlink():
            pilot_state = json.loads(state_file.read_text())
            context['pilot'] = {key: pilot_state.get(key) for key in
                                ('status', 'findings', 'revision', 'contractSha256',
                                 'assignedRoles', 'releaseNoteTipSha256')}
            if pilot_state.get('contractFile'):
                from .parallel_addendum import load_addenda
                contract_path = (pilot / pilot_state['contractFile']).resolve()
                if not contract_path.is_relative_to(pilot / '.pilot/contracts'):
                    raise ValueError('Invalid active contract path')
                contract = json.loads(contract_path.read_text())
                if parallel.digest(contract) != pilot_state['contractSha256']:
                    raise ValueError('Active contract changed')
                parallel.write(view / 'PILOT_CONTRACT.json', contract)
                (view / 'PILOT_BRIEF.md').write_text((pilot / '.pilot/prompt.txt').read_text())
                notes = load_addenda(pilot, pilot_state['contractSha256'])
                parallel.write(view / 'PILOT_RELEASE_NOTES.json', notes)
                context['releaseNoteTipSha256'] = notes['tipSha256']
    _question_evidence(workspace, view, pilot, pilot_state)
    from .parallel_print import LEDGER, _account_snapshot, _local_account_snapshot, _read_ledger
    contract_sha = context.get('pilot', {}).get('contractSha256')
    if contract_sha and (workspace / LEDGER).exists():
        events = _read_ledger(workspace, contract_sha)
        instances = {}
        for event in events:
            if event['kind'] != 'user_confirmation':
                continue
            for part in event['parts']:
                for instance_id in part['instanceIds']:
                    instances[instance_id] = (part['name'], part['revision'],
                                              part['sha256STL'], event['status'])
        inventory = {}
        for name, revision, sha, reported_status in instances.values():
            if reported_status not in ('printed', 'fit'):
                continue
            key = (name, revision, sha)
            counts = inventory.setdefault(key, {'name': name, 'revision': revision,
                                                 'sha256STL': sha, 'reportedPrinted': 0,
                                                 'reportedFit': 0})
            counts['reportedPrinted'] += 1
            counts['reportedFit'] += reported_status == 'fit'
        parallel.write(view / 'PRINT_INVENTORY.json', {
            'status': 'verified_user_reports', 'contractSha256': contract_sha,
            'eventCount': len(events), 'recentEvents': events[-20:],
            'parts': sorted(inventory.values(), key=lambda item: (item['name'],
                                                                  item['revision'],
                                                                  item['sha256STL'])),
            'note': 'User-reported physical instances only; compare STL hashes with current case before claiming current inventory.'
        })
    else:
        parallel.write(view / 'PRINT_INVENTORY.json', {'status': 'unavailable',
                                                        'eventCount': 0, 'parts': []})
    try:
        print_account = _account_snapshot(_local_account_snapshot())
    except ValueError:
        print_account = _account_snapshot(None)
    parallel.write(view / 'PRINT_ACCOUNT_STATUS.json', print_account)
    parallel.write(view / 'TEAM_STATUS.json', context)
    before = parallel.snapshot(view)
    runtime = {'leadModel': parallel.LEAD, 'workerModel': parallel.WORKER,
               'toolchainRoot': str(tool_root), 'planAttempts': 0, 'revision': 0,
               'timeoutSeconds': 1800, 'jobs': []}
    schema = obj({'answer': STRING})
    request = ('You are the LEAD and the sole user-facing interface for this KiCad project. '
               'Answer the user question from TEAM_STATUS.json, PROJECT_EVIDENCE.json, '
               'PROJECT_ARTIFACTS, PRINT_INVENTORY.json, PRINT_ACCOUNT_STATUS.json, NATIVE_CHECKS.json, '
               'NATIVE_VERDICT.json when present, '
               'the original contract, release notes, and original user brief. Inspect the '
               'actual relevant files before answering. Be direct and state uncertainty '
               'when evidence is missing. The current LAN printer status does not prove '
               'a physical part was printed or fits; only an explicit user confirmation '
               'in the verified print/events.jsonl may support that claim. '
               'This is read-only: do not edit files, create a contract or release note, '
               'delegate, start workers, or claim a physical test.\n\nUSER QUESTION:\n' + question)
    runner = runner or parallel.codex
    for attempt, delay in enumerate((0, *parallel.CAPACITY_RETRY_DELAYS), 1):
        if delay:
            time.sleep(delay)
        receipt = runner(root, runtime, 'ask', runtime['leadModel'], view,
                         request, schema, True)
        if parallel.snapshot(view) != before:
            raise ValueError('Read-only lead changed its question context')
        if not receipt.get('error'):
            result = receipt.get('result')
            validate(result, schema)
            return {'ok': True, 'answer': result['answer'], 'run': run_id,
                    'contractSha256': context.get('pilot', {}).get('contractSha256')}
        if receipt.get('failureKind') != 'model_capacity':
            raise ValueError(parallel.job_error(receipt))
    raise parallel.ModelCapacityExhausted(parallel.job_error(receipt))


def note(workspace, prompt_file, *, execute=False, runner=None):
    """Let the lead record a new user change while a team run is active."""
    if not execute:
        raise ValueError('Lead model execution requires explicit --execute')
    workspace = Path(workspace).resolve(strict=True)
    prompt_file = Path(prompt_file)
    if not prompt_file.is_absolute() or not prompt_file.is_file():
        raise ValueError('Expected an absolute user message file')
    bridge = _read_state(workspace)
    if bridge.get('status') != 'running':
        raise ValueError('Use note only during an active run; use start for a later change')
    run_id = bridge.get('activeRun')
    if not run_id or not bridge.get('contractRun'):
        raise ValueError('No original sealed team contract; use start for the first build')
    from .parallel_addendum import record_addendum
    pilot = _control_path(workspace, 'team-runs', run_id)
    entry = record_addendum(pilot, prompt_file, runner=runner,
                            source_assets=workspace)
    return {'ok': True, 'run': run_id, 'noteSha256': entry['sha256'],
            'sequence': entry['sequence'], 'routes': entry['routes'],
            'queuedForResume': bridge.get('status') == 'running'}


def resume(workspace, *, execute=False, runner=None, checker=None):
    """Continue a bounded team run with its same contract and queued notes."""
    workspace = Path(workspace).resolve(strict=True)
    bridge = _read_state(workspace)
    contract_run = bridge.get('contractRun')
    if not contract_run:
        raise ValueError('No original sealed team contract to resume')
    if bridge.get('status') == 'candidate_ready' and not bridge.get('pendingPrintNotes'):
        raise ValueError('The team is already ready; use start for a new user change')
    original = _control_path(workspace, 'team-runs', contract_run, '.pilot', 'prompt.txt')
    return start(workspace, original, execute=execute, runner=runner,
                 checker=checker, teams=True, resume_notes=True)


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
    ask_cmd = commands.add_parser('ask')
    ask_cmd.add_argument('workspace')
    ask_cmd.add_argument('--prompt', required=True)
    ask_cmd.add_argument('--execute', action='store_true')
    note_cmd = commands.add_parser('note')
    note_cmd.add_argument('workspace')
    note_cmd.add_argument('--prompt', required=True)
    note_cmd.add_argument('--execute', action='store_true')
    resume_cmd = commands.add_parser('resume')
    resume_cmd.add_argument('workspace')
    resume_cmd.add_argument('--execute', action='store_true')
    sync_cmd = commands.add_parser('sync')
    sync_cmd.add_argument('workspace')
    args = parser.parse_args(argv)
    try:
        if args.command == 'start':
            result = start(args.workspace, args.prompt, execute=args.execute,
                           max_rounds=args.max_rounds, teams=True)
        elif args.command == 'sync':
            result = sync(args.workspace)
        elif args.command == 'ask':
            result = ask(args.workspace, args.prompt, execute=args.execute)
        elif args.command == 'note':
            result = note(args.workspace, args.prompt, execute=args.execute)
        elif args.command == 'resume':
            result = resume(args.workspace, execute=args.execute)
        else:
            result = status(args.workspace)
        print_ok = result.get('printTeam') is None or result['printTeam'].get('ok') is not False
        ok = (args.command in ('sync', 'ask', 'note') or
              result['status'] in ('candidate_ready', 'initialized', 'planned', 'idle')) and print_ok
        print(json.dumps({'ok': ok, **result}, sort_keys=True))
        return 0 if ok else 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
