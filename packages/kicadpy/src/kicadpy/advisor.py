"""A second model that reads the board and advises the one building it, at the two moments it pays.

The builder (Sol) is fast and cheap and does 90 % of a board alone. What it cannot do alone is
notice its own blind spot: on 2026-10-06 a desk pet burned 7 repair continuations that changed
nothing, and every board so far was signed off by the model that drew it. The Stop hook
(`kicadpy.autofinish`) calls an advisor at exactly two moments:

- **stuck** — the blockers came back unchanged twice in a row. The advice goes into the next
  repair continuation.
- **ready** — the board just went green and the firmware is built. The advisor reviews it once;
  "NO CONCERNS" lets the run finish, anything else is one more turn for the builder to act on or
  answer in `.circuit/advisor-response.md`.

The advisor never touches the workspace (read-only sandbox), never talks to the person, and never
decides anything: the gate stays the referee, and the builder may reject advice with a reason. It
reads a small bundle of artifacts, not the builder's transcript (tens of millions of tokens).
At most `MAX_CALLS` per run; a failure or timeout is skipped, never a blocker.

Which advisor: `KICAD_ADVISOR` (`codex:<model>` or `claude:<model>`), else the first line of
`~/.harness/kicad-advisor`. Empty or absent: off, and the hook behaves exactly as before.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

MAX_CALLS = 3
# Measured 2026-10-08: Astra at medium effort reviewed a desk pet in 139 s (180 s timed out at high).
TIMEOUT = 300
EFFORT = 'medium'
STUCK_AFTER = 2
PER_FILE = 6000
BUNDLE_MAX = 40000
NO_CONCERNS = 'NO CONCERNS'
CONFIG = Path.home() / '.harness' / 'kicad-advisor'

#: What the advisor reads, in order; the first existing path of each group.
BUNDLE = (
    ('product', ('product.json', 'design/product.json')),
    ('readme', ('README.md',)),
    ('pinout', ('engineering/pinout.md', 'design/engineering/pinout.md')),
    ('power', ('engineering/power.md', 'design/engineering/power.md')),
    ('bringup', ('engineering/bringup.md', 'design/engineering/bringup.md')),
    ('firmware-readme', ('firmware/README.md',)),
    ('flash', ('firmware/flash.json',)),
    ('handoff', ('engineering/handoff.md',)),
)


def spec(env=None):
    """`(engine, model)` or None when the advisor is off."""
    env = os.environ if env is None else env
    raw = (env.get('KICAD_ADVISOR') or '').strip()
    if not raw:
        try:
            raw = CONFIG.read_text(encoding='utf-8').splitlines()[0].strip()
        except (OSError, IndexError):
            raw = ''
    if not raw or raw.lower() in ('off', 'none', '0'):
        return None
    engine, _, model = raw.partition(':')
    engine = engine.strip().lower()
    if engine not in ('codex', 'claude') or not model.strip():
        return None
    return engine, model.strip()


def bundle(workspace, findings=()):
    """The artifacts the advisor reads, capped, each under its own heading."""
    workspace = Path(workspace)
    parts = []
    for name, candidates in BUNDLE:
        for rel in candidates:
            path = workspace / rel
            if path.is_file():
                text = path.read_text(encoding='utf-8', errors='replace')
                if len(text) > PER_FILE:
                    text = text[:PER_FILE] + '\n…(truncated)'
                parts.append(f'===== {rel} ({name}) =====\n{text}')
                break
    if findings:
        parts.append('===== current gate findings =====\n' + '\n'.join(findings))
    out = '\n\n'.join(parts)
    return out[:BUNDLE_MAX]


def prompt(moment, workspace, findings=()):
    head = {
        'stuck': ('The builder has repaired this board several times and the blockers below came back '
                  'unchanged. Find the cause it keeps missing.'),
        'ready': ('The builder says this board is prototype-ready and its firmware is built. Review it as '
                  'an independent engineer before it is handed to a person who does not know electronics.'),
    }[moment]
    return (
        'You are the ADVISOR for a KiCad board built by another agent in the workspace given as the '
        'working directory. You may read any file there; you must not modify anything. ' + head + '\n\n'
        'Look for real defects only: a pin used for something its datasheet forbids (strapping, USB, '
        'flash), a power budget that does not add up, a firmware pin table that disagrees with the '
        'netlist, a servo or LED rail that can back-feed USB, an assumption with no bench check, a '
        'safety rule broken. Style, wording and nice-to-haves are not defects.\n\n'
        'Answer in at most 8 numbered points. Every point names the file (and line or field) it is '
        'about and says what to change. If you find nothing real, answer exactly: ' + NO_CONCERNS + '\n'
        'Never ask the person anything. Never suggest weakening a check.\n\n'
        'Artifacts (also on disk):\n\n' + bundle(workspace, findings)
    )


def _cite_ok(text, workspace):
    """Advice must point at something in the workspace, or be the explicit all-clear."""
    if text.strip().upper().startswith(NO_CONCERNS):
        return True
    names = set(re.findall(r'[\w./-]+\.(?:json|md|kicad_sch|kicad_pcb|ino|cpp|h|c|py|csv)', text))
    return any((Path(workspace) / n).exists() or (Path(workspace) / n.lstrip('./')).exists() for n in names)


def command(engine, model, workspace, text, out_file):
    if engine == 'codex':
        return ['codex', 'exec', '-m', model, '-c', f'model_reasoning_effort={EFFORT}', '-s', 'read-only',
                '--skip-git-repo-check', '--ephemeral', '--json',
                '-C', str(workspace), '-o', str(out_file), text]
    return ['claude', '-p', '--model', model, '--permission-mode', 'plan', text]


def usage_of(stream):
    """Token counts from `codex exec --json` (`turn.completed` events), summed; {} when absent."""
    total = {}
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('type') == 'turn.completed' and isinstance(event.get('usage'), dict):
            for key in ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens'):
                total[key] = total.get(key, 0) + int(event['usage'].get(key) or 0)
    return total


def ask(moment, workspace, findings=(), env=None, run=subprocess.run, timeout=TIMEOUT):
    """The advisor's answer, or None when it is off, fails, times out, or cites nothing real.

    Every call, used or not, is logged under `.circuit/advisor/` so a person can see what was said.
    """
    chosen = spec(env)
    if not chosen:
        return None
    engine, model = chosen
    if not shutil.which(engine):
        return None
    workspace = Path(workspace)
    log_dir = workspace / '.circuit' / 'advisor'
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S') + f'-{len(list(log_dir.glob("*.md"))) + 1:02d}'
    text = prompt(moment, workspace, findings)
    answer = None
    note = ''
    usage = {}
    began = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'last.txt'
        try:
            proc = run(command(engine, model, workspace, text, out), cwd=workspace, capture_output=True,
                       text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            usage = usage_of(proc.stdout or '')
            raw = out.read_text(encoding='utf-8') if out.exists() else (proc.stdout or '')
            if proc.returncode != 0:
                note = f'exit {proc.returncode}: {(proc.stderr or "")[-400:]}'
            elif _cite_ok(raw, workspace):
                answer = raw.strip()
            else:
                note = 'dropped: cites nothing in the workspace'
            body = raw
        except subprocess.TimeoutExpired:
            note, body = f'timed out after {timeout}s', ''
        except OSError as exc:
            note, body = f'could not run {engine}: {exc}', ''
    seconds = round(time.monotonic() - began)
    cost = ' · '.join(f'{k} {v}' for k, v in usage.items()) or 'usage not reported'
    (log_dir / f'{stamp}-{moment}.md').write_text(
        f'# advisor {engine}:{model} · {moment} · {stamp}\n\n{note or "used"} · {seconds} s · {cost}\n\n{body}\n',
        encoding='utf-8')
    return answer


def moment(state, ready, findings, firmware, max_calls=MAX_CALLS):
    """Which advisor moment this Stop is, if any, before `decide` runs."""
    if state.get('advisor_calls', 0) >= max_calls:
        return None
    if not ready and state.get('continuations', 0) >= 8:   # autofinish.MAX_CONTINUATIONS
        return None   # the next Stop is the reporting turn; advice would be thrown away
    if ready:
        return 'ready' if firmware and not state.get('advisor_ready') else None
    unchanged = state.get('unchanged', 0) + 1 if findings == state.get('findings') else 0
    return 'stuck' if unchanged >= STUCK_AFTER else None
