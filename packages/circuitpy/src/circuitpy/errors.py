"""The ``build_board()`` error hierarchy (contract §1, verbatim).

Defined here so ``spec``/``fab``/``review`` can raise them without a circular
import; :mod:`circuitpy.generation` re-exports every name, so the contract's
``circuitpy.generation.BuildError`` spelling holds.

Layering rule (donor discipline): :mod:`circuitpy.toolchain` raises plain
``RuntimeError``/``TimeoutError`` with an output tail; each caller wraps into
this hierarchy at its own boundary.
"""

from __future__ import annotations


class BuildError(Exception):
    """Base error for :func:`circuitpy.generation.build_board`.

    The frozen contract (`docs/circuit-interfaces.md` §1) requires every error
    raised by ``build_board`` to subclass this type, so callers such as the
    circuitcode skill runner can map them to JSON error codes.
    """


class ProjectShapeError(BuildError):
    """Missing board source / bad ``product.json`` / bad output suffix /
    unknown fab profile / unreadable ``parts.json`` / invalid frozen block
    snapshot."""


class SpecValidationError(BuildError):
    """Pre-flight source/spec rules failed — the safety envelope lives here
    (no mains ever, sealed battery blocks only, certified radio modules only).
    Refused at spec time, before any toolchain process runs."""


class CompileError(BuildError):
    """tscircuit eval failed fatally — the TSX would not compile, so no
    ``circuit.json`` was produced. Non-fatal build errors are NOT this: they
    arrive as ``*_error`` elements inside circuit.json and become warnings."""


class ToolchainError(BuildError):
    """A toolchain subprocess failed — tscircuit-cli / node / kicad-cli
    missing, crashed, or timed out."""


class ExportError(BuildError):
    """Gerber/BOM/CPL/render/sidecar writing failed on an otherwise-good
    build."""
