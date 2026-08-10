"""dashscope provider — Alibaba Model Studio (Bailian/DashScope) video
generation, async task submit + poll (founder directive 2026-08-09; recorded
in ``docs/video-interfaces-CHANGES.md``).

Flow (documented DashScope task shape):

  1. ``POST {base}/services/aigc/video-generation/video-synthesis`` with
     ``Authorization: Bearer $DASHSCOPE_API_KEY`` and
     ``X-DashScope-Async: enable`` → ``{"output": {"task_id": ...}}``
  2. ``GET {base}/tasks/{task_id}`` until ``output.task_status`` is
     ``SUCCEEDED`` (``FAILED``/``CANCELED``/``UNKNOWN`` → ProviderError)
  3. download ``output.video_url`` (older models:
     ``output.results[0].video_url``) to ``ctx.output_path``

Env: ``DASHSCOPE_API_KEY`` (required), ``VIDEO_DASHSCOPE_MODEL`` (optional,
default ``wan2.2-t2v-plus``), ``VIDEO_DASHSCOPE_BASE_URL`` (optional,
default the international endpoint ``https://dashscope-intl.aliyuncs.com/api/v1``).

NETWORK-UNTESTED (honest markers, per directive):
  * TODO(verify-live): the newest Wan models (wan2.7-t2v-*) are documented
    on workspace-scoped hosts (``https://{WorkspaceId}.{region}.maas.aliyuncs.com``)
    with ``parameters.resolution/ratio/duration`` instead of ``size``;
    this module sends the classic ``parameters.size = "W*H"`` shape that
    matches wan2.x-t2v-plus era models. Point ``VIDEO_DASHSCOPE_BASE_URL``
    at a workspace host if the account requires it.
  * Tests never exercise this module — constructing it without
    ``DASHSCOPE_API_KEY`` raises ProviderError before any socket opens.

No retries beyond the caller's single retry; failures raise ProviderError
with the vendor error body truncated.
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

DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/api/v1"
DEFAULT_MODEL = "wan2.2-t2v-plus"
DEFAULT_BUDGET_S = 600.0
POLL_INTERVAL_S = 5.0
_BODY_TRUNCATE = 300

_TERMINAL_FAILURES = {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}


class DashscopeProvider(Provider):
    name = "dashscope"

    def __init__(self) -> None:
        key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not key:
            raise ProviderError(
                "dashscope provider requires the DASHSCOPE_API_KEY environment "
                "variable (Alibaba Cloud Model Studio → API keys)"
            )
        self._key = key
        self._base_url = (
            os.environ.get("VIDEO_DASHSCOPE_BASE_URL", "").strip().rstrip("/")
            or DEFAULT_BASE_URL
        )
        self.model = os.environ.get("VIDEO_DASHSCOPE_MODEL", "").strip() or DEFAULT_MODEL

    # -- HTTP plumbing (isolated here; never exercised in tests) ------------

    def _request(
        self, url: str, payload: dict | None = None, *, async_header: bool = False
    ) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        if async_header:
            headers["X-DashScope-Async"] = "enable"
        request = urllib.request.Request(
            url,
            data=body,
            method="POST" if body is not None else "GET",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:_BODY_TRUNCATE]
            raise ProviderError(
                f"dashscope request to {url} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"dashscope request to {url} failed: {exc}") from exc

    def _download(self, url: str, target: Path) -> None:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"dashscope video download failed: {exc}") from exc

    # -- Provider interface --------------------------------------------------

    def render_shot(self, ctx: ShotContext) -> Path:
        shot = ctx.shot
        budget = ctx.max_render_s or DEFAULT_BUDGET_S
        deadline = time.monotonic() + budget
        width, height = ctx.series.resolution

        payload = {
            "model": self.model,
            "input": {"prompt": build_shot_prompt(ctx)},
            # TODO(verify-live): wan2.7-era models take
            # {"resolution": "720P"|"1080P", "ratio": "9:16", "duration": n}.
            "parameters": {"size": f"{width}*{height}"},
        }
        submitted = self._request(
            f"{self._base_url}/services/aigc/video-generation/video-synthesis",
            payload,
            async_header=True,
        )
        task_id = str(((submitted.get("output") or {}).get("task_id")) or "")
        if not task_id:
            raise ProviderError(
                f"dashscope submit for shot '{shot.id}' returned no task_id: "
                f"{json.dumps(submitted)[:_BODY_TRUNCATE]}"
            )

        while True:
            if time.monotonic() > deadline:
                raise ProviderError(
                    f"dashscope render for shot '{shot.id}' timed out after "
                    f"{budget:g}s (task {task_id})"
                )
            status = self._request(f"{self._base_url}/tasks/{task_id}")
            output = status.get("output") or {}
            state = str(output.get("task_status", "")).upper()
            if state == "SUCCEEDED":
                break
            if state in _TERMINAL_FAILURES:
                raise ProviderError(
                    f"dashscope render for shot '{shot.id}' failed "
                    f"({state}): {json.dumps(status)[:_BODY_TRUNCATE]}"
                )
            time.sleep(min(POLL_INTERVAL_S, max(0.1, deadline - time.monotonic())))

        video_url = output.get("video_url")
        if not video_url:
            results = output.get("results") or []
            if results and isinstance(results, list) and isinstance(results[0], dict):
                video_url = results[0].get("video_url") or results[0].get("url")
        if not video_url:
            raise ProviderError(
                f"dashscope response for shot '{shot.id}' has no video_url: "
                f"{json.dumps(output)[:_BODY_TRUNCATE]}"
            )
        self._download(str(video_url), ctx.output_path)
        if not ctx.output_path.is_file() or ctx.output_path.stat().st_size == 0:
            raise ProviderError(
                f"dashscope render for shot '{shot.id}' produced no clip at "
                f"{ctx.output_path}"
            )
        return ctx.output_path
