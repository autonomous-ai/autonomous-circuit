"""Machine-run case checks: the team harness runs them after every case builder turn.

The case builder writes and updates its mechanical check scripts (joint sweeps, clearances,
wire routes) and declares them in `case/checks.json`; it does not sit and wait on long sweeps.
After each builder turn the controller runs every declared script, then the native
`kicadpy.case check` (or `check-print` for a handwired case), each in its own subprocess
with a timeout and no model. Results are parsed from the reports the scripts write, never
from an exit code:

    case/checks.json  {"checks": [{"name": "mechanical",
                                   "script": "tools/case/mech_check.py",
                                   "args": ["--force"],
                                   "report": "case/reports/mechanical.json",
                                   "timeoutSeconds": 900}]}

For the build waterfall (kicadpy.case_waterfall) each check names its `stage` (simple,
moving, joints or lid; simple by default) and a moving-part check its `kind` (print or
motion), and a top-level `stages` object lists the parts of each stage that applies.

A report with `verdict` (pass, review, unverified, fail) or a boolean `passed` gives the level.
A report that is missing, unreadable or not rewritten by this run is unverified: what could not
be measured is never a pass. A script must rewrite its report even when it skips work because
its inputs did not change.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import signal
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile

from . import case_measure

CHECKS_FILE = 'case/checks.json'
LEVELS = (case_measure.PASS, case_measure.REVIEW, case_measure.UNVERIFIED, case_measure.FAIL)
DEFAULT_TIMEOUT_SECONDS = 900
MAX_TIMEOUT_SECONDS = 3600
TOTAL_SECONDS = 2700        # every declared script together
NATIVE_TIMEOUT_SECONDS = 900
MAX_CHECKS = 12
SCRIPT_ROOTS = ('case/', 'tools/case/')
MESSAGE_CHARS = 300
STAGES = ('simple', 'moving', 'joints', 'lid')   # the build order; see case_waterfall
KINDS = ('print', 'motion')
FORBIDDEN_NAME_CHARS = '/\\:*?"<>|'   # Bambu Studio refuses to open a 3MF with these in names


def worst(levels):
    """fail > unverified > review > pass, as case_measure.verdict."""
    return case_measure.verdict([{'level': level} for level in levels])


def _entry(name, level, message, seconds=0.0, **extra):
    return {'name': name, 'level': level, 'message': str(message)[:MESSAGE_CHARS],
            'seconds': round(seconds, 1), **extra}


def _relative(worktree, name, roots):
    """A file the case owns, relative to the worktree; None when it escapes."""
    if not isinstance(name, str) or not name:
        return None
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or not str(path).startswith(roots):
        return None
    full = (Path(worktree) / path)
    if full.is_symlink() or not full.resolve().is_relative_to(Path(worktree).resolve()):
        return None
    return str(path)


def declared(worktree):
    """(checks, problems) from case/checks.json; a bad declaration is the builder's defect."""
    file = Path(worktree) / CHECKS_FILE
    if not file.is_file():
        return [], []
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return [], [f'{CHECKS_FILE} is not valid JSON: {exc}']
    items = data.get('checks') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], [f'{CHECKS_FILE} needs a "checks" list']
    checks, problems, names = [], [], set()
    for index, item in enumerate(items[:MAX_CHECKS]):
        label = f'{CHECKS_FILE} check {index + 1}'
        if not isinstance(item, dict):
            problems.append(f'{label} is not an object')
            continue
        name = item.get('name')
        if not isinstance(name, str) or not name.strip() or name in names or name == 'native':
            problems.append(f'{label} needs a unique name other than "native"')
            continue
        script = _relative(worktree, item.get('script'), SCRIPT_ROOTS)
        if script is None or not script.endswith('.py') or not (Path(worktree) / script).is_file():
            problems.append(f'{name}: script must be an existing .py file inside case/ or tools/case/')
            continue
        report = _relative(worktree, item.get('report'), ('case/', 'build/case/'))
        if report is None or not report.endswith('.json'):
            problems.append(f'{name}: report must be a .json path inside case/ or build/case/')
            continue
        args = item.get('args', [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            problems.append(f'{name}: args must be a list of strings')
            continue
        timeout = item.get('timeoutSeconds', DEFAULT_TIMEOUT_SECONDS)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            problems.append(f'{name}: timeoutSeconds must be a positive number')
            continue
        stage = item.get('stage', STAGES[0])
        if stage not in STAGES:
            problems.append(f'{name}: stage must be one of {", ".join(STAGES)}')
            continue
        kind = item.get('kind', 'check')
        if kind != 'check' and kind not in KINDS:
            problems.append(f'{name}: kind must be print or motion')
            continue
        parts = item.get('parts', [])
        if not isinstance(parts, list) or not all(isinstance(part, str) for part in parts):
            problems.append(f'{name}: parts must be a list of part names')
            continue
        names.add(name)
        checks.append({'name': name, 'script': script, 'args': args, 'report': report,
                       'timeoutSeconds': min(float(timeout), MAX_TIMEOUT_SECONDS),
                       'stage': stage, 'kind': kind, 'parts': parts[:200]})
    if len(items) > MAX_CHECKS:
        problems.append(f'{CHECKS_FILE} declares more than {MAX_CHECKS} checks; the rest were not run')
    return checks, problems


def run_process(argv, cwd, env, timeout):
    """(returncode or None on timeout, output tail). Kills the whole process group on timeout."""
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               errors='replace', start_new_session=True)
    try:
        output, _ = process.communicate(timeout=timeout)
        return process.returncode, output[-2000:]
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        output, _ = process.communicate()
        return None, (output or '')[-2000:]


def _tail(output):
    lines = [line for line in (output or '').splitlines() if line.strip()]
    return lines[-1] if lines else 'no output'


def report_level(path, started_ns):
    """(level, message) from a JSON report this run wrote."""
    path = Path(path)
    try:
        if path.stat().st_mtime_ns < started_ns:
            return case_measure.UNVERIFIED, 'report was not rewritten by this run'
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return case_measure.UNVERIFIED, 'report was not written'
    except (OSError, ValueError) as exc:
        return case_measure.UNVERIFIED, f'report is unreadable: {exc}'
    if not isinstance(data, dict):
        return case_measure.UNVERIFIED, 'report is not a JSON object'
    if data.get('verdict') in LEVELS:
        level = data['verdict']
    elif isinstance(data.get('passed'), bool):
        level = case_measure.PASS if data['passed'] else case_measure.FAIL
    else:
        return case_measure.UNVERIFIED, 'report has no verdict or passed field'
    errors = data.get('errors')
    detail = (f'{len(errors)} error(s)' if isinstance(errors, list) and errors else
              str(data.get('summary') or data.get('message') or level))
    return level, detail


def plate_names(worktree):
    """Plate and object names of the Print all 3MF (manifest printProject); None when absent.

    Bambu Studio refuses a project whose plate name holds a forbidden character, so the
    person cannot open Print all at all. No declared or readable 3MF is skipped, not failed.
    """
    out = Path(worktree) / 'case/out'
    try:
        project = json.loads((out / 'parts.json').read_text(encoding='utf-8')).get('printProject')
        relative = project.get('file') if isinstance(project, dict) else None
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(relative, str) or not relative.endswith('.3mf'):
        return None
    file = out / relative
    if not file.is_file() or not file.resolve().is_relative_to(Path(worktree).resolve()):
        return None
    try:
        with zipfile.ZipFile(file) as archive:
            config = archive.read('Metadata/model_settings.config')
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        return _entry('plate-names', case_measure.UNVERIFIED,
                      f'could not read plate names from case/out/{relative}: {exc}')
    try:
        root = ET.fromstring(config)
    except ET.ParseError as exc:
        return _entry('plate-names', case_measure.UNVERIFIED, f'Metadata/model_settings.config: {exc}')
    names = [(kind, item.get('value', ''))
             for kind, element, key in (('plate', 'plate', 'plater_name'), ('object', 'object', 'name'))
             for node in root.iter(element) for item in node.findall('metadata') if item.get('key') == key]
    bad = [f'{kind} "{name}"' for kind, name in names if any(char in name for char in FORBIDDEN_NAME_CHARS)]
    if bad:
        return _entry('plate-names', case_measure.FAIL,
                      f'rename {", ".join(bad)} in case/out/{relative}: Bambu Studio refuses names '
                      f'with {" ".join(FORBIDDEN_NAME_CHARS)} (write "MỚI-SỬA", not "MỚI/SỬA")')
    return _entry('plate-names', case_measure.PASS, f'{len(names)} plate/object names are Bambu-safe')


def handwired(worktree):
    try:
        manifest = json.loads((Path(worktree) / 'case/out/parts.json').read_text())
        return manifest['verification']['nativePCBCheckerApplies'] is False
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _native(worktree, env, case_python, runner, timeout, prefix=()):
    """The repo's own checker on the exported parts: check, or check-print for handwired."""
    from .case import status

    worktree = Path(worktree)
    if not (worktree / 'case/out/parts.json').is_file():
        return _entry('native', case_measure.UNVERIFIED, 'no case/out/parts.json yet; nothing exported to check')
    command = 'check-print' if handwired(worktree) else 'check'
    report = worktree / ('case/reports/print-check.json' if command == 'check-print' else 'case/reports/case-check.json')
    started_ns, began = time.time_ns(), time.monotonic()
    try:
        code, output = runner([*prefix, case_python, '-m', 'kicadpy.case', command, '.'], worktree, env, timeout)
    except OSError as exc:
        return _entry('native', case_measure.UNVERIFIED, f'could not start kicadpy.case {command}: {exc}',
                      time.monotonic() - began, command=command)
    seconds = time.monotonic() - began
    if code is None:
        return _entry('native', case_measure.UNVERIFIED, f'kicadpy.case {command} timed out after {timeout:.0f} s',
                      seconds, command=command)
    level, message = report_level(report, started_ns)
    if level == case_measure.UNVERIFIED:
        return _entry('native', level, f'{message}: {_tail(output)}', seconds, command=command)
    if command == 'check':
        # The same freshness the final gate reads: a report bound to an older board is not a pass.
        state, reason = status(worktree)
        if state in ('stale', 'none'):
            return _entry('native', case_measure.UNVERIFIED, reason, seconds, command=command)
        if state == 'failed':
            level = case_measure.FAIL
        message = reason
    return _entry('native', level, message, seconds, command=command, report=str(report.relative_to(worktree)))


def run(worktree, env, *, case_python, runner=run_process, total_seconds=TOTAL_SECONDS,
        native_timeout=NATIVE_TIMEOUT_SECONDS, prefix=(), reuse=None):
    """Run every declared case check and the native checker; return the summary.

    `prefix` wraps each command, e.g. in the builder's sandbox: the scripts are agent-written.
    `reuse` {check name: (level, message)}: checks of parts that passed and are frozen are not
    run again; their recorded result stands (the build waterfall's closed stages).
    """
    reuse = reuse or {}
    worktree = Path(worktree)
    began = time.monotonic()
    env = {**env, 'MPLCONFIGDIR': str(worktree / 'build/case/mpl')}
    checks, problems = declared(worktree)
    results = [_entry('checks.json', case_measure.FAIL, problem) for problem in problems]
    if not checks and not problems:
        results.append(_entry('checks.json', case_measure.UNVERIFIED,
                              f'{CHECKS_FILE} declares no mechanical checks; joints, clearances and '
                              'wire routes were not measured by the harness'))
    for check in checks:
        if check['name'] in reuse:
            level, message = reuse[check['name']]
            results.append(_entry(check['name'], level, f'reused: {message}', script=check['script'],
                                  report=check['report'], stage=check['stage'], kind=check['kind'],
                                  reused=True))
            continue
        remaining = total_seconds - (time.monotonic() - began)
        if remaining <= 1:
            results.append(_entry(check['name'], case_measure.UNVERIFIED,
                                  f'not run: the {total_seconds:.0f} s case check budget was spent',
                                  script=check['script'], stage=check['stage'], kind=check['kind']))
            continue
        timeout = min(check['timeoutSeconds'], remaining)
        started_ns, start = time.time_ns(), time.monotonic()
        try:
            code, output = runner([*prefix, case_python, check['script'], *check['args']], worktree, env, timeout)
        except OSError as exc:
            results.append(_entry(check['name'], case_measure.UNVERIFIED, f'could not start: {exc}',
                                  time.monotonic() - start, script=check['script'],
                                  stage=check['stage'], kind=check['kind']))
            continue
        seconds = time.monotonic() - start
        if code is None:
            level, message = case_measure.UNVERIFIED, f'timed out after {timeout:.0f} s'
        else:
            level, message = report_level(worktree / check['report'], started_ns)
            if level == case_measure.UNVERIFIED:
                message = f'{message} (exit {code}): {_tail(output)}'
        results.append(_entry(check['name'], level, message, seconds,
                              script=check['script'], report=check['report'],
                              stage=check['stage'], kind=check['kind']))
    names = plate_names(worktree)
    if names:
        results.append(names)
    results.append(_native(worktree, env, case_python, runner, native_timeout, prefix))
    return {'schema': 1, 'verdict': worst(entry['level'] for entry in results), 'checks': results,
            'seconds': round(time.monotonic() - began, 1),
            'checkedAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def brief(summary):
    """The compact form a prompt carries."""
    return {'verdict': summary['verdict'], 'cached': bool(summary.get('cached')),
            'report': summary.get('report'),
            'checks': [{key: entry[key] for key in ('name', 'level', 'message', 'seconds', 'stage')
                        if key in entry}
                       for entry in summary['checks']]}
