"""Is the firmware written? — the one question the Stop hook and the verdict both ask.

The firmware for a board lives in `<workspace>/firmware/`: a README (what it does, the
toolchain, build and flash commands, the pin table read off the final copper) and the sources
for the chip's usual toolchain. The viewer's Firmware tab reads the same tree with the same
rules (viewer/src/server/circuit/catalog.mjs), so "written" here is exactly "the tab is not
empty": a README plus at least one source file that a person reads. Objects, ELFs, UF2s and
whatever a build directory leaves behind are neither.

The firmware never gates `fab.ready`: a board is prototype-ready on its design evidence. What it
gates is the *turn* — the Stop hook (`kicadpy.autofinish`) asks once for the firmware when the
board is ready and this tree is empty, and the pane's Firmware phase stays active until it is not.

Written is not built. The Opus 5.5 servo bench (2026-09-30) shipped a README, sources, host tests
and a `flash.json` that named a UF2 nobody had produced — the machine had no ARM toolchain — and the
pane said Firmware done; the same sources failed to compile once a toolchain was there. So the
phase now asks a second question, `built`: the binary the recipe names exists and is no older than
the sources it was built from. The Flash button reads the same recipe, so "built" is exactly
"the button has something to write".
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FIRMWARE_DIR = 'firmware'
README = 'README.md'
#: The Flash button's recipe (viewer firmwareFlash.mjs). Asked for, never required for `written`.
RECIPE = 'flash.json'
SOURCE_SUFFIXES = frozenset({
    '.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.ino', '.s', '.asm', '.ld',
    '.py', '.rs', '.js', '.ts', '.lua',
    '.txt', '.ini', '.toml', '.yaml', '.yml', '.json', '.cfg', '.cmake', '.mk', '.sh',
})
BARE_NAMES = frozenset({'Makefile', 'makefile', 'GNUmakefile', 'Kconfig', 'Dockerfile'})
SKIP_DIRS = frozenset({
    'node_modules', '.pio', '.pioenvs', '.piolibdeps', 'build', 'cmake-build-debug', 'cmake-build-release',
    'target', 'dist', 'out', '__pycache__', '.venv', 'venv', '.cache', '.git', '.idea', '.vscode',
})


def is_source(name: str) -> bool:
    """A file the Firmware tab shows: a source or build file, never a binary or a dotfile."""
    base = Path(name).name
    if not base or base.startswith('.'):
        return False
    return base in BARE_NAMES or Path(base).suffix.lower() in SOURCE_SUFFIXES


def sources(workspace: Path | str) -> list[str]:
    """Every source under `firmware/` (README excluded), workspace-relative, sorted."""
    root = Path(workspace) / FIRMWARE_DIR
    if not root.is_dir():
        return []
    found: list[str] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in SKIP_DIRS and not entry.name.startswith('.'):
                    stack.append(entry)
            elif entry.is_file() and is_source(entry.name) and not (entry.parent == root and entry.name.lower() == README.lower()):
                found.append(entry.relative_to(Path(workspace)).as_posix())
    return sorted(found)


def has_readme(workspace: Path | str) -> bool:
    root = Path(workspace) / FIRMWARE_DIR
    try:
        return any(p.is_file() and p.name.lower() == README.lower() for p in root.iterdir())
    except OSError:
        return False


def firmware_written(workspace: Path | str) -> bool:
    """README plus at least one source — the tab shows something, the reader can build it."""
    return has_readme(workspace) and bool(sources(workspace))


#: What compiles into the binary: sources, headers, linker scripts and the build files. A host
#: helper (`servoctl.py`), a README, the recipe or a host test edited after the build never make
#: the binary stale — the 2026-09-30 Astra tree had `servoctl.py` saved 40 s after its UF2.
COMPILED_SUFFIXES = frozenset({'.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.ino', '.s', '.asm', '.ld', '.rs',
                               '.cmake', '.mk', '.ini'})
COMPILED_BARE_NAMES = frozenset({'CMakeLists.txt', 'Makefile', 'makefile', 'GNUmakefile', 'Kconfig'})
TEST_DIRS = frozenset({'test', 'tests'})


def compiles_into_binary(rel: str) -> bool:
    """A workspace-relative source path that the build reads (never a host test or helper)."""
    path = Path(rel)
    if any(part in TEST_DIRS for part in path.parts[:-1]):
        return False
    return path.name in COMPILED_BARE_NAMES or path.suffix.lower() in COMPILED_SUFFIXES


def recipe(workspace: Path | str) -> dict | None:
    """`firmware/flash.json` parsed, or None when absent or not a JSON object."""
    path = Path(workspace) / FIRMWARE_DIR / RECIPE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def binary(workspace: Path | str) -> Path | None:
    """The file the Flash button would write, as the recipe names it (paths relative to `firmware/`).

    `rp2040`: the `uf2`. `esp32` with arduino-cli: `<build>/<sketch>.ino.bin`, which is what
    `arduino-cli compile --build-path` leaves behind. Any other family, or a recipe without the
    fields, names nothing — and nothing cannot be built.
    """
    data = recipe(workspace)
    if not data:
        return None
    root = Path(workspace) / FIRMWARE_DIR
    family = str(data.get('family') or '').lower()
    if family == 'rp2040' and data.get('uf2'):
        return Path(os.path.normpath(root / str(data['uf2'])))
    if family == 'esp32' and data.get('build') and data.get('sketch'):
        return Path(os.path.normpath(root / str(data['build']) / (Path(str(data['sketch'])).name + '.ino.bin')))
    return None


def built(workspace: Path | str) -> bool:
    """The recipe's binary exists, is not empty, and is no older than any compiled source."""
    target = binary(workspace)
    if target is None:
        return False
    try:
        stat = target.stat()
    except OSError:
        return False
    if stat.st_size == 0:
        return False
    newest = 0.0
    for rel in sources(workspace):
        if not compiles_into_binary(rel):
            continue
        try:
            newest = max(newest, (Path(workspace) / rel).stat().st_mtime)
        except OSError:
            continue
    return stat.st_mtime >= newest


def firmware_built(workspace: Path | str) -> bool:
    """Written, and the binary the recipe names is there and current — the Firmware phase's `done`."""
    return firmware_written(workspace) and built(workspace)


def missing(workspace: Path | str) -> list[str]:
    """What is still missing, in the words the hook hands back to the agent."""
    gaps = []
    if not has_readme(workspace):
        gaps.append(f'{FIRMWARE_DIR}/{README} (what it does, toolchain, build + flash commands, the pin table, what is untested)')
    if not sources(workspace):
        gaps.append(f"{FIRMWARE_DIR}/ sources for the chip's usual toolchain (main.c / main.cpp / main.ino / code.py, plus the build file)")
    if not (Path(workspace) / FIRMWARE_DIR / RECIPE).is_file():
        gaps.append(f"{FIRMWARE_DIR}/{RECIPE} (the recipe the pane's Flash button runs: family, fqbn, sketch, build, usbVid — see AGENTS.md)")
    elif not built(workspace):
        target = binary(workspace)
        where = os.path.relpath(target, Path(workspace)) if target is not None else f'{FIRMWARE_DIR}/{RECIPE} names no binary (family rp2040 needs `uf2`; esp32 needs `build` + `sketch`)'
        gaps.append(f'the built binary — {where} is missing, empty or older than the sources: build it with the toolchain '
                    f'the tile carries ($KICAD_HARNESS_PICO_SDK + $KICAD_HARNESS_ARM_TOOLCHAIN for an RP2040, arduino-cli for an ESP32) '
                    f'and fix the sources until it builds; a compile error is a finding, not a note')
    return gaps
