"""minimax provider — MiniMax Hailuo video API, task submit + status poll +
file retrieve (founder directive 2026-08-09; recorded in
``docs/video-interfaces-CHANGES.md``).

Flow (the documented v1 task shape):

  1. ``POST {base}/v1/video_generation`` with
     ``Authorization: Bearer $MINIMAX_API_KEY`` → ``{"task_id", "base_resp"}``
  2. ``GET {base}/v1/query/video_generation?task_id=...`` until ``status``
     is ``Success`` (``Fail`` → ProviderError); success carries ``file_id``
  3. ``GET {base}/v1/files/retrieve?file_id=...`` →
     ``file.download_url`` → download to ``ctx.output_path``

Env: ``MINIMAX_API_KEY`` (required), ``VIDEO_MINIMAX_MODEL`` (optional,
default ``MiniMax-Hailuo-2.3`` — their current standard tier),
``VIDEO_MINIMAX_BASE_URL`` (optional, default the global host
``https://api.minimax.io``; mainland accounts need ``https://api.minimaxi.com``
— host and key region must match).

NETWORK-UNTESTED (honest markers, per directive):
  * TODO(verify-live): MiniMax also documents a newer v2 surface
    (``POST /v2/video_generation`` with a ``content`` array and a query
    response carrying ``task.content.url`` directly, model ``MiniMax-H3``).
    This module implements the v1 file-retrieve flow the directive names;
    the exact live model id string for the 2.3 tier should be confirmed
    against the account's model list on first real run.
  * Tests never exercise this module — constructing it without
    ``MINIMAX_API_KEY`` raises ProviderError before any socket opens.

No retries beyond the caller's single retry; failures raise ProviderError
with the vendor error body truncated.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dramapy.errors import ProviderError
from dramapy.providers.base import Provider, ShotContext, build_shot_prompt

DEFAULT_BASE_URL = "https://api.minimax.io"
DEFAULT_MODEL = "MiniMax-Hailuo-2.3"
DEFAULT_BUDGET_S = 600.0
POLL_INTERVAL_S = 5.0
_BODY_TRUNCATE = 300

_FAILURE_STATES = {"FAIL", "FAILED", "ERROR", "CANCELLED", "CANCELED"}


class MinimaxProvider(Provider):
    name = "minimax"

    def __init__(self) -> None:
        key = os.environ.get("MINIMAX_API_KEY", "").strip()
        if not key:
            raise ProviderError(
                "minimax provider requires the MINIMAX_API_KEY environment "
                "variable (MiniMax platform → API keys)"
            )
        self._key = key
        # GroupId: some MiniMax endpoints/regions require it as a query param
        # on query + file-retrieve. Harmless when the account doesn't need it,
        # so we append it whenever set.
        self._group_id = os.environ.get("MINIMAX_GROUP_ID", "").strip()
        self._base_url = (
            os.environ.get("VIDEO_MINIMAX_BASE_URL", "").strip().rstrip("/")
            or DEFAULT_BASE_URL
        )
        self.model = os.environ.get("VIDEO_MINIMAX_MODEL", "").strip() or DEFAULT_MODEL
        try:
            self._poll_s = float(os.environ.get("VIDEO_MINIMAX_POLL_S", "") or POLL_INTERVAL_S)
        except ValueError:
            self._poll_s = POLL_INTERVAL_S

    def _with_group(self, params: dict) -> dict:
        """Add GroupId to a query-param dict when the account provides one."""
        if self._group_id:
            return {**params, "GroupId": self._group_id}
        return params

    # -- HTTP plumbing (isolated here; never exercised in tests) ------------

    def _request(self, url: str, payload: dict | None = None) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST" if body is not None else "GET",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                parsed = json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:_BODY_TRUNCATE]
            raise ProviderError(
                f"minimax request to {url} failed: HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"minimax request to {url} failed: {exc}") from exc
        base_resp = parsed.get("base_resp") or {}
        if base_resp and int(base_resp.get("status_code", 0) or 0) != 0:
            raise ProviderError(
                f"minimax request to {url} returned error "
                f"{base_resp.get('status_code')}: "
                f"{str(base_resp.get('status_msg', ''))[:_BODY_TRUNCATE]}"
            )
        return parsed

    def _download(self, url: str, target: Path) -> None:
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"minimax video download failed: {exc}") from exc

    # -- Provider interface --------------------------------------------------

    def render_shot(self, ctx: ShotContext) -> Path:
        shot = ctx.shot
        budget = ctx.max_render_s or DEFAULT_BUDGET_S
        deadline = time.monotonic() + budget
        width, height = ctx.series.resolution

        payload = {
            "model": self.model,
            "prompt": build_shot_prompt(ctx),
            # Hailuo durations are 6/10s class; clamp the spec into range.
            "duration": max(6, min(10, round(shot.duration_s))),
            "resolution": "1080P" if min(width, height) >= 1080 else "768P",
        }
        submitted = self._request(f"{self._base_url}/v1/video_generation", payload)
        task_id = str(submitted.get("task_id") or "")
        if not task_id:
            raise ProviderError(
                f"minimax submit for shot '{shot.id}' returned no task_id: "
                f"{json.dumps(submitted)[:_BODY_TRUNCATE]}"
            )

        query_url = (
            f"{self._base_url}/v1/query/video_generation?"
            + urllib.parse.urlencode(self._with_group({"task_id": task_id}))
        )
        while True:
            if time.monotonic() > deadline:
                raise ProviderError(
                    f"minimax render for shot '{shot.id}' timed out after "
                    f"{budget:g}s (task {task_id})"
                )
            status = self._request(query_url)
            state = str(status.get("status", "")).upper()
            if state == "SUCCESS":
                break
            if state in _FAILURE_STATES:
                raise ProviderError(
                    f"minimax render for shot '{shot.id}' failed ({state}): "
                    f"{json.dumps(status)[:_BODY_TRUNCATE]}"
                )
            if self._poll_s > 0:
                time.sleep(min(self._poll_s, max(0.0, deadline - time.monotonic())))

        file_id = str(status.get("file_id") or "")
        if not file_id:
            raise ProviderError(
                f"minimax success for shot '{shot.id}' carried no file_id: "
                f"{json.dumps(status)[:_BODY_TRUNCATE]}"
            )
        retrieve_url = (
            f"{self._base_url}/v1/files/retrieve?"
            + urllib.parse.urlencode(self._with_group({"file_id": file_id}))
        )
        retrieved = self._request(retrieve_url)
        download_url = ((retrieved.get("file") or {}).get("download_url")) or ""
        if not download_url:
            raise ProviderError(
                f"minimax file retrieve for shot '{shot.id}' has no "
                f"download_url: {json.dumps(retrieved)[:_BODY_TRUNCATE]}"
            )
        self._download(str(download_url), ctx.output_path)
        if not ctx.output_path.is_file() or ctx.output_path.stat().st_size == 0:
            raise ProviderError(
                f"minimax render for shot '{shot.id}' produced no clip at "
                f"{ctx.output_path}"
            )
        return ctx.output_path
