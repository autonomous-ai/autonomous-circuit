"""cinematic provider — offline flow tests.

The network transport is never exercised in CI (no key, no sockets). But the
*orchestration* is pure logic and must be right before the first live call:

  turnaround SETS per character (once, thread-safe) → world anchors per
  location/prop (once, thread-safe) → a reference-STACK keyframe edit (with
  graceful fallback) → Kling image-to-video → post ceiling → download.

These tests fake the shared :class:`FalClient` by replacing its ``run`` /
``download`` on the provider's client instance (mirroring
``test_minimax_provider``'s in-memory transport).
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from dramapy.bible import Character, Series  # noqa: E402
from dramapy.errors import ProviderError  # noqa: E402
from dramapy.providers.base import ShotContext  # noqa: E402
from dramapy.providers.cinematic import (  # noqa: E402
    DEFAULT_FALLBACK_KEYFRAME_MODEL,
    DEFAULT_I2V_MODEL,
    DEFAULT_KEYFRAME_MODEL,
    DEFAULT_PORTRAIT_MODEL,
    CinematicProvider,
    _clamp_kling_duration,
)
from dramapy.spec import Shot  # noqa: E402
from dramapy.voices import voice_for_tag_elevenlabs  # noqa: E402

MEI = Character(id="mei", name="Mei", look="28, sharp bob, gray suit", voice="f_low")
# 4 turnaround gens per new character: 1 t2i base + 3 edits.
TURNAROUND_T2I = 1
TURNAROUND_EDITS = 3


class FakeFal:
    """In-memory stand-in for FalClient. Distinguishes the model families by id
    and returns real fal response shapes with distinct, traceable urls, so a
    test can prove exactly which reference went into which call. Every counter
    is guarded — a double-generation must come from the provider, never here."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.labels: list[str] = []
        self.payloads: list[tuple[str, dict]] = []
        self.returns: list[tuple[str, str]] = []  # (model_id, returned primary url)
        self.downloads: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._n = 0

    def run(self, model_id, payload, *, budget_s=None, label=None):
        with self._lock:
            self._n += 1
            n = self._n
            self.calls.append(model_id)
            self.labels.append(label or "")
            self.payloads.append((model_id, dict(payload)))
        out = self._respond(model_id, n)
        # record the primary url this call returned
        url = ""
        for k in ("images",):
            if k in out:
                url = out[k][0]["url"]
        for k in ("video", "audio"):
            if k in out:
                url = out[k]["url"]
        with self._lock:
            self.returns.append((model_id, url))
        return out

    @staticmethod
    def _respond(model_id: str, n: int) -> dict:
        if "elevenlabs" in model_id:          # lip-sync sync-target voice
            return {"audio": {"url": f"https://fal.media/eleven/{n}.mp3"}}
        if "sync-lipsync" in model_id:        # post: lip-sync
            return {"video": {"url": f"https://fal.media/lipsync/{n}.mp4"}}
        if "topaz" in model_id:               # post: upscale
            return {"video": {"url": f"https://fal.media/topaz/{n}.mp4"}}
        if "kling" in model_id:               # image-to-video
            return {"video": {"url": f"https://fal.media/kling/{n}.mp4"}}
        if "nano-banana" in model_id and "/edit" in model_id:   # stack/turnaround edit
            return {"images": [{"url": f"https://fal.media/nbedit/{n}.jpg"}]}
        if "seedream" in model_id:            # single-ref fallback edit
            return {"images": [{"url": f"https://fal.media/seedream/{n}.jpg"}]}
        # nano-banana-pro t2i: base view / world ref / cast-less keyframe.
        return {"images": [{"url": f"https://fal.media/nb/{n}.jpg"}]}

    def download(self, url, target):
        with self._lock:
            self.downloads.append((url, str(target)))
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x00" * 2048)  # non-empty artifact
        return target

    # -- counting helpers ----------------------------------------------------
    def count(self, needle: str) -> int:
        return sum(needle in m for m in self.calls)

    def n_t2i(self) -> int:
        return sum(m.endswith("nano-banana-pro") for m in self.calls)

    def n_nano_edit(self) -> int:
        return sum(m.endswith("nano-banana-pro/edit") for m in self.calls)

    def n_seedream(self) -> int:
        return sum("seedream" in m for m in self.calls)

    def payload_for(self, needle: str) -> dict:
        for model_id, payload in self.payloads:
            if needle in model_id:
                return payload
        raise AssertionError(f"no call matching {needle!r} in {self.calls}")

    def payload_before(self, needle: str) -> dict:
        for i, m in enumerate(self.calls):
            if needle in m:
                assert i > 0, f"{needle!r} was the first call"
                return self.payloads[i - 1][1]
        raise AssertionError(f"no call matching {needle!r} in {self.calls}")

    def returned(self, needle: str) -> str:
        hits = [u for m, u in self.returns if needle in m]
        assert hits, f"no return for {needle!r}"
        return hits[-1]

    def labels_startswith(self, prefix: str) -> int:
        return sum(lbl.startswith(prefix) for lbl in self.labels)


def _series() -> Series:
    return Series(
        title="Neon", genre="revenge", style="photoreal-drama",
        aspect="9:16", resolution=(1080, 1920), fps=24, language="en",
    )


def _ctx(root: Path, shot: Shot, characters=(MEI,), location="", props=()) -> ShotContext:
    # output must be <root>/episodes/<ep>_shots/shot_*.mp4 so parents[2] == root.
    out = root / "episodes" / "ep001_shots" / f"shot_{shot.id}.mp4"
    return ShotContext(
        shot=shot, series=_series(), characters=tuple(characters),
        output_path=out, max_render_s=30, scene_id="s1",
        location=location, props=tuple(props),
    )


def _dialogue(shot_id="s1_02", duration=5.0) -> Shot:
    return Shot(id=shot_id, kind="dialogue", duration_s=duration,
                cast=["mei"], line="Still here.", prompt="close on her face",
                emotion="cold")


def _provider(fake: FakeFal) -> CinematicProvider:
    provider = CinematicProvider()
    provider._client.run = fake.run          # type: ignore[method-assign]
    provider._client.download = fake.download  # type: ignore[method-assign]
    return provider


_TRACKED_ENV = (
    "FAL_KEY", "VIDEO_FAL_POLL_S", "VIDEO_CINEMATIC_PORTRAIT_MODEL",
    "VIDEO_KEYFRAME_MODEL", "VIDEO_CINEMATIC_KEYFRAME_MODEL",
    "VIDEO_CINEMATIC_FALLBACK_KEYFRAME_MODEL", "VIDEO_CINEMATIC_I2V_MODEL",
    "VIDEO_KEYFRAME_RESOLUTION", "VIDEO_REFSTACK_CAP",
    "VIDEO_LIPSYNC", "VIDEO_UPSCALE",
)


def _reset_env() -> None:
    for k in _TRACKED_ENV:
        if k not in ("FAL_KEY", "VIDEO_FAL_POLL_S"):
            os.environ.pop(k, None)


class CinematicFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = {k: os.environ.get(k) for k in _TRACKED_ENV}
        os.environ["FAL_KEY"] = "test-key"
        os.environ["VIDEO_FAL_POLL_S"] = "0"
        # These tests exercise the pure refset->stack->i2v->download path; the
        # post ceiling is covered separately (CinematicPostProcessTest).
        os.environ["VIDEO_LIPSYNC"] = "off"
        os.environ["VIDEO_UPSCALE"] = "off"
        _reset_env()
        self.root = Path(tempfile.mkdtemp(prefix="dramapy-cinematic-"))

    def tearDown(self) -> None:
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # (a) full flow: turnaround set -> stack keyframe edit -> i2v -> download.
    def test_full_flow_turnaround_then_stack_keyframe_then_i2v(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue())
        out = provider.render_shot(ctx)

        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        # 1 base t2i + 3 turnaround edits + 1 keyframe edit + 1 kling.
        self.assertEqual(fake.n_t2i(), TURNAROUND_T2I)
        self.assertEqual(fake.n_nano_edit(), TURNAROUND_EDITS + 1)
        self.assertEqual(fake.count("kling"), 1)
        self.assertEqual(fake.calls[0], DEFAULT_PORTRAIT_MODEL)   # base view t2i
        # the keyframe edit (the edit immediately before kling) carried a STACK
        # of the character's front + expression views, forced to 9:16 at 2K.
        kf = fake.payload_before("kling")
        self.assertEqual(kf["image_urls"],
                         ["https://fal.media/nb/1.jpg",       # front (t2i)
                          "https://fal.media/nbedit/2.jpg"])  # expression (edit)
        self.assertEqual(kf["aspect_ratio"], "9:16")
        self.assertEqual(kf["resolution"], "2K")
        # i2v was seeded by the keyframe url.
        i2v = fake.payload_for("kling")
        self.assertEqual(i2v["image_url"], fake.returned("nano-banana-pro/edit"))

    # (b) turnaround set generated ONCE and reused across two shots.
    def test_turnaround_set_generated_once_and_reused(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        provider.render_shot(_ctx(self.root, _dialogue("s1_01")))
        provider.render_shot(_ctx(self.root, _dialogue("s1_02")))

        # one turnaround SET for the whole episode; both shots reuse it.
        self.assertEqual(fake.n_t2i(), TURNAROUND_T2I)               # base once
        self.assertEqual(fake.n_nano_edit(), TURNAROUND_EDITS + 2)   # 3 set + 1/shot
        self.assertEqual(fake.count("kling"), 2)
        manifest = self.root / "cast" / "mei" / "refset.json"
        self.assertTrue(manifest.is_file())
        views = json.loads(manifest.read_text())["views"]
        self.assertEqual([v["slot"] for v in views],
                         ["front", "expression", "three_quarter", "profile"])
        # both keyframe edits stacked the SAME reference set.
        kf_urls = {tuple(p["image_urls"]) for m, p in fake.payloads
                   if m.endswith("/edit") and len(p["image_urls"]) >= 2}
        self.assertEqual(kf_urls,
                         {("https://fal.media/nb/1.jpg", "https://fal.media/nbedit/2.jpg")})
        # turnaround pixels cached on disk.
        self.assertTrue((self.root / "cast" / "mei" / "ref_front.jpg").is_file())

    # (c) STACK carries cast + location + props, capped, in priority order.
    def test_stack_assembles_cast_location_and_props(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue(), location="neon lobby",
                   props=("brass key", "red umbrella"))
        provider.render_shot(ctx)

        kf = fake.payload_before("kling")
        urls = kf["image_urls"]
        # location + both props reached the keyframe edit alongside the cast set.
        loc_json = self.root / "world" / "loc-neon-lobby" / "ref.json"
        self.assertTrue(loc_json.is_file())
        loc_url = json.loads(loc_json.read_text())["url"]
        self.assertIn(loc_url, urls)
        self.assertIn("https://fal.media/nb/1.jpg", urls)  # character front
        self.assertEqual(len(urls), 5)  # front, loc, expression, key, umbrella
        # world refs are generated once and cached under world/<slug>/.
        self.assertTrue((self.root / "world" / "prop-brass-key" / "ref.jpg").is_file())
        self.assertTrue((self.root / "world" / "prop-red-umbrella" / "ref.jpg").is_file())

    def test_stack_respects_cap(self) -> None:
        os.environ["VIDEO_REFSTACK_CAP"] = "2"
        fake = FakeFal()
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue(), location="neon lobby",
                   props=("brass key",))
        provider.render_shot(ctx)
        kf = fake.payload_before("kling")
        # cap=2 keeps the character front + the location, drops the rest.
        self.assertEqual(len(kf["image_urls"]), 2)

    # (d) location + prop refs generated once, thread-safe under concurrency.
    def test_world_refs_generated_once_under_concurrency(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        shots = [_dialogue("s1_01"), _dialogue("s1_02")]
        barrier = threading.Barrier(len(shots))

        def worker(shot):
            barrier.wait()  # release both threads into render_shot together
            return provider.render_shot(
                _ctx(self.root, shot, location="neon lobby", props=("brass key",))
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(shots)) as pool:
            outs = list(pool.map(worker, shots))

        self.assertTrue(all(p.is_file() and p.stat().st_size > 0 for p in outs))
        # character base + location + prop each generated exactly once.
        self.assertEqual(fake.labels_startswith("location"), 1)
        self.assertEqual(fake.labels_startswith("prop"), 1)
        self.assertEqual(fake.n_t2i(), TURNAROUND_T2I + 2)  # base + loc + prop
        self.assertEqual(len(list((self.root / "world" / "loc-neon-lobby").glob("ref.json"))), 1)

    # (e) concurrent shots must not double-generate a character set.
    def test_concurrent_shots_do_not_double_generate_refset(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        shots = [_dialogue("s1_01"), _dialogue("s1_02")]
        barrier = threading.Barrier(len(shots))

        def worker(shot):
            barrier.wait()
            return provider.render_shot(_ctx(self.root, shot))

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(shots)) as pool:
            list(pool.map(worker, shots))

        self.assertEqual(fake.n_t2i(), TURNAROUND_T2I)  # set generated exactly once
        self.assertEqual(len(list((self.root / "cast" / "mei").glob("refset.json"))), 1)

    # (f) a character whose bible already pins ref_images skips generation.
    def test_supplied_ref_images_skip_turnaround(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        casted = Character(id="mei", name="Mei", look="x", voice="f_low",
                           ref_images=["https://cdn.example/mei_a.jpg",
                                       "https://cdn.example/mei_b.jpg"])
        provider.render_shot(_ctx(self.root, _dialogue(), characters=(casted,)))

        self.assertEqual(fake.n_t2i(), 0)          # no base t2i generated
        self.assertEqual(fake.count("kling"), 1)
        kf = fake.payload_before("kling")
        self.assertEqual(kf["image_urls"],
                         ["https://cdn.example/mei_a.jpg", "https://cdn.example/mei_b.jpg"])

    # (g) establish shot, no cast/location -> plain t2i keyframe (no edit).
    def test_establish_no_cast_uses_t2i_keyframe(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        shot = Shot(id="s0_00", kind="establish", duration_s=5.0,
                    prompt="neon-lit rainy lobby at night")
        out = provider.render_shot(_ctx(self.root, shot, characters=()))

        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        self.assertEqual(fake.n_nano_edit(), 0)   # no references -> no edit
        self.assertEqual(fake.n_t2i(), 1)         # the t2i keyframe
        self.assertEqual(fake.count("kling"), 1)
        self.assertEqual(fake.payload_before("kling")["aspect_ratio"], "9:16")

    # (h) establish shot WITH a location -> the location ref anchors the edit.
    def test_establish_with_location_edits_from_location_ref(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        shot = Shot(id="s0_00", kind="establish", duration_s=5.0,
                    prompt="wide of the lobby")
        provider.render_shot(_ctx(self.root, shot, characters=(), location="neon lobby"))
        kf = fake.payload_before("kling")
        loc_url = json.loads(
            (self.root / "world" / "loc-neon-lobby" / "ref.json").read_text())["url"]
        self.assertEqual(kf["image_urls"], [loc_url])  # single-anchor edit

    # (i) graceful fallback: stack edit fails -> single-ref Seedream edit.
    def test_keyframe_falls_back_to_single_ref_seedream(self) -> None:
        fake = FailFal(fail_on="nano-banana-pro/edit")
        provider = _provider(fake)
        # supplied refs so the turnaround (also nano edit) is skipped and only
        # the keyframe edit exercises the failing model.
        casted = Character(id="mei", name="Mei", look="x", voice="f_low",
                           ref_images=["https://cdn.example/mei_a.jpg",
                                       "https://cdn.example/mei_b.jpg"])
        out = provider.render_shot(_ctx(self.root, _dialogue(), characters=(casted,)))

        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        self.assertEqual(fake.count("nano-banana-pro/edit"), 1)  # attempted
        self.assertEqual(fake.n_seedream(), 1)                   # fallback used
        fb = fake.payload_for("seedream")
        self.assertEqual(fb["image_urls"], ["https://cdn.example/mei_a.jpg"])  # single ref
        # i2v seeded by the fallback keyframe.
        self.assertEqual(fake.payload_for("kling")["image_url"], fake.returned("seedream"))

    # (j) full fallthrough: both edits fail -> plain t2i keyframe still renders.
    def test_keyframe_falls_all_the_way_to_t2i(self) -> None:
        fake = FailFal(fail_on="/edit")  # both nano edit AND seedream edit fail
        provider = _provider(fake)
        casted = Character(id="mei", name="Mei", look="x", voice="f_low",
                           ref_images=["https://cdn.example/mei_a.jpg"])
        out = provider.render_shot(_ctx(self.root, _dialogue(), characters=(casted,)))

        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        self.assertEqual(fake.count("/edit"), 2)   # nano edit + seedream both tried
        self.assertEqual(fake.n_t2i(), 1)          # t2i keyframe rescued the shot
        self.assertEqual(fake.payload_for("kling")["image_url"], fake.returned("nano-banana-pro"))

    # (k) env override: VIDEO_KEYFRAME_MODEL swaps the stack editor.
    def test_video_keyframe_model_env_override(self) -> None:
        os.environ["VIDEO_KEYFRAME_MODEL"] = "fal-ai/flux-pro/kontext/max/multi"
        casted = Character(id="mei", name="Mei", look="x", voice="f_low",
                           ref_images=["https://cdn.example/mei_a.jpg"])
        fake = FakeFal()
        provider = _provider(fake)
        provider.render_shot(_ctx(self.root, _dialogue(), characters=(casted,)))
        self.assertEqual(fake.count("kontext"), 1)  # the override edited the keyframe

    # (l) duration clamps to the Kling {5, 10} enum.
    def test_duration_clamps_to_5_or_10(self) -> None:
        self.assertEqual(_clamp_kling_duration(4.0), "5")
        self.assertEqual(_clamp_kling_duration(7.5), "10")
        self.assertEqual(_clamp_kling_duration(15.0), "10")
        for duration, expected in ((4.0, "5"), (9.0, "10")):
            fake = FakeFal()
            provider = _provider(fake)
            provider.render_shot(_ctx(self.root, _dialogue(f"d{int(duration)}", duration)))
            self.assertEqual(fake.payload_for("kling")["duration"], expected)

    # (m) missing FAL_KEY raises before any socket opens.
    def test_missing_fal_key_raises(self) -> None:
        os.environ.pop("FAL_KEY", None)
        with self.assertRaises(ProviderError):
            CinematicProvider()

    def test_default_model_ids_are_the_verified_schemas(self) -> None:
        # Guards against a silent default drift away from the live-verified ids.
        self.assertEqual(DEFAULT_PORTRAIT_MODEL, "fal-ai/nano-banana-pro")
        self.assertEqual(DEFAULT_KEYFRAME_MODEL, "fal-ai/nano-banana-pro/edit")
        self.assertEqual(DEFAULT_FALLBACK_KEYFRAME_MODEL,
                         "fal-ai/bytedance/seedream/v4/edit")
        self.assertEqual(DEFAULT_I2V_MODEL,
                         "fal-ai/kling-video/v2.5-turbo/pro/image-to-video")


class FailFal(FakeFal):
    """FakeFal that raises ProviderError for any model id containing ``fail_on``
    (records the attempt so tests can assert the step was tried, then failed)."""

    def __init__(self, fail_on: str) -> None:
        super().__init__()
        self._fail_on = fail_on

    def run(self, model_id, payload, *, budget_s=None, label=None):
        if self._fail_on in model_id:
            with self._lock:
                self.calls.append(model_id)
                self.labels.append(label or "")
                self.payloads.append((model_id, dict(payload)))
            raise ProviderError(f"boom {model_id}")
        return super().run(model_id, payload, budget_s=budget_s, label=label)


class CinematicPostProcessTest(unittest.TestCase):
    """The post ceiling: lip-sync (dialogue only) + upscale (every shot),
    chained on fal urls, best-effort, single final download."""

    def setUp(self) -> None:
        self._prev = {k: os.environ.get(k) for k in _TRACKED_ENV}
        os.environ["FAL_KEY"] = "test-key"
        os.environ["VIDEO_FAL_POLL_S"] = "0"
        _reset_env()  # post steps default ON when FAL_KEY is set
        self.root = Path(tempfile.mkdtemp(prefix="dramapy-cinematic-post-"))

    def tearDown(self) -> None:
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _clip_download_url(fake: FakeFal, ctx) -> str:
        """The url downloaded to the clip path (not the debug ref/keyframe)."""
        target = str(ctx.output_path)
        clip = [url for url, dst in fake.downloads if dst == target]
        assert len(clip) == 1, f"expected exactly one clip download, got {clip}"
        return clip[0]

    # lip-sync + upscale both run on a dialogue shot; the chain is url-only and
    # only the final (upscaled) url is downloaded to the clip path.
    def test_dialogue_shot_lipsyncs_then_upscales(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue())
        out = provider.render_shot(ctx)

        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        self.assertEqual(fake.count("elevenlabs"), 1)   # sync-target voice
        self.assertEqual(fake.count("sync-lipsync"), 1)  # lip-sync
        self.assertEqual(fake.count("topaz"), 1)         # upscale
        # Chained on fal urls: kling -> lipsync -> topaz, no premature download.
        ls = fake.payload_for("sync-lipsync")
        self.assertEqual(ls["video_url"], fake.returned("kling"))
        self.assertEqual(ls["audio_url"], fake.returned("elevenlabs"))
        up = fake.payload_for("topaz")
        self.assertEqual(up["video_url"], fake.returned("sync-lipsync"))
        self.assertEqual(up["upscale_factor"], 2)
        # Only the FINAL (upscaled) url is downloaded to the clip path.
        self.assertEqual(self._clip_download_url(fake, ctx), fake.returned("topaz"))

    # upscale runs on every shot; lip-sync does NOT run on a non-dialogue shot.
    def test_upscale_applies_to_non_dialogue_but_lipsync_does_not(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        shot = Shot(id="s0_00", kind="establish", duration_s=5.0,
                    prompt="neon-lit rainy lobby at night")
        ctx = _ctx(self.root, shot, characters=())
        provider.render_shot(ctx)

        self.assertEqual(fake.count("elevenlabs"), 0)   # no line -> no voice
        self.assertEqual(fake.count("sync-lipsync"), 0)  # not a dialogue shot
        self.assertEqual(fake.count("topaz"), 1)         # upscale still runs
        self.assertEqual(self._clip_download_url(fake, ctx), fake.returned("topaz"))

    # the eleven-v3 sync target uses the SAME voice mapping as assembly.
    def test_lipsync_voice_and_text_match_assembly_mapping(self) -> None:
        fake = FakeFal()
        provider = _provider(fake)
        shot = Shot(id="s1_09", kind="dialogue", duration_s=5.0, cast=["mei"],
                    line="Rise!", prompt="close", emotion="angry")
        provider.render_shot(_ctx(self.root, shot))

        voice_payload = fake.payload_for("elevenlabs")
        self.assertEqual(voice_payload["voice"], voice_for_tag_elevenlabs(MEI.voice))
        self.assertEqual(voice_payload["text"], "[angry] Rise!")

    # a lip-sync failure falls back to the un-synced clip; the shot never fails.
    def test_lipsync_failure_falls_back_to_unsynced_clip(self) -> None:
        os.environ["VIDEO_UPSCALE"] = "off"  # isolate the lip-sync fallback
        fake = FailFal(fail_on="sync-lipsync")
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue())
        out = provider.render_shot(ctx)

        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        self.assertEqual(fake.count("sync-lipsync"), 1)  # attempted
        self.assertEqual(self._clip_download_url(fake, ctx), fake.returned("kling"))

    # an upscale failure falls back to the previous (lip-synced) url.
    def test_upscale_failure_falls_back_to_previous_clip(self) -> None:
        fake = FailFal(fail_on="topaz")
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue())
        out = provider.render_shot(ctx)

        self.assertTrue(out.is_file() and out.stat().st_size > 0)
        self.assertEqual(fake.count("topaz"), 1)  # attempted
        self.assertEqual(self._clip_download_url(fake, ctx), fake.returned("sync-lipsync"))

    # a failure generating the sync-target audio also falls back (no lip-sync).
    def test_lipsync_audio_failure_falls_back(self) -> None:
        os.environ["VIDEO_UPSCALE"] = "off"
        fake = FailFal(fail_on="elevenlabs")
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue())
        provider.render_shot(ctx)

        self.assertEqual(fake.count("elevenlabs"), 1)   # attempted
        self.assertEqual(fake.count("sync-lipsync"), 0)  # skipped: no audio url
        self.assertEqual(self._clip_download_url(fake, ctx), fake.returned("kling"))

    # VIDEO_LIPSYNC=off skips lip-sync but upscale still runs.
    def test_lipsync_off_switch(self) -> None:
        os.environ["VIDEO_LIPSYNC"] = "off"
        fake = FakeFal()
        provider = _provider(fake)
        provider.render_shot(_ctx(self.root, _dialogue()))
        self.assertEqual(fake.count("elevenlabs"), 0)
        self.assertEqual(fake.count("sync-lipsync"), 0)
        self.assertEqual(fake.count("topaz"), 1)  # upscale unaffected

    # VIDEO_UPSCALE=off skips upscale but lip-sync still runs on dialogue.
    def test_upscale_off_switch(self) -> None:
        os.environ["VIDEO_UPSCALE"] = "off"
        fake = FakeFal()
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue())
        provider.render_shot(ctx)
        self.assertEqual(fake.count("sync-lipsync"), 1)
        self.assertEqual(fake.count("topaz"), 0)
        self.assertEqual(self._clip_download_url(fake, ctx), fake.returned("sync-lipsync"))

    # both off -> pure keyframe->i2v->download (no post calls, clip = kling).
    def test_both_off_switches(self) -> None:
        os.environ["VIDEO_LIPSYNC"] = "off"
        os.environ["VIDEO_UPSCALE"] = "off"
        fake = FakeFal()
        provider = _provider(fake)
        ctx = _ctx(self.root, _dialogue())
        provider.render_shot(ctx)
        self.assertEqual(fake.count("elevenlabs"), 0)
        self.assertEqual(fake.count("sync-lipsync"), 0)
        self.assertEqual(fake.count("topaz"), 0)
        self.assertEqual(self._clip_download_url(fake, ctx), fake.returned("kling"))


if __name__ == "__main__":
    unittest.main()
