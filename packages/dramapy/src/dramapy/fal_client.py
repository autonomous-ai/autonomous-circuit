"""Shared fal.ai client — one HTTP path for every fal-backed stage.

fal has two call shapes; this wraps both:

* **queue** (slow jobs: video, some image) — ``POST queue.fal.run/<model>`` →
  ``{request_id, status_url, response_url}``, poll status until COMPLETED,
  then GET the response.
* **sync** (fast jobs: image, tts, music) — ``POST fal.run/<model>`` returns
  the result directly.

Every stage (keyframe image, image-to-video, ElevenLabs voice, music, SFX)
goes through :class:`FalClient` so auth, polling, error surfacing, timeouts,
and image passing live in exactly one place. Network code is isolated here
and never exercised by tests — the offline provider/voice/music tests fake
``FalClient`` methods.

Env: ``FAL_KEY`` (required). No other config.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from dramapy.errors import ProviderError

QUEUE_BASE = "https://queue.fal.run"
SYNC_BASE = "https://fal.run"
DEFAULT_POLL_INTERVAL_S = 3.0
# Per-call budget for a single fal model run (submit → poll → fetch). Hosted
# video is slow: a 4K multi-character keyframe or i2v legitimately runs many
# minutes, and fal queue time counts against it — 600s was too tight and timed
# out the complex battle shots. Generous default, env-overridable.
DEFAULT_BUDGET_S = float(os.environ.get("VIDEO_FAL_BUDGET_S") or 1800.0)
_BODY_TRUNCATE = 400

_COMPLETE = {"COMPLETED", "OK", "SUCCESS"}
_FAILED = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}


class FalClient:
    """Thin fal.ai caller. Construct once (needs FAL_KEY), reuse per render."""

    def __init__(self, key: str | None = None) -> None:
        resolved = (key or os.environ.get("FAL_KEY", "")).strip()
        if not resolved:
            raise ProviderError(
                "fal calls require the FAL_KEY environment variable "
                "(https://fal.ai/dashboard/keys)"
            )
        self._key = resolved
        try:
            self._poll_s = float(os.environ.get("VIDEO_FAL_POLL_S", "") or DEFAULT_POLL_INTERVAL_S)
        except ValueError:
            self._poll_s = DEFAULT_POLL_INTERVAL_S

    # -- low-level HTTP ------------------------------------------------------

    def _http(self, url: str, payload: dict | None, method: str) -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={
                "Authorization": f"Key {self._key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:_BODY_TRUNCATE]
            raise ProviderError(f"fal {method} {url} → HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"fal {method} {url} failed: {exc}") from exc

    # -- public: run a model ------------------------------------------------

    def run(
        self,
        model_id: str,
        payload: dict,
        *,
        budget_s: float = DEFAULT_BUDGET_S,
        label: str | None = None,
    ) -> dict:
        """Run a fal model via the queue API and return its result dict.

        Submits, polls status until terminal, fetches the response. Raises
        :class:`ProviderError` on failure or timeout. ``label`` names the
        stage in error messages (e.g. ``"keyframe s1_02"``).
        """
        tag = label or model_id
        submitted = self._http(f"{QUEUE_BASE}/{model_id}", payload, "POST")
        status_url = submitted.get("status_url")
        response_url = submitted.get("response_url")
        # Some sync-style models return the result directly from submit.
        if not status_url or not response_url:
            if submitted.get("status") is None and (
                "images" in submitted or "video" in submitted or "audio" in submitted
            ):
                return submitted
            raise ProviderError(
                f"fal {tag}: submit returned no queue urls: "
                f"{json.dumps(submitted)[:_BODY_TRUNCATE]}"
            )

        deadline = time.monotonic() + budget_s
        while True:
            if time.monotonic() > deadline:
                raise ProviderError(f"fal {tag}: timed out after {budget_s:g}s")
            status = self._http(str(status_url), None, "GET")
            state = str(status.get("status", "")).upper()
            if state in _COMPLETE:
                break
            if state in _FAILED:
                raise ProviderError(
                    f"fal {tag}: failed ({state}): {json.dumps(status)[:_BODY_TRUNCATE]}"
                )
            if self._poll_s > 0:
                time.sleep(min(self._poll_s, max(0.0, deadline - time.monotonic())))

        return self._http(str(response_url), None, "GET")

    # -- public: download an output ----------------------------------------

    def download(self, url: str, target: Path) -> Path:
        request = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"fal download {url} failed: {exc}") from exc
        if not target.is_file() or target.stat().st_size == 0:
            raise ProviderError(f"fal download produced no file at {target}")
        return target

    # -- public: pass a local image to a fal model -------------------------

    @staticmethod
    def data_uri(image_path: Path) -> str:
        """Base64 ``data:`` URI for a local image — fal ``image_url`` params
        accept a URL or a data URI, so this avoids a separate upload for the
        rare local-file input (most inputs are already fal output URLs)."""
        mime = mimetypes.guess_type(str(image_path))[0] or "image/png"
        raw = Path(image_path).read_bytes()
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def first_url(result: dict, *keys: str) -> str | None:
    """Pull the first output URL from a fal result. Tries ``video.url``,
    ``audio.url``, ``images[0].url``, ``audio_url``, etc."""
    for key in keys or ("video", "audio", "image"):
        node = result.get(key)
        if isinstance(node, dict) and node.get("url"):
            return str(node["url"])
    for list_key in ("images", "audios", "videos"):
        node = result.get(list_key)
        if isinstance(node, list) and node and isinstance(node[0], dict) and node[0].get("url"):
            return str(node[0]["url"])
    for flat in ("video_url", "audio_url", "image_url", "url"):
        if isinstance(result.get(flat), str):
            return result[flat]
    return None
