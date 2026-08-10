"""fal provider — Wan 2.2 text-to-video via the fal queue API.

Submit + poll against ``https://queue.fal.run`` (contract §1):

  1. ``POST https://queue.fal.run/<model>`` with ``Authorization: Key $FAL_KEY``
     → ``{"request_id", "status_url", "response_url"}``
  2. ``GET status_url`` until ``status == "COMPLETED"`` (poll every 3s,
     bounded by the per-shot budget)
  3. ``GET response_url`` → ``{"video": {"url": ...}}`` → download to
     ``ctx.output_path``

All network code lives in this module and is NEVER exercised by tests —
constructing the provider without ``FAL_KEY`` raises
:class:`~dramapy.errors.ProviderError` before any socket is opened, and the
test suite only ever selects the mock provider for rendering.

Env: ``FAL_KEY`` (required), ``VIDEO_FAL_MODEL`` (optional model id override,
default ``fal-ai/wan/v2.2-a14b/text-to-video``).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from dramapy.errors import ProviderError
from dramapy.providers.base import Provider, ShotContext, build_shot_prompt

QUEUE_BASE = "https://queue.fal.run"
DEFAULT_MODEL = "fal-ai/wan/v2.2-a14b/text-to-video"
DEFAULT_BUDGET_S = 600.0
POLL_INTERVAL_S = 3.0
_BODY_TRUNCATE = 300


class FalProvider(Provider):
    name = "fal"
    model = "wan-2.2"

    def __init__(self) -> None:
        key = os.environ.get("FAL_KEY", "").strip()
        if not key:
            raise ProviderError(
                "fal provider requires the FAL_KEY environment variable "
                "(https://fal.ai dashboard → keys)"
            )
        self._key = key
        self._model_id = os.environ.get("VIDEO_FAL_MODEL", "").strip() or DEFAULT_MODEL

    # -- HTTP plumbing -----------------------------------------------------

    def _request(self, url: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST" if body is not None else "GET",
            headers={
                "Authorization": f"Key {self._key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:_BODY_TRUNCATE]
            raise ProviderError(
                f"fal request to {url} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"fal request to {url} failed: {exc}") from exc

    def _download(self, url: str, target: Path) -> None:
        request = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"fal video download failed: {exc}") from exc

    # -- Provider interface --------------------------------------------------

    def render_shot(self, ctx: ShotContext) -> Path:
        shot = ctx.shot
        budget = ctx.max_render_s or DEFAULT_BUDGET_S
        deadline = time.monotonic() + budget

        width, height = ctx.series.resolution
        payload = {
            "prompt": build_shot_prompt(ctx),
            "aspect_ratio": ctx.series.aspect,
            "resolution": "720p" if min(width, height) >= 720 else "480p",
        }

        submitted = self._request(f"{QUEUE_BASE}/{self._model_id}", payload)
        status_url = submitted.get("status_url")
        response_url = submitted.get("response_url")
        if not status_url or not response_url:
            raise ProviderError(
                f"fal submit for shot '{shot.id}' returned no queue urls: "
                f"{json.dumps(submitted)[:_BODY_TRUNCATE]}"
            )

        while True:
            if time.monotonic() > deadline:
                raise ProviderError(
                    f"fal render for shot '{shot.id}' timed out after {budget:g}s"
                )
            status = self._request(str(status_url))
            state = str(status.get("status", "")).upper()
            if state == "COMPLETED":
                break
            if state in {"FAILED", "ERROR", "CANCELLED"}:
                raise ProviderError(
                    f"fal render for shot '{shot.id}' failed: "
                    f"{json.dumps(status)[:_BODY_TRUNCATE]}"
                )
            time.sleep(min(POLL_INTERVAL_S, max(0.1, deadline - time.monotonic())))

        result = self._request(str(response_url))
        video_url = ((result.get("video") or {}).get("url")) if isinstance(result, dict) else None
        if not video_url:
            raise ProviderError(
                f"fal response for shot '{shot.id}' has no video url: "
                f"{json.dumps(result)[:_BODY_TRUNCATE]}"
            )
        self._download(str(video_url), ctx.output_path)
        if not ctx.output_path.is_file() or ctx.output_path.stat().st_size == 0:
            raise ProviderError(
                f"fal render for shot '{shot.id}' produced no clip at {ctx.output_path}"
            )
        return ctx.output_path
