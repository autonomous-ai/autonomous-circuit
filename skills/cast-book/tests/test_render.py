"""cast-book --render: turnaround-sheet PIXEL generation (offline).

Exercised in-process (not via subprocess) so the fal client can be faked. Uses
the REAL dramapy.refstack so the "front is t2i, the rest edit from the base"
contract is what actually runs; only the network client is a stand-in.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
CLI_PATH = SKILL_DIR / "scripts" / "cast" / "cli.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("cast_cli_under_test", CLI_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SERIES_TEMPLATE = '''"""Series bible for tests."""

from dramalib.bible import Character, Series

SERIES = Series(
    title="Test Series",
    genre="revenge",
    style="photoreal-drama",
)

# CAST-BOOK BEGIN
CAST = [
    Character(
        id="li_wei",
        name="Li Wei",
        look="woman, 28, sharp black bob, gray suit",
        voice="f_low_calm",
        ref_images=[],
    ),
]
# CAST-BOOK END

TOTAL_EPISODES = 60
'''


class FakeFal:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self._n = 0

    def run(self, model_id, payload, *, budget_s=None, label=None):
        with self._lock:
            self._n += 1
            n = self._n
            self.calls.append(model_id)
        if "/edit" in model_id:
            return {"images": [{"url": f"https://fal.media/edit/{n}.jpg"}]}
        return {"images": [{"url": f"https://fal.media/t2i/{n}.jpg"}]}

    def download(self, url, target):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"\x00" * 256)
        return target


def test_render_generates_pixels_and_wires_ref_images(tmp_path: Path):
    (tmp_path / "series.py").write_text(SERIES_TEMPLATE, encoding="utf-8")
    cli = _load_cli()
    fake = FakeFal()
    cli.CLIENT_FACTORY = lambda: fake
    try:
        rc = cli.main([str(tmp_path), "--render"])
    finally:
        cli.CLIENT_FACTORY = None
    assert rc == 0

    cast_dir = tmp_path / "cast" / "li_wei"
    # A full turnaround SET rendered: 1 base t2i + 3 edits.
    assert fake.calls[0] == "fal-ai/nano-banana-pro"
    assert sum("/edit" in m for m in fake.calls) == 3
    assert (cast_dir / "ref_front.jpg").is_file()
    assert (cast_dir / "refset.json").is_file()
    manifest = json.loads((cast_dir / "refset.json").read_text())
    assert [v["slot"] for v in manifest["views"]] == [
        "front", "expression", "three_quarter", "profile"
    ]
    # The rendered jpgs were wired into the guarded CAST block.
    block = (tmp_path / "series.py").read_text().split("# CAST-BOOK BEGIN")[1]
    assert "cast/li_wei/ref_front.jpg" in block


def test_render_without_dramapy_degrades_gracefully(tmp_path: Path, monkeypatch):
    (tmp_path / "series.py").write_text(SERIES_TEMPLATE, encoding="utf-8")
    cli = _load_cli()
    monkeypatch.setattr(cli, "_import_refstack", lambda: None)
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main([str(tmp_path), "--render"])
    assert rc == 0
    payload = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["cast"][0]["rendered"] == 0
    assert "render_note" in payload
