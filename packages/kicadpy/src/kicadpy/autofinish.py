"""Bounded Stop continuation for an explicitly started native board build.

One Stop-hook module for both engines: Codex (`-c hooks.Stop=…`, `Interrupt`) and Grok Build
(`.grok/hooks/kicad.json`, `StopCancelled`). Both send the event as JSON on stdin and read a
`{"decision": "block", "reason": …}` answer on stdout.

This is orchestration, never a substitute for the manufacturing gate. Each Stop
runs the real publisher; stale sidecars and successful process exits cannot pass.
No hook is armed by workspace creation or by a planning/question-only turn.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid

from .project import write_json

STATE = Path('.circuit/autofinish.json')
MAX_CONTINUATIONS = 8
MAX_SECONDS = 4 * 60 * 60
PUBLISH_TIMEOUT = 480


def read_state(workspace):
    try:
        value = json.loads((Path(workspace) / STATE).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def state_lock(workspace):
    directory = Path(workspace) / '.circuit'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'autofinish.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def arm(workspace):
    """Idempotent while active: repeated --active build must not reset the budget."""
    workspace = Path(workspace)
    with state_lock(workspace):
        state = read_state(workspace)
        session = os.environ.get('CODEX_THREAD_ID') or None
        if state.get('status') == 'active' and state.get('session') in (None, session):
            return
        if state.get('status') == 'active' and session is None:
            return
        write_json(workspace / STATE, {
            'spec': 1, 'run': uuid.uuid4().hex, 'status': 'active',
            'session': session, 'started': time.time(),
            'continuations': 0, 'unchanged': 0,
        })


def cancel(workspace, session):
    with state_lock(workspace):
        state = read_state(workspace)
        if state.get('status') == 'active' and state.get('session') in (None, session):
            state['status'] = 'interrupted'
            write_json(Path(workspace) / STATE, state)


def inspect_board(workspace):
    """Rebuild every native packet, retaining publisher output for diagnosis."""
    workspace = Path(workspace)
    projects = sorted((workspace / 'design').glob('*.kicad_pro'))
    if not projects:
        return False, ['No design/*.kicad_pro exists. Finish the approved design and publish it.']
    problems = []
    deadline = time.monotonic() + 1000
    for project in projects:
        if time.monotonic() >= deadline:
            problems.append('Verification time budget reached; remaining boards were not checked.')
            break
        log = workspace / '.circuit' / ('autofinish-' + project.stem + '.log')
        with log.open('w') as output:
            proc = subprocess.Popen(
                [sys.executable, '-m', 'kicadpy.publish', '--manufacturing', str(project)],
                cwd=workspace, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
            )
            try:
                proc.wait(timeout=min(PUBLISH_TIMEOUT, max(1, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass  # A stuck native process must not hold the Stop hook forever.
                problems.append(f'{project.name}: publisher timed out; inspect {log.relative_to(workspace)}.')
                continue
        # Parse the publisher's result as well as the freshly written artifact.
        try:
            result = json.loads(log.read_text().splitlines()[-1])
            if result.get('ok') is not True or proc.returncode != 0:
                raise ValueError(str(result.get('error', 'publisher failed')))
            board = json.loads((workspace / 'boards' / (project.stem + '.board.json')).read_text())
            if board['source']['engine'] != 'kicad-native' or board['native']['publication'] != 'complete':
                raise ValueError('publication incomplete')
            findings = board.get('validation', {}).get('warnings', [])
            errors = [f"{f.get('kind', 'error')}: {f.get('detail') or f.get('message', '')}"
                      for f in findings if f.get('severity') == 'error']
            problems.extend(f'{project.name}: {item}' for item in errors)
            if board.get('fab', {}).get('ready') is not True and not errors:
                problems.append(f'{project.name}: fab.ready is not true; inspect the manufacturing report.')
        except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            problems.append(f'{project.name}: publication could not be verified: {exc}. See {log.relative_to(workspace)}.')
    return not problems, sorted(set(problems))


def decide(state, ready, findings, now):
    """Return the next persisted state and the Codex hook response."""
    state = dict(state)
    if ready:
        state.update(status='ready', findings=[])
        return state, {'systemMessage': 'Circuit freshly published every board: fab.ready=true. Physical hardware remains untested.'}
    unchanged = state.get('unchanged', 0) + 1 if findings == state.get('findings') else 0
    state.update(findings=findings, unchanged=unchanged)
    exhausted = state['continuations'] >= MAX_CONTINUATIONS or now - state['started'] >= MAX_SECONDS
    if exhausted:
        # One final continuation reports the limit honestly; subsequent Stop is allowed.
        state['status'] = 'exhausted'
        reason = ('Circuit auto-finish reached its repair budget. The board is NOT ready. '
                  'Do not re-arm or change files in this reporting turn. Explain the remaining blockers '
                  'and what you attempted in plain language; do not ask the user to solve electrical issues.')
    else:
        state['continuations'] += 1
        reason = (f"Circuit verification: board NOT ready (repair continuation {state['continuations']}/{MAX_CONTINUATIONS}). "
                  'Continue the already approved build, fix the causes below, then publish and inspect previews. '
                  'Do not stop merely because two review rounds ended. Snapshot before edits; revert regressions. '
                  'Never weaken checks, remove requested functions, fabricate evidence, or edit derived reports. '
                  'Resolve engineering decisions yourself using datasheets/calculations; physical bench tests belong in bringup. ')
        if unchanged:
            reason += ('The blockers did not change. Use a different repair strategy: inspect actual pad/net geometry, '
                       'adjust local placement or routing scope if needed while preserving correct copper; '
                       'do not repeat the same failed operation. ')
    reason += '\nCurrent findings (full reports and publisher logs are in boards/ and .circuit/):\n' + '\n'.join(findings)[:12000]
    return state, {'decision': 'block', 'reason': reason}


def is_user_cancel(event):
    """Codex says `Interrupt`; Grok Build says `StopCancelled` and names who cancelled.

    Grok's runtime cancels (`max_turns`, `no_progress`) are not the user walking away: the run
    stays armed for the next Stop. An unknown `cancelledBy` is treated as the user, as Grok's own
    docs ask.
    """
    name = event.get('hook_event_name')
    if name == 'Interrupt':
        return True
    return name == 'StopCancelled' and event.get('cancelledBy') != 'runtime'


def handle(event, inspect=inspect_board):
    workspace = Path(os.environ.get('HARNESS_WORKSPACE') or event.get('cwd') or '.').resolve()
    # Codex/Claude put the session under `session_id`; Grok Build under `sessionId`.
    session = event.get('session_id') or event.get('sessionId')
    if not session:
        return {}
    if is_user_cancel(event):
        if (workspace / STATE).exists():
            cancel(workspace, session)
        return {}
    if event.get('hook_event_name') != 'Stop':
        return {}
    # Grok fires a second Stop at session teardown (`reason` channel_closed / shutdown) whose
    # decision is ignored; only a real end of turn is worth a publisher run.
    if event.get('reason') not in (None, 'end_turn'):
        return {}
    # Do not create orchestration state in a workspace that never started a build.
    if not (workspace / STATE).exists():
        return {}
    with state_lock(workspace):
        state = read_state(workspace)
        if state.get('status') != 'active' or state.get('session') not in (None, session):
            return {}
        state['session'] = session
        write_json(workspace / STATE, state)
    try:
        ready, findings = inspect(workspace)
    except Exception as exc:
        ready, findings = False, [f'Circuit verification failed: {exc}. Diagnose the checker before claiming readiness.']
    # Interrupt can arrive while the publisher runs. Never revive its cancelled run.
    with state_lock(workspace):
        current = read_state(workspace)
        if current.get('run') != state.get('run') or current.get('status') != 'active':
            return {}
        updated, response = decide(state, ready, findings, time.time())
        write_json(workspace / STATE, updated)
    return response


def main():
    event = json.load(sys.stdin)
    print(json.dumps(handle(event)))


if __name__ == '__main__':
    main()
