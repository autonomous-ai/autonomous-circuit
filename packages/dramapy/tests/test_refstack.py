"""refstack — pure assembly + the client-injected render helpers (offline).

The stack/payload/slug logic is pure and unit-testable; ``render_turnaround_set``
takes an injected client (faked here, exactly like the provider tests) so the
"base t2i, then edit-from-base for every other view" contract is verified with
zero network.
"""

from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

import unittest  # noqa: E402

from dramapy import refstack  # noqa: E402
from dramapy.spec import ResolvedCharacter, ResolvedSeries  # noqa: E402


def _series() -> ResolvedSeries:
    return ResolvedSeries(
        title="Neon", genre="revenge", style="photoreal-drama", aspect="9:16",
        resolution=(1080, 1920), fps=24, language="en",
    )


def _char() -> ResolvedCharacter:
    return ResolvedCharacter(
        id="mei", name="Mei", look="28, sharp bob, gray suit", voice="f_low",
        ref_images=(),
    )


class FakeFal:
    """Records every run/download; returns real fal image/video shapes."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.payloads: list[tuple[str, dict]] = []
        self.downloads: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._n = 0

    def run(self, model_id, payload, *, budget_s=None, label=None):
        with self._lock:
            self._n += 1
            n = self._n
            self.calls.append(model_id)
            self.payloads.append((model_id, dict(payload)))
        if "/edit" in model_id:
            return {"images": [{"url": f"https://fal.media/edit/{n}.jpg"}]}
        return {"images": [{"url": f"https://fal.media/t2i/{n}.jpg"}]}

    def download(self, url, target):
        with self._lock:
            self.downloads.append((url, str(target)))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"\x00" * 512)
        return target


class AssembleStackTest(unittest.TestCase):
    def test_priority_order_and_dedup(self) -> None:
        sets = [["a_front", "a_expr", "a_34"], ["b_front", "b_expr"]]
        stack = refstack.assemble_reference_stack(
            sets, location_ref="loc", prop_refs=("p1", "p2"), cap=14
        )
        # primary views, then location, then extra views, then props.
        self.assertEqual(
            stack, ["a_front", "b_front", "loc", "a_expr", "b_expr", "p1", "p2"]
        )

    def test_cap_drops_the_tail_first(self) -> None:
        sets = [["a_front", "a_expr"], ["b_front", "b_expr"]]
        stack = refstack.assemble_reference_stack(
            sets, location_ref="loc", prop_refs=("p1",), cap=3
        )
        # cap=3 keeps every face + the location; props/extra views are dropped.
        self.assertEqual(stack, ["a_front", "b_front", "loc"])

    def test_dedup_preserves_first_position(self) -> None:
        sets = [["shared", "a_expr"], ["shared", "b_expr"]]
        stack = refstack.assemble_reference_stack(sets, cap=14)
        self.assertEqual(stack.count("shared"), 1)
        self.assertEqual(stack, ["shared", "a_expr", "b_expr"])

    def test_empty_is_empty(self) -> None:
        self.assertEqual(refstack.assemble_reference_stack([], cap=14), [])


class SlugAndPayloadTest(unittest.TestCase):
    def test_slugify(self) -> None:
        self.assertEqual(
            refstack.slugify("Neon-lit Rainy Lobby, at Night!"),
            "neon-lit-rainy-lobby-at-night",
        )
        self.assertEqual(refstack.slugify(""), "unknown")
        self.assertEqual(refstack.slugify("!!!"), "unknown")

    def test_nano_payloads_force_9_16(self) -> None:
        t2i = refstack.t2i_payload("fal-ai/nano-banana-pro", "p")
        self.assertEqual(t2i["aspect_ratio"], "9:16")
        self.assertEqual(t2i["resolution"], "2K")
        edit = refstack.edit_payload("fal-ai/nano-banana-pro/edit", "p", ["u1", "u2"])
        self.assertEqual(edit["aspect_ratio"], "9:16")
        self.assertEqual(edit["image_urls"], ["u1", "u2"])

    def test_seedream_payloads_use_image_size(self) -> None:
        edit = refstack.edit_payload(
            "fal-ai/bytedance/seedream/v4/edit", "p", ["u1"], size=(1080, 1920)
        )
        self.assertEqual(edit["image_size"], {"width": 1080, "height": 1920})
        self.assertEqual(edit["image_urls"], ["u1"])

    def test_resolution_override_threads_through(self) -> None:
        t2i = refstack.t2i_payload("fal-ai/nano-banana-pro", "p", resolution="4K")
        self.assertEqual(t2i["resolution"], "4K")


class TurnaroundSetTest(unittest.TestCase):
    def test_base_is_t2i_and_others_edit_from_base(self) -> None:
        fake = FakeFal()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "cast" / "mei"
            views = refstack.render_turnaround_set(
                fake, character=_char(), series=_series(),
                t2i_model="fal-ai/nano-banana-pro",
                edit_model="fal-ai/nano-banana-pro/edit",
                budget_s=30, out_dir=out,
            )
        # 4 views, front first, one t2i base + three edits.
        self.assertEqual([v["slot"] for v in views],
                         ["front", "expression", "three_quarter", "profile"])
        self.assertEqual(fake.calls[0], "fal-ai/nano-banana-pro")       # base t2i
        self.assertTrue(all(m == "fal-ai/nano-banana-pro/edit" for m in fake.calls[1:]))
        base_url = views[0]["url"]
        # every derived view is an edit fed EXACTLY the base url (same person).
        for model_id, payload in fake.payloads[1:]:
            self.assertEqual(payload["image_urls"], [base_url])
        # pixels cached next to the manifest.
        self.assertEqual(len(fake.downloads), 4)
        self.assertTrue(all(dst.endswith(".jpg") for _, dst in fake.downloads))

    def test_prop_and_location_refs_are_t2i(self) -> None:
        fake = FakeFal()
        loc = refstack.render_location_ref(
            fake, location="neon lobby", series=_series(),
            t2i_model="fal-ai/nano-banana-pro", budget_s=30,
        )
        prop = refstack.render_prop_ref(
            fake, prop="brass key", series=_series(),
            t2i_model="fal-ai/nano-banana-pro", budget_s=30,
        )
        self.assertTrue(loc.startswith("https://fal.media/t2i/"))
        self.assertTrue(prop.startswith("https://fal.media/t2i/"))
        self.assertEqual(fake.calls, ["fal-ai/nano-banana-pro"] * 2)


if __name__ == "__main__":
    unittest.main()
