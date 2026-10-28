"""Append-only user instructions routed by the lead after the first contract.

The sealed PILOT_CONTRACT.json is never rewritten. Each user message is kept
verbatim in a hash-chained release note, and only its affected roles receive
the lead's routing when they start or resume work.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time

from .parallel_contract import CONTRACT_SCHEMA, ROLES, STRING, array, digest, encoded, obj, validate


PRINT_ROLE = 'print'
LEAD_ROUTES = (*ROLES, PRINT_ROLE)


ADDENDUM_SCHEMA = obj({
    'routes': array({'type': 'string', 'enum': list(LEAD_ROUTES)}),
    'note': STRING,
    'changedFields': array({'type': 'string', 'enum': list(CONTRACT_SCHEMA['properties'])}),
})
MAX_USER_MESSAGE_BYTES = 100_000
MAX_REFERENCE_FILES = 128
MAX_REFERENCE_DIRECTORIES = 256
MAX_REFERENCE_FILE_BYTES = 16 * 1024 * 1024
MAX_REFERENCE_TOTAL_BYTES = 64 * 1024 * 1024
REFERENCE_DIRECTORIES = ('prompts/assets', 'references')


@contextmanager
def _addendum_lock(root):
    """Serialize messages without taking the controller's long running job lock."""
    with (root / '.pilot/addenda.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _note_path(root, sequence, sha):
    return root / '.pilot/addenda' / f'n{sequence:04}-{sha}.json'


def _asset_directory(root, sequence, sha):
    return root / '.pilot/addenda/assets' / f'n{sequence:04}-{sha}'


def _valid_reference_name(relative):
    if not isinstance(relative, str):
        return False
    parts = relative.split('/')
    allowed = ((len(parts) >= 3 and parts[:2] == ['prompts', 'assets']) or
               (len(parts) >= 2 and parts[0] == 'references'))
    return allowed and all(
        part not in ('', '.', '..', '.git') and '\\' not in part and
        not any(ord(char) < 32 or ord(char) == 127 for char in part)
        for part in parts)


def _verify_asset_manifest(assets):
    if not isinstance(assets, dict) or len(assets) > MAX_REFERENCE_FILES:
        raise ValueError('Invalid release-note reference manifest')
    total = 0
    for relative, info in assets.items():
        if not _valid_reference_name(relative) or not isinstance(info, dict) or set(info) != {'sha256', 'bytes'}:
            raise ValueError('Invalid release-note reference path or details')
        size, sha = info['bytes'], info['sha256']
        if (type(size) is not int or size < 0 or size > MAX_REFERENCE_FILE_BYTES or
                not isinstance(sha, str) or len(sha) != 64):
            raise ValueError('Invalid release-note reference size or hash')
        try:
            bytes.fromhex(sha)
        except ValueError as exc:
            raise ValueError('Invalid release-note reference hash') from exc
        total += size
        if total > MAX_REFERENCE_TOTAL_BYTES:
            raise ValueError('Release-note references exceed bounds')


def _read_note_assets(root, entry):
    """Verify every sealed byte, and reject unlisted files in the note snapshot."""
    assets = entry.get('assets', {})
    if not assets:
        return {}
    source = _asset_directory(root, entry['sequence'], entry['sha256'])
    if source.parent.is_symlink() or source.is_symlink() or not source.is_dir():
        raise ValueError('Release-note reference snapshot is missing or linked')
    expected_directories = set()
    for relative in assets:
        parts = relative.split('/')
        expected_directories.update('/'.join(parts[:index]) for index in range(1, len(parts)))
    found = set()
    directories = 0

    def walk_error(error):
        raise error

    for current, dirs, names in os.walk(source, followlinks=False, onerror=walk_error):
        directories += 1
        if directories > MAX_REFERENCE_DIRECTORIES:
            raise ValueError('Release-note reference snapshot exceeds bounds')
        for name in dirs:
            path = Path(current) / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink() or relative not in expected_directories:
                raise ValueError(f'Unsafe release-note reference directory: {path}')
        for name in names:
            path = Path(current) / name
            relative = path.relative_to(source).as_posix()
            if path.is_symlink() or relative not in assets:
                raise ValueError(f'Unlisted release-note reference: {relative}')
            found.add(relative)
    if found != set(assets):
        raise ValueError('Release-note reference snapshot is incomplete')
    result = {}
    for relative, info in assets.items():
        data = _read_reference(source, relative)
        if len(data) != info['bytes'] or hashlib.sha256(data).hexdigest() != info['sha256']:
            raise ValueError(f'Release-note reference hash changed: {relative}')
        result[relative] = data
    return result


def load_addenda(directory, contract_sha256=None):
    """Verify and return the complete ordered chain and its deterministic tip.

    An empty chain is anchored to the base contract hash. The caller may pass
    a known contract hash to avoid reloading mutable controller state.
    """
    from . import parallel

    root = Path(directory).resolve()
    if contract_sha256 is None:
        root, state = parallel.load(root)
        contract_sha256 = state.get('contractSha256')
    if not isinstance(contract_sha256, str) or len(contract_sha256) != 64:
        raise ValueError('A sealed contract is required before release notes')
    try:
        bytes.fromhex(contract_sha256)
    except ValueError as exc:
        raise ValueError('Invalid sealed contract hash') from exc
    directory = root / '.pilot/addenda'
    if directory.is_symlink():
        raise ValueError('Release note directory cannot be a symlink')
    paths = sorted(directory.glob('n*.json')) if directory.is_dir() else []
    entries = []
    previous = contract_sha256
    for sequence, path in enumerate(paths, 1):
        if path.is_symlink():
            raise ValueError('Release note cannot be a symlink')
        raw = path.read_bytes()
        entry = json.loads(raw)
        if not isinstance(entry, dict):
            raise ValueError('Invalid release note')
        fields = {'schema', 'sequence', 'contractSha256', 'previousSha256',
                  'userTextSha256', 'userText', 'routes', 'note', 'changedFields', 'sha256'}
        schema = entry.get('schema')
        if schema == 2:
            fields.add('assets')
        if set(entry) != fields or schema not in (1, 2) or type(entry['sequence']) is not int:
            raise ValueError('Invalid release note fields')
        if raw != encoded(entry):
            raise ValueError('Release note bytes are not canonical')
        unsigned = {key: value for key, value in entry.items() if key != 'sha256'}
        result = {key: entry[key] for key in ('routes', 'note', 'changedFields')}
        validate(result, ADDENDUM_SCHEMA)
        if not entry['routes'] or len(entry['routes']) != len(set(entry['routes'])):
            raise ValueError('Release note needs unique responsible teams')
        if len(entry['changedFields']) != len(set(entry['changedFields'])):
            raise ValueError('Release note repeats a changed contract field')
        if schema == 2:
            _verify_asset_manifest(entry['assets'])
        user_text = entry['userText']
        if (not isinstance(user_text, str) or not user_text.strip() or
                len(user_text.encode('utf-8')) > MAX_USER_MESSAGE_BYTES or '\x00' in user_text):
            raise ValueError('Invalid exact user message')
        if (entry['sequence'] != sequence or entry['contractSha256'] != contract_sha256 or
                entry['previousSha256'] != previous or
                entry['userTextSha256'] != hashlib.sha256(user_text.encode('utf-8')).hexdigest() or
                entry['sha256'] != digest(unsigned) or
                path != _note_path(root, sequence, entry['sha256'])):
            raise ValueError('Release note chain was changed or reordered')
        _read_note_assets(root, entry)
        previous = entry['sha256']
        entries.append(entry)
    return {'entries': entries, 'tipSha256': previous}


def _reference_files(workspace):
    """List only bounded regular files below the two known attachment roots."""
    if workspace.is_symlink() or not workspace.is_dir():
        raise ValueError('Pilot workspace is not a real directory')
    files, total, directories = [], 0, 0

    def walk_error(error):
        raise error

    for directory in REFERENCE_DIRECTORIES:
        source = workspace
        for part in directory.split('/'):
            source = source / part
            if source.is_symlink():
                raise ValueError(f'Symlink in pilot reference path: {directory}')
        if not source.exists():
            continue
        if not source.is_dir():
            raise ValueError(f'Expected pilot reference directory: {directory}')
        for current, dirs, names in os.walk(source, followlinks=False, onerror=walk_error):
            directories += 1
            if directories > MAX_REFERENCE_DIRECTORIES:
                raise ValueError('Pilot references exceed lead-view directory bounds')
            for name in dirs + names:
                path = Path(current) / name
                if path.is_symlink():
                    raise ValueError(f'Symlink in pilot reference path: {path}')
                if (name in ('.', '..', '.git') or '/' in name or '\\' in name or
                        any(ord(char) < 32 or ord(char) == 127 for char in name)):
                    raise ValueError(f'Unsafe pilot reference name: {name}')
            for name in names:
                path = Path(current) / name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError(f'Pilot reference is not an ordinary file: {path}')
                if info.st_size > MAX_REFERENCE_FILE_BYTES:
                    raise ValueError(f'Pilot reference file exceeds lead-view limit: {path}')
                total += info.st_size
                if total > MAX_REFERENCE_TOTAL_BYTES or len(files) >= MAX_REFERENCE_FILES:
                    raise ValueError('Pilot references exceed lead-view bounds')
                files.append(path.relative_to(workspace).as_posix())
    return sorted(files)


def _read_reference(workspace, relative):
    """Open each path component without following links, including during copy."""
    parts = relative.split('/')
    if not _valid_reference_name(relative):
        raise ValueError(f'Unsafe pilot reference path: {relative}')
    directory = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=directory)
            os.close(directory)
            directory = following
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_size > MAX_REFERENCE_FILE_BYTES):
                raise ValueError(f'Pilot reference is not an ordinary bounded file: {relative}')
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                data = stream.read(MAX_REFERENCE_FILE_BYTES + 1)
            after = os.fstat(descriptor)
            if (len(data) > MAX_REFERENCE_FILE_BYTES or len(data) != before.st_size or
                    (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                raise ValueError(f'Pilot reference changed while copying: {relative}')
            return data
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _reference_bytes(workspace):
    """Capture one consistent, bounded set of attachment bytes."""
    result, total = {}, 0
    for relative in _reference_files(workspace):
        data = _read_reference(workspace, relative)
        total += len(data)
        if total > MAX_REFERENCE_TOTAL_BYTES:
            raise ValueError('Pilot references exceed lead-view bounds')
        result[relative] = data
    return result


def _copy_references(references, destination):
    """Write verified copies and a manifest into an isolated snapshot."""
    manifest, total = {}, 0
    if len(references) > MAX_REFERENCE_FILES:
        raise ValueError('Pilot references exceed lead-view bounds')
    for relative, data in sorted(references.items()):
        if not _valid_reference_name(relative) or not isinstance(data, bytes):
            raise ValueError(f'Invalid pilot reference: {relative}')
        total += len(data)
        if len(data) > MAX_REFERENCE_FILE_BYTES or total > MAX_REFERENCE_TOTAL_BYTES:
            raise ValueError('Pilot references exceed lead-view bounds')
        expected = hashlib.sha256(data).hexdigest()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            raise ValueError(f'Pilot reference copy failed verification: {relative}')
        manifest[relative] = {'sha256': expected, 'bytes': len(data)}
    (destination / 'PILOT_REFERENCE_ASSETS.json').write_text(
        json.dumps({'schema': 1, 'files': manifest}, ensure_ascii=False, sort_keys=True,
                   indent=2) + '\n', encoding='utf-8')
    return manifest


def _lead_view(root, state, entries, destination, references=None):
    """Give the lead a stable, read-only view while workers edit elsewhere."""
    from . import parallel

    source = root / '.pilot/instructions/AGENTS.md'
    (destination / 'AGENTS.md').write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
    for name, value in (
        ('PILOT_BRIEF.md', (root / '.pilot/prompt.txt').read_text(encoding='utf-8')),
        ('PILOT_CONTRACT.json', (root / 'workspace/PILOT_CONTRACT.json').read_text(encoding='utf-8')),
        ('PILOT_RELEASE_NOTES.json', json.dumps(entries, ensure_ascii=False, indent=2)),
    ):
        (destination / name).write_text(value, encoding='utf-8')
    references = _copy_references(
        _reference_bytes(root / 'workspace') if references is None else references, destination)
    parallel.git(destination, 'init', '-q', '-b', 'lead')
    parallel.git(destination, 'add', '--', 'AGENTS.md', 'PILOT_BRIEF.md',
                 'PILOT_CONTRACT.json', 'PILOT_RELEASE_NOTES.json',
                 'PILOT_REFERENCE_ASSETS.json', *references)
    parallel.git(destination, '-c', 'user.name=KiCad Lead', '-c', 'user.email=lead@localhost',
                 'commit', '-qm', 'Snapshot lead inputs')
    return references


def _render_release_notes(contract_sha256, entries):
    lines = ['# Pilot release notes', '', f'Base contract SHA-256: `{contract_sha256}`', '',
             'The base contract is immutable. New requirements are additive. Later numbered '
             'notes supersede earlier notes only for the named fields; all other base fields '
             'remain in force.', '']
    for entry in entries:
        lines.extend((f"## {entry['sequence']}. {', '.join(entry['routes'])}", '',
                      f"Note SHA-256: `{entry['sha256']}`", '',
                      f"Changed contract fields: {', '.join(entry['changedFields']) or 'none'}", '',
                      'User message (verbatim):', '', '```text', entry['userText'], '```', '',
                      'Lead routing note:', '', entry['note'], ''))
        for relative, info in entry.get('assets', {}).items():
            lines.extend((f"Reference `{relative}`: SHA-256 `{info['sha256']}`", ''))
    return '\n'.join(lines)


def notes_for_role(directory, role):
    """Return verified, applicable release notes for worker and tester prompts."""
    if role not in LEAD_ROUTES:
        raise ValueError('Unknown team role')
    chain = load_addenda(directory)
    entries = [entry for entry in chain['entries'] if role in entry['routes']]
    if not entries:
        return ''
    lines = [f'RELEASE NOTE CHAIN TIP: {chain["tipSha256"]}',
             'The original sealed contract remains the baseline. Apply these notes in sequence. '
             'New requirements are additive. A later note supersedes an earlier note and the '
             'base contract only for its named changed fields. Keep all unrelated contract '
             'requirements. Never rewrite the base '
             'contract. If a conflict cannot be resolved from the note, ask the lead.']
    for entry in entries:
        lines.extend((f'\nRELEASE NOTE {entry["sequence"]} ({entry["sha256"]})',
                      f'Changed fields: {json.dumps(entry["changedFields"])}',
                      'Exact user message:\n' + entry['userText'],
                      'Lead routing:\n' + entry['note']))
        for relative, info in entry.get('assets', {}).items():
            path = _asset_directory(Path(directory).resolve(), entry['sequence'],
                                    entry['sha256']) / relative
            lines.append(f'Immutable reference {relative}: {path} (SHA-256 {info["sha256"]})')
    return '\n'.join(lines)


def carry_addenda(source_root, target_root):
    """Carry the exact verified release-note chain into a fresh adopted run.

    The target must already have adopted the same revision-one contract. No
    model is called, and the original contract is not touched in either run.
    """
    from . import parallel

    source_root = Path(source_root).resolve()
    target_root = Path(target_root).resolve()
    if source_root == target_root:
        raise ValueError('Source and target runs must differ')
    with ExitStack() as locks:
        for root in sorted((source_root, target_root)):
            locks.enter_context(_addendum_lock(root))
        source_root, source = parallel.load(source_root)
        target_root, target = parallel.load(target_root)
        if (source.get('revision') != 1 or target.get('revision') != 1 or
                not source.get('contractFile') or not target.get('contractFile') or
                source['contractSha256'] != target['contractSha256']):
            raise ValueError('Release notes require the exact same original contract')
        source_chain = load_addenda(source_root, source['contractSha256'])
        target_chain = load_addenda(target_root, target['contractSha256'])
        if target_chain['entries']:
            if target_chain != source_chain:
                raise ValueError('Target already has different release notes')
            return target_chain
        target_directory = target_root / '.pilot/addenda'
        if target_directory.is_symlink():
            raise ValueError('Target release note directory cannot be a symlink')
        with tempfile.TemporaryDirectory(prefix='addenda-copy-', dir=target_root / '.pilot') as stage_name:
            stage = Path(stage_name)
            for entry in source_chain['entries']:
                name = _note_path(source_root, entry['sequence'], entry['sha256']).name
                (stage / name).write_bytes((source_root / '.pilot/addenda' / name).read_bytes())
                for relative, data in _read_note_assets(source_root, entry).items():
                    copied_asset = (stage / 'assets' / name.removesuffix('.json') / relative)
                    copied_asset.parent.mkdir(parents=True, exist_ok=True)
                    copied_asset.write_bytes(data)
                    info = entry['assets'][relative]
                    if (len(copied_asset.read_bytes()) != info['bytes'] or
                            hashlib.sha256(copied_asset.read_bytes()).hexdigest() != info['sha256']):
                        raise ValueError(f'Copied release-note reference failed verification: {relative}')
            if target_directory.exists():
                if any(target_directory.iterdir()):
                    raise ValueError('Target release note directory is not empty')
                target_directory.rmdir()
            # Rename a complete directory so consumers never see a partial chain.
            os.replace(stage, target_directory)
        copied = load_addenda(target_root, target['contractSha256'])
        if copied != source_chain:
            shutil.rmtree(target_directory)
            raise ValueError('Copied release note chain failed verification')
        (target_root / '.pilot/RELEASE_NOTES.md').write_text(
            _render_release_notes(target['contractSha256'], copied['entries']), encoding='utf-8')
        return copied


def record_addendum(directory, user_prompt_path, *, runner=None, source_assets=None):
    """Ask the lead to route one exact follow-up message; append one sealed note.

    This entry point is independent of the team's long-running lock so a user
    can address the lead while board, firmware, and case jobs are in flight.
    """
    from . import parallel

    raw = Path(user_prompt_path).read_bytes()
    if len(raw) > MAX_USER_MESSAGE_BYTES:
        raise ValueError('User message exceeds release note limit')
    user_text = raw.decode('utf-8')
    if not user_text.strip() or '\x00' in user_text:
        raise ValueError('Expected a nonempty exact user message')
    root, state = parallel.load(directory)
    if state.get('revision') != 1 or not state.get('contractFile'):
        raise ValueError('Create the one original contract before recording release notes')
    runner = runner or parallel.codex
    with _addendum_lock(root):
        root, state = parallel.load(root)
        if state.get('revision') != 1 or not state.get('contractFile'):
            raise ValueError('The original contract has changed')
        if state.get('status') == 'candidate_ready':
            raise ValueError('Team run already finalized; submit this change to the next run')
        chain = load_addenda(root, state['contractSha256'])
        sequence = len(chain['entries']) + 1
        pilot_assets = _reference_bytes(root / 'workspace')
        lead_assets = dict(pilot_assets)
        for previous in chain['entries']:
            lead_assets.update(_read_note_assets(root, previous))
        source_assets = Path(source_assets) if source_assets is not None else None
        new_assets = (_reference_bytes(source_assets) if source_assets is not None
                      else dict(pilot_assets))
        lead_assets.update(new_assets)
        with tempfile.TemporaryDirectory(prefix='lead-note-', dir=root / '.pilot') as temporary:
            view = Path(temporary)
            references = _lead_view(root, state, chain['entries'], view, lead_assets)
            before = parallel.snapshot(view)
            prompt = (
                'You are the LEAD receiving the user\'s next instruction for an active KiCad '
                'team. Read the original PILOT_CONTRACT.json and all earlier '
                'PILOT_RELEASE_NOTES.json. Do not edit files. Never regenerate or rewrite the '
                'contract. Read PILOT_REFERENCE_ASSETS.json and any relevant copied files in '
                'prompts/assets or references; their hashes were checked before this call. '
                f'{len(references)} reference file(s) are available in this isolated view. '
                'Treat the exact new user message below as the highest-priority '
                'product instruction. Write a concise release note and route it to every '
                'responsible team among board, firmware, case, and print. Route requests '
                'about starting or tracking a physical 3D print, printer account status, or '
                'what has actually been printed to print. Route CAD, geometry, slicer project '
                'or plate preparation changes to case as well. A print-only request does not '
                'rebuild board, firmware or case. For any baseline contract '
                'field changed by this message, name its top-level field in changedFields. '
                'The latest note supersedes earlier notes and the base contract only for '
                'those named fields. Leave unrelated requirements in force. Preserve uncertain '
                'facts as uncertainty; do not claim physical tests or completed CAD. '
                f'Return routes, note, changedFields.\n\nEXACT USER MESSAGE:\n{user_text}'
            )
            for attempt, delay in enumerate((0, *parallel.CAPACITY_RETRY_DELAYS), 1):
                if delay:
                    time.sleep(delay)
                receipt = runner(root, state, f'lead-addendum-{sequence:04}', state['leadModel'],
                                 view, prompt, ADDENDUM_SCHEMA, True)
                if parallel.snapshot(view) != before:
                    raise ValueError('Read-only lead changed its input view')
                if not receipt.get('error'):
                    break
                if receipt.get('failureKind') != 'model_capacity':
                    raise ValueError(parallel.job_error(receipt))
            else:
                raise parallel.ModelCapacityExhausted(parallel.job_error(receipt))
            result = receipt.get('result')
            validate(result, ADDENDUM_SCHEMA)
            if not result['routes'] or len(result['routes']) != len(set(result['routes'])):
                raise ValueError('Lead must name unique responsible teams')
            if len(result['changedFields']) != len(set(result['changedFields'])):
                raise ValueError('Lead repeated a changed contract field')
            if result['routes'] == [PRINT_ROLE] and result['changedFields']:
                raise ValueError('Print-only note cannot change a design contract field')
        _, current = parallel.load(root)
        if current['contractSha256'] != state['contractSha256']:
            raise ValueError('Contract changed while the lead routed the message')
        if current.get('status') == 'candidate_ready':
            raise ValueError('Team run finalized while the lead routed the message')
        if _reference_bytes(root / 'workspace') != pilot_assets or (
                source_assets is not None and _reference_bytes(source_assets) != new_assets):
            raise ValueError('Reference assets changed while the lead routed the message')
        asset_manifest = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                          for name, data in sorted(new_assets.items())}
        _verify_asset_manifest(asset_manifest)
        unsigned = {'schema': 2, 'sequence': sequence,
                    'contractSha256': state['contractSha256'],
                    'previousSha256': chain['tipSha256'],
                    'userTextSha256': hashlib.sha256(raw).hexdigest(),
                    'userText': user_text, 'routes': result['routes'],
                    'note': result['note'], 'changedFields': result['changedFields'],
                    'assets': asset_manifest}
        entry = {**unsigned, 'sha256': digest(unsigned)}
        path = _note_path(root, sequence, entry['sha256'])
        if path.exists():
            raise ValueError('Release note already exists')
        asset_directory = _asset_directory(root, sequence, entry['sha256'])
        if asset_directory.exists() or asset_directory.is_symlink():
            raise ValueError('Release-note reference snapshot already exists')
        if new_assets:
            asset_directory.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='asset-stage-', dir=root / '.pilot') as temporary:
                stage = Path(temporary)
                for relative, data in new_assets.items():
                    copied = stage / relative
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    copied.write_bytes(data)
                    if hashlib.sha256(copied.read_bytes()).hexdigest() != asset_manifest[relative]['sha256']:
                        raise ValueError(f'Release-note reference copy failed: {relative}')
                os.replace(stage, asset_directory)
        try:
            parallel.write(path, entry)
            # Validate the entry and its immutable attachments before returning it.
            verified = load_addenda(root, state['contractSha256'])
        except Exception:
            if not path.exists() and asset_directory.exists():
                shutil.rmtree(asset_directory)
            raise
        # The human-readable document is derived from the verified chain; JSON
        # entries, not Markdown, are authoritative for dispatch and integrity.
        (root / '.pilot/RELEASE_NOTES.md').write_text(
            _render_release_notes(state['contractSha256'], verified['entries']), encoding='utf-8')
        return entry
