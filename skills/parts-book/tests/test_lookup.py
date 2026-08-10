"""parts-book --lookup: catalog refresh against a FAKE jlcsearch client.

Run in-process (not via subprocess) so the client can be replaced. The real
network path is never exercised here — `_http_get_json` is monkeypatched to
explode, which is the test that the fake really is in use.
"""

from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
CLI_PATH = SKILL_DIR / "scripts" / "parts" / "cli.py"

from test_parts_book import BLOCK_MD, BLOCK_TSX  # noqa: E402


def _load_cli():
    spec = importlib.util.spec_from_file_location("parts_cli_under_test", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_project(tmp_path: Path) -> Path:
    (tmp_path / "product.json").write_text('{"name": "lookup-test"}', encoding="utf-8")
    block = tmp_path / "blocks" / "demo-block"
    block.mkdir(parents=True)
    (block / "demo-block.tsx").write_text(BLOCK_TSX, encoding="utf-8")
    (block / "BLOCK.md").write_text(BLOCK_MD, encoding="utf-8")
    return tmp_path


class FakeCatalog:
    """Shaped exactly like a jlcsearch /api/search component record."""

    CATALOG = {
        "C6186": {"lcsc": 6186, "mfr": "AMS1117-3.3", "package": "SOT-223",
                  "is_basic": True, "is_preferred": False,
                  "stock": 1490681, "price": 0.151271429},
        "C25900": {"lcsc": 25900, "mfr": "0402WGF4701TCE", "package": "0402",
                   "is_basic": True, "is_preferred": False,
                   "stock": 9379077, "price": 0.000485714},
        "C15850": {"lcsc": 15850, "mfr": "CL21A106KAYNNNE", "package": "0805",
                   "is_basic": True, "is_preferred": False,
                   "stock": 12668317, "price": 0.009142857},
    }

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, lcsc: str) -> dict:
        self.calls.append(lcsc)
        return self.CATALOG[lcsc]


def _run(cli, *args: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(list(args))
    lines = buf.getvalue().strip().splitlines()
    assert len(lines) == 1, f"expected exactly one JSON line, got {lines}"
    payload = json.loads(lines[0])
    assert (rc == 0) == payload["ok"]
    return payload


def _no_network(cli, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("tests must never reach the network")
    monkeypatch.setattr(cli, "_http_get_json", boom)


def test_lookup_fills_stock_price_and_basic(tmp_path: Path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("CIRCUIT_PARTS_CACHE_DIR", str(tmp_path / "cache"))
    cli = _load_cli()
    _no_network(cli, monkeypatch)
    fake = FakeCatalog()
    cli.LOOKUP_FN = fake

    payload = _run(cli, str(project), "--lookup")
    assert payload["ok"] is True
    assert "lookup_note" not in payload
    assert sorted(fake.calls) == ["C15850", "C25900", "C6186"]
    today = date.today().isoformat()
    assert all(p["stock_checked"] == today for p in payload["parts"])

    record = next(
        p for p in json.loads((project / "parts.json").read_text())["parts"]
        if p["lcsc"] == "C6186"
    )
    assert record["stock"] == 1490681
    assert record["unit_price_usd"] == 0.151271
    assert record["basic"] is True
    assert record["source"] == "jlcsearch"
    assert record["stock_checked"] == today


def test_lookup_uses_the_on_disk_cache(tmp_path: Path, monkeypatch):
    project = _make_project(tmp_path)
    cache = tmp_path / "cache"
    monkeypatch.setenv("CIRCUIT_PARTS_CACHE_DIR", str(cache))
    cli = _load_cli()
    _no_network(cli, monkeypatch)
    fake = FakeCatalog()
    cli.LOOKUP_FN = fake

    _run(cli, str(project), "--lookup")
    assert len(fake.calls) == 3
    assert (cache / "C6186.json").is_file()

    _run(cli, str(project), "--lookup")
    assert len(fake.calls) == 3, "second run must be served from the cache"

    _run(cli, str(project), "--lookup", "--no-cache")
    assert len(fake.calls) == 6, "--no-cache refetches"


def test_stale_cache_is_refetched(tmp_path: Path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("CIRCUIT_PARTS_CACHE_DIR", str(tmp_path / "cache"))
    cli = _load_cli()
    _no_network(cli, monkeypatch)
    fake = FakeCatalog()
    cli.LOOKUP_FN = fake

    _run(cli, str(project), "--lookup")
    _run(cli, str(project), "--lookup", "--max-age-days", "0")
    assert len(fake.calls) == 6


def test_lookup_degrades_gracefully_when_offline(tmp_path: Path, monkeypatch):
    """Offline is not a failure: the lock still lands as candidate slots and
    the JSON says why the numbers are missing."""
    project = _make_project(tmp_path)
    monkeypatch.setenv("CIRCUIT_PARTS_CACHE_DIR", str(tmp_path / "cache"))
    cli = _load_cli()
    _no_network(cli, monkeypatch)

    def offline(_lcsc: str) -> dict:
        raise RuntimeError("Temporary failure in name resolution")
    cli.LOOKUP_FN = offline

    payload = _run(cli, str(project), "--lookup")
    assert payload["ok"] is True
    assert "lookup_note" in payload
    assert "3 of 3" in payload["lookup_note"]
    assert "47-90s" in payload["lookup_note"]
    assert all(p["stock_checked"] is None for p in payload["parts"])
    on_disk = json.loads((project / "parts.json").read_text())
    assert on_disk["summary"]["checked"] == 0
    assert all(p["source"] == "block-default" for p in on_disk["parts"])


def test_partial_failure_keeps_the_parts_that_resolved(tmp_path: Path, monkeypatch):
    project = _make_project(tmp_path)
    monkeypatch.setenv("CIRCUIT_PARTS_CACHE_DIR", str(tmp_path / "cache"))
    cli = _load_cli()
    _no_network(cli, monkeypatch)

    def flaky(lcsc: str) -> dict:
        if lcsc == "C25900":
            raise RuntimeError("read timed out")
        return FakeCatalog.CATALOG[lcsc]
    cli.LOOKUP_FN = flaky

    payload = _run(cli, str(project), "--lookup")
    assert payload["ok"] is True
    assert "1 of 3" in payload["lookup_note"]
    checked = {p["id"]: p["stock_checked"] for p in payload["parts"]}
    assert checked["r-4.7k-0402"] is None
    assert checked["ams1117-3.3"] == date.today().isoformat()


def test_lookup_mismatch_is_recorded(tmp_path: Path, monkeypatch):
    """The catalog answering with a different number than we asked for is a
    fact the lock must carry, not something to smooth over."""
    project = _make_project(tmp_path)
    monkeypatch.setenv("CIRCUIT_PARTS_CACHE_DIR", str(tmp_path / "cache"))
    cli = _load_cli()
    _no_network(cli, monkeypatch)

    def wrong(_lcsc: str) -> dict:
        return {"lcsc": 111111, "mfr": "SOMETHING-ELSE", "package": "0402",
                "is_basic": False, "stock": 10, "price": 1.0}
    cli.LOOKUP_FN = wrong

    _run(cli, str(project), "--lookup")
    parts = json.loads((project / "parts.json").read_text())["parts"]
    assert all(p["lookup_mismatch"] == "C111111" for p in parts)
