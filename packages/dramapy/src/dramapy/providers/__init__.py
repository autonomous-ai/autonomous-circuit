"""Provider registry. Selection order (contract §1): explicit arg > env
``VIDEO_PROVIDER`` > ``"mock"``. Hosted backends are imported lazily so the
network-capable modules never even load unless selected."""

from __future__ import annotations

from dramapy.errors import ProviderError
from dramapy.providers.base import Provider, ShotContext

PROVIDER_NAMES = ("mock", "fal", "dashscope", "minimax", "cinematic")


def make_provider(name: str) -> Provider:
    normalized = str(name or "").strip().lower()
    if normalized == "mock":
        from dramapy.providers.mock import MockProvider

        return MockProvider()
    if normalized == "cinematic":
        from dramapy.providers.cinematic import CinematicProvider

        return CinematicProvider()
    if normalized == "fal":
        from dramapy.providers.fal import FalProvider

        return FalProvider()
    if normalized == "dashscope":
        from dramapy.providers.dashscope import DashscopeProvider

        return DashscopeProvider()
    if normalized == "minimax":
        from dramapy.providers.minimax import MinimaxProvider

        return MinimaxProvider()
    raise ProviderError(
        f"unknown provider '{name}' (known: {', '.join(PROVIDER_NAMES)})"
    )


__all__ = ["Provider", "ShotContext", "make_provider", "PROVIDER_NAMES"]
