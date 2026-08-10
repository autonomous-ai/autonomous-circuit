"""MiniMax provider — offline flow tests.

The network transport is never exercised in CI (no key, no sockets). But the
*orchestration* — submit → poll until Success → retrieve file → download,
plus GroupId plumbing and error surfacing — is pure logic and must be right
before the first live call. These tests monkeypatch the two HTTP methods
(`_request`, `_download`) with an in-memory fake so the whole render_shot
path runs deterministically offline.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy.bible import Character, Series  # noqa: E402
from dramapy.errors import ProviderError  # noqa: E402
from dramapy.providers.base import ShotContext  # noqa: E402
from dramapy.providers.minimax import MinimaxProvider  # noqa: E402
from dramapy.spec import Shot  # noqa: E402


def _ctx(tmp: Path, duration=4) -> ShotContext:
    series = Series(
        title="T", genre="revenge", style="photoreal-drama",
        aspect="9:16", resolution=(1080, 1920), fps=24, language="en",
    )
    shot = Shot(id="s1_02", kind="dialogue", duration_s=duration,
                cast=("mei",), line="Still here.", prompt="close on her face")
    return ShotContext(
        shot=shot, series=series,
        characters=(Character(id="mei", name="Mei", look="bob", voice="f_low"),),
        output_path=tmp / "shot.mp4", max_render_s=30,
    )


class MinimaxFlowTest(unittest.TestCase):
    def setUp(self):
        self._prev = {k: os.environ.get(k) for k in
                      ("MINIMAX_API_KEY", "MINIMAX_GROUP_ID", "VIDEO_MINIMAX_MODEL",
                       "VIDEO_MINIMAX_POLL_S")}
        os.environ["MINIMAX_API_KEY"] = "test-key"
        os.environ.pop("MINIMAX_GROUP_ID", None)
        os.environ.pop("VIDEO_MINIMAX_MODEL", None)
        os.environ["VIDEO_MINIMAX_POLL_S"] = "0"
        self.tmp = Path(tempfile.mkdtemp(prefix="dramapy-minimax-"))

    def tearDown(self):
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _wire(self, provider, *, statuses, urls_seen):
        """Install a fake transport: record every URL, script poll statuses."""
        seq = iter(statuses)

        def fake_request(url, payload=None):
            urls_seen.append(url)
            if "/v1/video_generation" in url and payload is not None:
                return {"task_id": "task-123", "base_resp": {"status_code": 0}}
            if "/v1/query/video_generation" in url:
                state = next(seq)
                out = {"status": state, "base_resp": {"status_code": 0}}
                if state == "Success":
                    out["file_id"] = "file-777"
                return out
            if "/v1/files/retrieve" in url:
                return {"file": {"download_url": "https://cdn.example/clip.mp4"},
                        "base_resp": {"status_code": 0}}
            raise AssertionError(f"unexpected url {url}")

        def fake_download(url, target):
            urls_seen.append(url)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\x00" * 2048)  # non-empty clip

        provider._request = fake_request
        provider._download = fake_download

    def test_happy_path_submit_poll_retrieve_download(self):
        provider = MinimaxProvider()
        urls = []
        self._wire(provider, statuses=["Preparing", "Processing", "Success"], urls_seen=urls)
        out = provider.render_shot(_ctx(self.tmp))
        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        # Order: one submit, three polls, one retrieve, one download.
        joined = " ".join(urls)
        self.assertIn("/v1/video_generation", urls[0])
        self.assertEqual(sum("/v1/query/" in u for u in urls), 3)
        self.assertIn("/v1/files/retrieve", joined)
        self.assertIn("cdn.example/clip.mp4", urls[-1])

    def test_group_id_appended_to_query_and_retrieve(self):
        os.environ["MINIMAX_GROUP_ID"] = "grp-42"
        provider = MinimaxProvider()
        urls = []
        self._wire(provider, statuses=["Success"], urls_seen=urls)
        provider.render_shot(_ctx(self.tmp))
        query = next(u for u in urls if "/v1/query/" in u)
        retrieve = next(u for u in urls if "/v1/files/retrieve" in u)
        self.assertIn("GroupId=grp-42", query)
        self.assertIn("GroupId=grp-42", retrieve)

    def test_failure_status_raises(self):
        provider = MinimaxProvider()
        self._wire(provider, statuses=["Processing", "Fail"], urls_seen=[])
        with self.assertRaises(ProviderError) as caught:
            provider.render_shot(_ctx(self.tmp))
        self.assertIn("s1_02", str(caught.exception))

    def test_missing_key_raises_before_any_socket(self):
        os.environ.pop("MINIMAX_API_KEY", None)
        with self.assertRaises(ProviderError):
            MinimaxProvider()

    def test_hailuo_duration_clamped_into_6_10(self):
        provider = MinimaxProvider()
        self._wire(provider, statuses=["Success"], urls_seen=[])
        captured = {}
        wired = provider._request

        def spy(url, payload=None):
            if payload is not None and "/v1/video_generation" in url:
                captured["duration"] = payload["duration"]
            return wired(url, payload)

        provider._request = spy
        provider.render_shot(_ctx(self.tmp, duration=3))
        self.assertGreaterEqual(captured["duration"], 6)
        self.assertLessEqual(captured["duration"], 10)


if __name__ == "__main__":
    unittest.main()
