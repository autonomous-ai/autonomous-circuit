"""dramapy — chat → screenplay → shots → stitched vertical episodes.

The Python pipeline behind Autonomous TV's dramacode skill (contract
``docs/video-interfaces.md`` §1). Public surface: :func:`generate_episode`
plus the ``GenerationError`` hierarchy; everything renders through ffmpeg
subprocesses (zero Python dependencies beyond the stdlib).
"""

from __future__ import annotations

from dramapy.errors import (
    ExportError,
    GenerationError,
    GeneratorRuntimeError,
    ProjectShapeError,
    ProviderError,
    SpecValidationError,
)
from dramapy.generation import generate_episode

__version__ = "0.1.0"

__all__ = [
    "generate_episode",
    "GenerationError",
    "ProjectShapeError",
    "GeneratorRuntimeError",
    "SpecValidationError",
    "ProviderError",
    "ExportError",
    "__version__",
]
