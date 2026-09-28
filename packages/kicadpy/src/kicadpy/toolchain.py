"""Keep KiCad's bundled Python isolated from the host interpreter."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

BUNDLE = Path('/Applications/KiCad/KiCad.app/Contents')


def executable(kind):
    env = os.environ.get('KICADPY_' + kind.upper())
    candidates = [env] if env else (
        [str(BUNDLE / 'MacOS/kicad-cli'), shutil.which('kicad-cli')] if kind == 'cli' else
        [str(BUNDLE / 'Frameworks/Python.framework/Versions/Current/bin/python3'), sys.executable])
    for item in candidates:
        if item and Path(item).is_file():
            return item
    raise RuntimeError(f'KiCad {kind} unavailable; set KICADPY_{kind.upper()}')


def run(args, timeout=120):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'tool failed ({result.returncode}): {result.stderr[-1500:]}')
    return result.stdout


def worker(operation, pcb, **kwargs):
    request = json.dumps(dict(operation=operation, pcb=str(pcb), **kwargs))
    result = subprocess.run([executable('python'), str(Path(__file__).with_name('worker.py'))],
                            input=request, text=True, capture_output=True, timeout=120)
    # pcbnew/wx can print diagnostics; the protocol is a prefixed JSON line.
    lines = [line[9:] for line in result.stdout.splitlines() if line.startswith('KICADPY: ')]
    # A response line is the result even when the interpreter dies on the way
    # out (pcbnew SWIG teardown segfaults); no line and a bad exit is the error.
    if len(lines) != 1:
        raise RuntimeError(result.stderr[-1500:] if result.returncode else 'missing worker response')
    return json.loads(lines[0])


def versions():
    cli = run([executable('cli'), 'version']).strip()
    if not cli.startswith('10.'):
        raise RuntimeError('spike requires KiCad 10')
    api = worker('version', '')['pcbnew']
    if api != cli:
        raise RuntimeError('KiCad CLI and pcbnew versions differ')
    return {'kicad': cli, 'pcbnew': api,
            'workerSha256': hashlib.sha256(Path(__file__).with_name('worker.py').read_bytes()).hexdigest()}
