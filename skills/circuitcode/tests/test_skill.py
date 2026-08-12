"""Self-contained tests for the circuitcode CLI layer.

    python -m pytest skills/circuitcode/tests -q

The suite talks to the scripts exactly like the agent does — subprocess in,
one JSON line out — with a stub circuitpy injected via
``CIRCUITCODE_TEST_CIRCUITPY_PATH`` (see conftest.py). No toolchain, no
KiCad, no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import uuid

import pytest

from conftest import GOOD_TSX, write_project

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_DIR / "scripts"
TOOLS = ("create", "circuit", "check", "review")


def _run(tool: str, *args: str, cwd: Path | None = None, timeout: int = 60):
    """Invoke ``python scripts/<tool> ...`` the way the agent does."""
    return subprocess.run(
        [sys.executable, str(SCRIPTS / tool), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
    )


def _payload(proc: subprocess.CompletedProcess) -> dict:
    """Contract §3: the parent parses ``stdout.splitlines()[-1]``."""
    lines = (proc.stdout or "").strip().splitlines()
    assert lines, f"no stdout (stderr: {proc.stderr[-600:]!r})"
    return json.loads(lines[-1])


def _run_json(tool: str, *args: str, cwd: Path | None = None, timeout: int = 60) -> dict:
    return _payload(_run(tool, *args, cwd=cwd, timeout=timeout))


def _code(payload: dict) -> str:
    return str((payload.get("error") or {}).get("code", ""))


# -- Smoke -------------------------------------------------------------------


def test_layout_intact():
    """Every CLI is a directory-runnable package with the shared runtime."""
    for name in TOOLS:
        assert (SCRIPTS / name / "__main__.py").is_file(), f"missing scripts/{name}/__main__.py"
        assert (SCRIPTS / name / "__init__.py").is_file(), f"missing scripts/{name}/__init__.py"
        assert (SCRIPTS / name / "cli.py").is_file(), f"missing scripts/{name}/cli.py"
    assert (SCRIPTS / "common" / "runner.py").is_file()
    assert (SCRIPTS / "common" / "pyversion.py").is_file()


@pytest.mark.parametrize("tool", TOOLS)
def test_help_works(tool: str):
    proc = _run(tool, "--help", timeout=30)
    assert proc.returncode == 0, f"{tool} --help failed: {proc.stderr}"
    assert "usage" in proc.stdout.lower()


def test_create_public_path_emits_planner_source_product_and_content_lock(
    tmp_path: Path,
) -> None:
    from circuitlib.starter import protected_usb_indicator_starter

    target = tmp_path / "fresh"
    proc = _run(
        "create",
        str(target),
        "--name",
        "fresh-protected-board",
        "--description",
        "a cold generated protected USB board",
        timeout=60,
    )
    payload = _payload(proc)
    assert proc.returncode == 0, (proc.stderr, payload)
    assert payload["ok"] is True
    assert payload["designProfile"] == "protected-usb-indicator-v1"
    assert payload["boardSizeMm"] == [46.9, 36.8]
    expected = protected_usb_indicator_starter(
        name="fresh-protected-board",
        description="a cold generated protected USB board",
    )
    assert (target / "boards" / "main.tsx").read_text() == expected.board_source
    assert json.loads((target / "product.json").read_text()) == expected.product
    lock = json.loads((target / "golden-blocks.lock.json").read_text())
    assert lock["treeSha256"] == payload["treeSha256"]
    assert payload["plannerBlocks"] == list(expected.block_ids)
    assert payload["blocks"] == lock["blocks"]
    assert lock["blocks"] == sorted((*expected.block_ids, "usb-c-power"))
    assert lock["source"] == "circuitcode/golden-blocks"
    assert all((target / "blocks" / path).is_file() for path in lock["files"])


def test_create_refuses_to_overwrite_even_an_existing_empty_path(tmp_path: Path) -> None:
    target = tmp_path / "owned"
    target.mkdir()
    proc = _run("create", str(target))
    payload = _payload(proc)
    assert proc.returncode == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "PROJECT_CREATE_FAILED"
    assert "refusing to overwrite" in payload["error"]["message"]


def test_create_can_atomically_initialize_only_viewer_metadata_workspace(
    tmp_path: Path,
) -> None:
    project_id = str(uuid.uuid4())
    target = tmp_path / project_id
    target.mkdir()
    metadata = (
        json.dumps(
            {
                "id": project_id,
                "name": "owned by server",
                "created_at": 1723456789000,
                "updated_at": 1723456789000,
            },
            indent=2,
        )
        + "\n"
    ).encode()
    (target / "project.json").write_bytes(metadata)

    proc = _run("create", str(target), "--initialize-existing")
    payload = _payload(proc)

    assert proc.returncode == 0, (proc.stderr, payload)
    assert payload["ok"] is True
    assert (target / "project.json").read_bytes() == metadata
    assert (target / "product.json").is_file()
    assert (target / "boards" / "main.tsx").is_file()
    assert (target / "golden-blocks.lock.json").is_file()


def test_create_existing_mode_preserves_valid_viewer_reference_inputs(
    tmp_path: Path,
) -> None:
    project_id = str(uuid.uuid4())
    target = tmp_path / project_id
    target.mkdir()
    metadata = (
        json.dumps(
            {
                "id": project_id,
                "name": "image-assisted plan",
                "created_at": 1723456789000,
                "updated_at": 1723456789000,
            },
            indent=2,
        )
        + "\n"
    ).encode()
    (target / "project.json").write_bytes(metadata)
    inputs = target / "inputs"
    inputs.mkdir()
    images = {
        f"{uuid.uuid4()}.png": b"not-decoded-by-the-generator\x00",
        f"{uuid.uuid4()}.webp": b"server-owned-reference\xff",
    }
    for name, data in images.items():
        (inputs / name).write_bytes(data)

    proc = _run("create", str(target), "--initialize-existing")
    payload = _payload(proc)

    assert proc.returncode == 0, (proc.stderr, payload)
    assert (target / "project.json").read_bytes() == metadata
    assert {
        path.name: path.read_bytes() for path in sorted(inputs.iterdir())
    } == images
    assert (target / "golden-blocks.lock.json").is_file()


@pytest.mark.parametrize(
    "unsafe",
    ["empty", "unknown-file", "product", "boards", "metadata-symlink"],
)
def test_create_existing_mode_refuses_every_non_metadata_only_shape(
    tmp_path: Path, unsafe: str
) -> None:
    target = tmp_path / str(uuid.uuid4())
    target.mkdir()
    if unsafe != "empty":
        (target / "project.json").write_text(
            json.dumps(
                {
                    "id": target.name,
                    "name": "fixture",
                    "created_at": 1723456789000,
                    "updated_at": 1723456789000,
                }
            )
            + "\n"
        )
    if unsafe == "unknown-file":
        (target / "notes.txt").write_text("owned")
    elif unsafe == "product":
        (target / "product.json").write_text("{}")
    elif unsafe == "boards":
        (target / "boards").mkdir()
    elif unsafe == "metadata-symlink":
        (target / "project.json").unlink()
        owner = tmp_path / "owned-meta.json"
        owner.write_text(
            json.dumps(
                {
                    "id": target.name,
                    "name": "fixture",
                    "created_at": 1723456789000,
                    "updated_at": 1723456789000,
                }
            )
            + "\n"
        )
        (target / "project.json").symlink_to(owner)

    before = sorted(path.name for path in target.iterdir())
    proc = _run("create", str(target), "--initialize-existing")
    payload = _payload(proc)

    assert proc.returncode == 1
    assert payload["error"]["code"] == "PROJECT_CREATE_FAILED"
    assert sorted(path.name for path in target.iterdir()) == before
    assert not (target / "golden-blocks.lock.json").exists()


@pytest.mark.parametrize(
    "unsafe",
    [
        "inputs-symlink",
        "input-subdir",
        "input-symlink",
        "input-bad-name",
        "input-bad-extension",
        "input-empty",
        "input-too-large",
        "input-too-many",
    ],
)
def test_create_existing_mode_refuses_unsafe_viewer_inputs(
    tmp_path: Path, unsafe: str
) -> None:
    target = tmp_path / str(uuid.uuid4())
    target.mkdir()
    (target / "project.json").write_text(
        json.dumps(
            {
                "id": target.name,
                "name": "fixture",
                "created_at": 1723456789000,
                "updated_at": 1723456789000,
            }
        )
        + "\n"
    )
    inputs = target / "inputs"
    if unsafe == "inputs-symlink":
        owner = tmp_path / "owned-inputs"
        owner.mkdir()
        inputs.symlink_to(owner, target_is_directory=True)
    else:
        inputs.mkdir()
        if unsafe == "input-subdir":
            (inputs / str(uuid.uuid4())).mkdir()
        elif unsafe == "input-symlink":
            owner = tmp_path / "owned.png"
            owner.write_bytes(b"owned")
            (inputs / f"{uuid.uuid4()}.png").symlink_to(owner)
        elif unsafe == "input-bad-name":
            (inputs / "reference.png").write_bytes(b"x")
        elif unsafe == "input-bad-extension":
            (inputs / f"{uuid.uuid4()}.jpeg").write_bytes(b"x")
        elif unsafe == "input-empty":
            (inputs / f"{uuid.uuid4()}.gif").write_bytes(b"")
        elif unsafe == "input-too-large":
            with (inputs / f"{uuid.uuid4()}.jpg").open("wb") as handle:
                handle.truncate(10 * 1024 * 1024 + 1)
        elif unsafe == "input-too-many":
            for _ in range(7):
                (inputs / f"{uuid.uuid4()}.png").write_bytes(b"x")

    before = sorted(path.name for path in target.iterdir())
    proc = _run("create", str(target), "--initialize-existing")
    payload = _payload(proc)

    assert proc.returncode == 1
    assert payload["error"]["code"] == "PROJECT_CREATE_FAILED"
    assert sorted(path.name for path in target.iterdir()) == before
    assert not (target / "golden-blocks.lock.json").exists()


def test_create_existing_mode_refuses_a_symlink_target(tmp_path: Path) -> None:
    project_id = str(uuid.uuid4())
    owned = tmp_path / "owned"
    owned.mkdir()
    metadata = (
        json.dumps(
            {
                "id": project_id,
                "name": "fixture",
                "created_at": 1723456789000,
                "updated_at": 1723456789000,
            }
        )
        + "\n"
    ).encode()
    (owned / "project.json").write_bytes(metadata)
    target = tmp_path / project_id
    target.symlink_to(owned, target_is_directory=True)

    proc = _run("create", str(target), "--initialize-existing")
    payload = _payload(proc)

    assert proc.returncode == 1
    assert "must not be a symlink" in payload["error"]["message"]
    assert (owned / "project.json").read_bytes() == metadata
    assert sorted(path.name for path in owned.iterdir()) == ["project.json"]


# -- The build ---------------------------------------------------------------


def test_build_happy_path(project: Path):
    proc = _run("circuit", "boards/main.tsx", cwd=project)
    payload = _payload(proc)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert payload["ok"] is True, payload

    # Exactly one JSON line on stdout (contract §3).
    assert len((proc.stdout or "").strip().splitlines()) == 1

    assert payload["circuit_json_path"] == "boards/main.circuit.json"
    assert payload["metadata_path"] == "boards/main.board.json"
    assert payload["schematic_png"] == "boards/main_review/_schematic.png"
    assert payload["pcb_png"] == "boards/main_review/_pcb.png"
    assert payload["board"] == {"width_mm": 20.0, "height_mm": 12.0, "layers": 2}
    assert payload["bom"] == {"lines": 2, "orderable": 2, "estimated_cost_usd": 0.42}
    assert payload["fab"]["profile"] == "jlcpcb"
    assert payload["fab"]["packet_dir"] == "boards/main_fab"

    # The artifacts really landed.
    for rel in ("circuit_json_path", "metadata_path", "schematic_png", "pcb_png"):
        assert (project / payload[rel]).is_file(), rel


def test_paths_are_workspace_relative(project: Path):
    """No absolute paths, and never a ``..`` escape (donor rule: a path
    outside the workspace stays absolute rather than growing ../ chains)."""
    payload = _run_json("circuit", "boards/main.tsx", cwd=project)
    for key in ("circuit_json_path", "metadata_path", "schematic_png", "pcb_png"):
        value = payload[key]
        assert not value.startswith("/"), f"{key} is absolute: {value}"
        assert not value.startswith(".."), f"{key} escapes the workspace: {value}"


def test_paths_outside_cwd_stay_absolute(project: Path, tmp_path: Path):
    """Run from elsewhere: the result must not emit ``../..`` chains."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    payload = _run_json("circuit", str(project / "boards" / "main.tsx"), cwd=elsewhere)
    assert payload["ok"] is True, payload
    assert payload["circuit_json_path"].startswith("/")


def test_directory_input_builds(project: Path):
    """A project directory resolves to boards/main.tsx with stem 'main'."""
    payload = _run_json("circuit", ".", cwd=project)
    assert payload["ok"] is True, payload
    assert payload["circuit_json_path"] == "boards/main.circuit.json"


def test_fab_flag_overrides_profile(project: Path):
    payload = _run_json("circuit", "boards/main.tsx", "--fab", "elecrow", cwd=project)
    assert payload["fab"]["profile"] == "elecrow"


def test_out_dir_and_stem_override(project: Path, tmp_path: Path):
    out = tmp_path / "out"
    payload = _run_json(
        "circuit", "boards/main.tsx", "--out-dir", str(out), "--stem", "alt",
        cwd=project,
    )
    assert payload["ok"] is True, payload
    assert (out / "alt.circuit.json").is_file()
    assert (out / "alt.board.json").is_file()


# -- Error codes -------------------------------------------------------------


@pytest.mark.parametrize(
    ("directive", "code"),
    [
        ("STUB_SPEC_ERROR", "VALIDATION_FAILED"),
        ("STUB_COMPILE_ERROR", "COMPILE_ERROR"),
        ("STUB_TOOLCHAIN_ERROR", "TOOLCHAIN_ERROR"),
        ("STUB_EXPORT_ERROR", "EXPORT_ERROR"),
        ("STUB_PART_ERROR", "PART_ERROR"),
        ("STUB_TIMEOUT", "BUILD_TIMEOUT"),
    ],
)
def test_error_code_surfaces(tmp_path: Path, directive: str, code: str):
    root = write_project(tmp_path / directive.lower(), tsx=f"// {directive}\n" + GOOD_TSX)
    proc = _run("circuit", "boards/main.tsx", cwd=root)
    payload = _payload(proc)
    assert proc.returncode == 1
    assert payload["ok"] is False
    assert _code(payload) == code, payload
    assert payload["error"]["message"]
    assert payload["error"]["traceback"]


def test_missing_product_json_is_validation_failed(tmp_path: Path):
    root = write_project(tmp_path / "noproduct")
    (root / "product.json").unlink()
    payload = _run_json("circuit", "boards/main.tsx", cwd=root)
    assert _code(payload) == "VALIDATION_FAILED"
    assert "product.json" in payload["error"]["message"]


def test_input_not_found():
    payload = _run_json("circuit", "nope/main.tsx")
    assert _code(payload) == "VALIDATION_FAILED"
    assert "not found" in payload["error"]["message"]


def test_directory_without_board_source(tmp_path: Path):
    proc = _run("circuit", str(tmp_path))
    payload = _payload(proc)
    assert proc.returncode == 2
    assert _code(payload) == "VALIDATION_FAILED"
    assert "boards/main.tsx" in payload["error"]["message"]


def test_non_tsx_input_refused(project: Path):
    payload = _run_json("circuit", "product.json", cwd=project)
    assert _code(payload) == "VALIDATION_FAILED"
    assert "generated artifact" in payload["error"]["message"]


def test_wall_clock_kill_is_build_timeout(tmp_path: Path):
    root = write_project(tmp_path / "hang", tsx="// STUB_HANG\n" + GOOD_TSX)
    payload = _run_json(
        "circuit", "boards/main.tsx", "--wall-clock-s", "3", cwd=root, timeout=60
    )
    assert payload["ok"] is False
    assert _code(payload) == "BUILD_TIMEOUT"
    assert payload.get("timed_out") is True


def test_non_json_child_output_is_runtime_error(tmp_path: Path):
    root = write_project(tmp_path / "garbage", tsx="// STUB_GARBAGE\n" + GOOD_TSX)
    payload = _run_json("circuit", "boards/main.tsx", cwd=root)
    assert payload["ok"] is False
    assert _code(payload) == "RUNTIME_ERROR"
    assert "non-JSON" in payload["error"]["message"]
    assert "not json" in payload["stdout"]


# -- Warnings ----------------------------------------------------------------


def test_warnings_ride_through_with_severity_intact(tmp_path: Path):
    root = write_project(
        tmp_path / "warned", tsx="// STUB_WARN_ERROR STUB_WARN_INFO\n" + GOOD_TSX
    )
    payload = _run_json("circuit", "boards/main.tsx", cwd=root)
    assert payload["ok"] is True, payload
    by_kind = {w["kind"]: w for w in payload["warnings"]}
    assert by_kind["source_trace_not_connected_error"]["severity"] == "error"
    assert by_kind["source_trace_not_connected_error"]["part"] == "U3.pin7"
    assert by_kind["kicad_unavailable"]["severity"] == "info"
    assert by_kind["unverified_gerbers"]["severity"] == "warning"
    # An error-severity warning is never fab-ready (contract §1).
    assert payload["fab"]["ready"] is False


# -- scripts/check -----------------------------------------------------------


def test_check_strips_paths(project: Path):
    proc = _run("check", "boards/main.tsx", cwd=project)
    payload = _payload(proc)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert payload["ok"] is True, payload
    for key in ("circuit_json_path", "metadata_path", "schematic_png", "pcb_png", "fab"):
        assert key not in payload, f"{key} should be stripped (tempdir path)"
    # The structural facts survive.
    assert payload["board"]["width_mm"] == 20.0
    assert payload["bom"]["lines"] == 2
    # Nothing was written into the workspace.
    assert not (project / "boards" / "main.circuit.json").exists()


def test_check_drops_packet_only_warnings(project: Path):
    """``unverified_gerbers`` describes the packet check discarded — it must
    not appear on every single run."""
    payload = _run_json("check", "boards/main.tsx", cwd=project)
    kinds = {w["kind"] for w in payload.get("warnings", [])}
    assert "unverified_gerbers" not in kinds
    assert "kicad_unavailable" not in kinds


def test_check_keeps_source_warnings(tmp_path: Path):
    root = write_project(tmp_path / "checkwarn", tsx="// STUB_WARN_ERROR\n" + GOOD_TSX)
    payload = _run_json("check", "boards/main.tsx", cwd=root)
    kinds = {w["kind"] for w in payload["warnings"]}
    assert "source_trace_not_connected_error" in kinds


def test_check_surfaces_error_codes(tmp_path: Path):
    root = write_project(tmp_path / "checkfail", tsx="// STUB_COMPILE_ERROR\n" + GOOD_TSX)
    proc = _run("check", "boards/main.tsx", cwd=root)
    payload = _payload(proc)
    assert proc.returncode == 1
    assert _code(payload) == "COMPILE_ERROR"


# -- scripts/review ----------------------------------------------------------


def test_review_surfaces_warnings_and_regenerates_pngs(tmp_path: Path):
    root = write_project(tmp_path / "reviewed", tsx="// STUB_WARN_ERROR\n" + GOOD_TSX)
    gen = _run_json("circuit", "boards/main.tsx", cwd=root)
    assert gen["ok"] is True, gen

    schematic = root / "boards" / "main_review" / "_schematic.png"
    pcb = root / "boards" / "main_review" / "_pcb.png"
    schematic.unlink()
    pcb.unlink()

    payload = _run_json("review", "boards", cwd=root)
    assert payload["ok"] is True, payload
    assert payload["stem"] == "main"
    assert payload["board_json"] == "boards/main.board.json"
    assert payload["schematic_png"] == "boards/main_review/_schematic.png"
    assert payload["pcb_png"] == "boards/main_review/_pcb.png"
    assert schematic.is_file() and pcb.is_file()
    kinds = {w["kind"]: w["severity"] for w in payload["warnings"]}
    assert kinds["source_trace_not_connected_error"] == "error"


def test_review_finds_sidecar_from_project_root(project: Path):
    assert _run_json("circuit", "boards/main.tsx", cwd=project)["ok"]
    payload = _run_json("review", ".", cwd=project)
    assert payload["ok"] is True, payload
    assert payload["board_json"] == "boards/main.board.json"


def test_review_multiple_sidecars_requires_stem(tmp_path: Path):
    for stem in ("alpha", "beta"):
        (tmp_path / f"{stem}.board.json").write_text("{}", encoding="utf-8")
    proc = _run("review", str(tmp_path))
    payload = _payload(proc)
    assert proc.returncode == 2
    assert payload["ok"] is False
    msg = payload["error"]["message"]
    assert "--stem" in msg and "alpha" in msg and "beta" in msg


def test_review_stem_disambiguates(tmp_path: Path):
    for stem in ("alpha", "beta"):
        (tmp_path / f"{stem}.board.json").write_text(
            json.dumps({"generator": "circuitpy", "validation": {}}), encoding="utf-8"
        )
    payload = _run_json("review", str(tmp_path), "--stem", "beta")
    assert payload["ok"] is True, payload
    assert payload["stem"] == "beta"
    assert payload["schematic_png"] is None
    assert payload["pcb_png"] is None


def test_review_without_sidecar_fails(tmp_path: Path):
    proc = _run("review", str(tmp_path))
    payload = _payload(proc)
    assert proc.returncode == 2
    assert _code(payload) == "VALIDATION_FAILED"
    assert ".board.json" in payload["error"]["message"]


def test_review_input_not_found():
    payload = _run_json("review", "nope")
    assert _code(payload) == "VALIDATION_FAILED"
