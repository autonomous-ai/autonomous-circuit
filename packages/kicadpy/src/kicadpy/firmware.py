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
"""
from __future__ import annotations

from pathlib import Path

FIRMWARE_DIR = 'firmware'
README = 'README.md'
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


def missing(workspace: Path | str) -> list[str]:
    """What is still missing, in the words the hook hands back to the agent."""
    gaps = []
    if not has_readme(workspace):
        gaps.append(f'{FIRMWARE_DIR}/{README} (what it does, toolchain, build + flash commands, the pin table, what is untested)')
    if not sources(workspace):
        gaps.append(f"{FIRMWARE_DIR}/ sources for the chip's usual toolchain (main.c / main.cpp / main.ino / code.py, plus the build file)")
    return gaps
