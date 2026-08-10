"""The ``generate_episode()`` error hierarchy (contract §1, verbatim).

Defined here so ``spec``/``providers``/``stitch`` can raise them without a
circular import; :mod:`dramapy.generation` re-exports every name, so the
contract's ``dramapy.generation.GenerationError`` spelling holds.

``SyntaxError`` and ``ImportError`` from project code are never wrapped —
they propagate untouched so the skill runner can map them by type.
"""

from __future__ import annotations


class GenerationError(Exception):
    """Base error for :func:`dramapy.generation.generate_episode`.

    The frozen contract (`docs/video-interfaces.md` §1) requires every error
    raised by ``generate_episode`` to subclass this type, so callers such as
    the dramacode skill runner can map them to JSON error codes.
    """


class ProjectShapeError(GenerationError):
    """Missing ``gen_episode`` / bad envelope / bad output suffix / missing
    ``series.py``. Also: an ``AssertionError`` from a project ``validate()``
    is re-raised as this type carrying the assert message."""


class GeneratorRuntimeError(GenerationError):
    """The project's ``gen_episode()`` raised, or the module failed to load."""


class SpecValidationError(GenerationError):
    """Episode/Scene/Shot structure invalid (shot rules, unknown cast ids)."""


class ProviderError(GenerationError):
    """The render backend failed (network, quota, content policy, timeout)."""


class ExportError(GenerationError):
    """Stitch/mux/subtitle/poster writing failed after shots rendered."""


class EnvelopeError(ProjectShapeError, TypeError):
    """A ``gen_episode()`` return that is not the accepted envelope shape.

    The contract states both that a bad envelope is a :class:`ProjectShapeError`
    (error hierarchy) and that unknown envelope keys "raise ``TypeError``"
    (§1 envelope rule). This private subclass satisfies both clauses at once:
    ``isinstance(exc, ProjectShapeError)`` and ``isinstance(exc, TypeError)``
    are both true. Catch :class:`ProjectShapeError`; this name is an
    implementation detail, not part of the frozen hierarchy.
    """
