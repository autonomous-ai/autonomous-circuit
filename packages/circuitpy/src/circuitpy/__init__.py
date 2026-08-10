"""circuitpy — Autonomous Circuit's build pipeline.

Board TSX -> compiled Circuit JSON + verified, fab-ready PCB/PCBA artifacts
via the repo's pinned tscircuit toolchain and (when installed) kicad-cli.
Zero Python runtime dependencies; all Node/EDA work is out-of-process.

Public surface: :func:`circuitpy.generation.build_board` and the
:mod:`circuitpy.errors` hierarchy (re-exported here).
"""

from circuitpy.errors import (
    BuildError,
    CompileError,
    ExportError,
    ProjectShapeError,
    SpecValidationError,
    ToolchainError,
)
from circuitpy.generation import build_board

__all__ = [
    "build_board",
    "BuildError",
    "ProjectShapeError",
    "SpecValidationError",
    "CompileError",
    "ToolchainError",
    "ExportError",
]
