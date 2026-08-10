"""``python scripts/cast <project_dir> [--add ID --name N --look L --voice V]``.

Manages the cast of a drama series project:

  * parses ``series.py``'s CAST list (via ``ast`` — the file is never
    executed),
  * ensures ``cast/<id>/`` exists for every character and writes
    ``cast/<id>/ref_prompts.json`` — the 4-6 reference prompts for the
    four-view sheet + expression refs (v1 generates PROMPTS, not images;
    image generation lands with real providers),
  * wires any reference images already present (``cast/<id>/ref_*.png``)
    into each Character's ``ref_images``,
  * regenerates the guarded block in ``series.py`` — ONLY the text
    between the ``# CAST-BOOK BEGIN`` / ``# CAST-BOOK END`` markers this
    skill owns; everything outside the markers is never touched.

Prints a single JSON line on stdout::

  {"ok": true, "cast": [{"id": "li_wei", "ref_prompts": 5}, ...]}
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

BEGIN_MARKER = "# CAST-BOOK BEGIN"
END_MARKER = "# CAST-BOOK END"

# Turnaround-sheet render models — mirror the cinematic provider's defaults
# (dramapy.providers.cinematic is the source of truth). Nano Banana Pro: one
# engine for the base (t2i) and every edited view, so the set is one person.
RENDER_T2I_MODEL = "fal-ai/nano-banana-pro"
RENDER_EDIT_MODEL = "fal-ai/nano-banana-pro/edit"
# Optional test seam: a zero-arg factory returning a FalClient-shaped object.
CLIENT_FACTORY = None

# The four-view sheet (research convention: 1/4 head close-up +
# front/side/back full body on white) + one expression ref = 5 slots,
# inside the 4-6 range. Asset naming follows 人名_特征 (name_feature).
REF_SLOTS: list[tuple[str, str]] = [
    ("ref_head_quarter",
     "{look}, one-quarter head close-up, neutral expression, even studio "
     "light, plain white background, character reference sheet"),
    ("ref_front",
     "{look}, full body, front view, standing, arms relaxed, plain white "
     "background, character reference sheet"),
    ("ref_side",
     "{look}, full body, side profile view, standing, plain white "
     "background, character reference sheet"),
    ("ref_back",
     "{look}, full body, back view, standing, plain white background, "
     "character reference sheet"),
    ("ref_expression_intense",
     "{look}, chest-up, intense emotional expression, eyes to camera, "
     "plain white background, character expression reference"),
]


def _err(message: str) -> int:
    print(json.dumps({"ok": False, "error": {
        "code": "VALIDATION_FAILED", "message": message}}))
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/cast",
        description=(
            "Sync a drama project's cast: reference prompts per character "
            "+ the guarded CAST block in series.py."
        ),
    )
    p.add_argument(
        "project",
        type=Path,
        help="Project directory containing series.py (or series.py itself).",
    )
    p.add_argument("--add", metavar="ID", default=None,
                   help="Add a new character with this id (requires "
                        "--name/--look/--voice).")
    p.add_argument("--name", default=None)
    p.add_argument("--look", default=None)
    p.add_argument("--voice", default=None)
    p.add_argument(
        "--render", action="store_true",
        help="Also generate the turnaround-sheet PIXELS (front t2i + edited "
             "views) via the cinematic consistency model and wire them into "
             "ref_images. Needs dramapy on the path and FAL_KEY; degrades to "
             "prompts-only with a note otherwise.",
    )
    p.add_argument("--budget", type=float, default=600.0,
                   help="Per-image render budget in seconds (with --render).")
    return p


# --- turnaround-sheet pixel rendering (opt-in) ---------------------------


def _import_refstack():
    """Best-effort import of dramapy.refstack (adds the repo's dramapy src to
    sys.path when running from the source tree). Returns the module or None."""
    try:
        import dramapy.refstack as rs  # type: ignore
        return rs
    except Exception:
        pass
    # cli.py lives at skills/cast-book/scripts/cast/cli.py -> repo root is 4 up.
    repo_src = Path(__file__).resolve().parents[4] / "packages" / "dramapy" / "src"
    if repo_src.is_dir() and str(repo_src) not in sys.path:
        sys.path.insert(0, str(repo_src))
    try:
        import dramapy.refstack as rs  # type: ignore
        return rs
    except Exception:
        return None


def _default_client():
    from dramapy.fal_client import FalClient  # type: ignore
    return FalClient()


def _parse_series_style(src: str, default: str = "photoreal-drama") -> str:
    """Pull SERIES(style=...) from series.py via ast (never exec)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return default
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "SERIES"
                and isinstance(node.value, ast.Call)):
            for kw in node.value.keywords:
                if kw.arg == "style" and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    return kw.value.value
    return default


def _render_pixels(project_dir: Path, c: dict, rs, client, style: str,
                   budget_s: float) -> int:
    """Render one character's turnaround SET to cast/<id>/ref_<slot>.jpg and
    write cast/<id>/refset.json (the manifest the cinematic provider reuses).
    Returns the number of views rendered."""
    cast_dir = project_dir / "cast" / c["id"]
    character = SimpleNamespace(
        id=c["id"], name=c["name"], look=c["look"],
        voice=c.get("voice", ""), ref_images=(),
    )
    series = SimpleNamespace(style=style)
    views = rs.render_turnaround_set(
        client, character=character, series=series,
        t2i_model=RENDER_T2I_MODEL, edit_model=RENDER_EDIT_MODEL,
        budget_s=budget_s, out_dir=cast_dir,
    )
    (cast_dir / "refset.json").write_text(
        json.dumps({"t2i_model": RENDER_T2I_MODEL,
                    "edit_model": RENDER_EDIT_MODEL, "views": views}),
        encoding="utf-8",
    )
    return len(views)


def _parse_cast(src: str) -> list[dict]:
    """Extract CAST entries from series.py source via ast (never exec).

    Returns dicts {id, name, look, voice, ref_images, lineno, end_lineno}.
    Raises ValueError on a shape this tool doesn't own.
    """
    tree = ast.parse(src)
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "CAST"):
            if not isinstance(node.value, ast.List):
                raise ValueError("CAST must be a list literal")
            entries = []
            for el in node.value.elts:
                if not (isinstance(el, ast.Call)
                        and ((isinstance(el.func, ast.Name)
                              and el.func.id == "Character")
                             or (isinstance(el.func, ast.Attribute)
                                 and el.func.attr == "Character"))):
                    raise ValueError(
                        "CAST entries must be Character(...) calls"
                    )
                entry: dict = {"ref_images": []}
                for kw in el.keywords:
                    if kw.arg in ("id", "name", "look", "voice"):
                        if not isinstance(kw.value, ast.Constant) or \
                                not isinstance(kw.value.value, str):
                            raise ValueError(
                                f"Character.{kw.arg} must be a string literal"
                            )
                        entry[kw.arg] = kw.value.value
                    elif kw.arg == "ref_images":
                        if not isinstance(kw.value, ast.List):
                            raise ValueError(
                                "Character.ref_images must be a list literal"
                            )
                        entry["ref_images"] = [
                            c.value for c in kw.value.elts
                            if isinstance(c, ast.Constant)
                        ]
                for field in ("id", "name", "look", "voice"):
                    if field not in entry:
                        raise ValueError(
                            f"Character missing required field {field!r}"
                        )
                entries.append(entry)
            return [
                {"_span": (node.lineno, node.end_lineno)},
                *entries,
            ]
    raise ValueError("series.py has no module-level CAST assignment")


def _pystr(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _render_block(cast: list[dict]) -> str:
    """The canonical text of the guarded block (between the markers)."""
    lines = ["CAST = ["]
    for c in cast:
        lines.append("    Character(")
        lines.append(f"        id={_pystr(c['id'])},")
        lines.append(f"        name={_pystr(c['name'])},")
        lines.append(f"        look={_pystr(c['look'])},")
        lines.append(f"        voice={_pystr(c['voice'])},")
        if c["ref_images"]:
            lines.append("        ref_images=[")
            for r in c["ref_images"]:
                lines.append(f"            {_pystr(r)},")
            lines.append("        ],")
        else:
            lines.append("        ref_images=[],")
        lines.append("    ),")
    lines.append("]")
    return "\n".join(lines)


def _write_ref_prompts(project_dir: Path, c: dict) -> int:
    cast_dir = project_dir / "cast" / c["id"]
    cast_dir.mkdir(parents=True, exist_ok=True)
    prompts = [
        {
            "slot": slot,
            "filename": f"{slot}.png",
            "prompt": template.format(look=c["look"]),
        }
        for slot, template in REF_SLOTS
    ]
    (cast_dir / "ref_prompts.json").write_text(json.dumps({
        "id": c["id"],
        "name": c["name"],
        "look": c["look"],
        "voice": c["voice"],
        # Research convention 人名_特征 — name the eventual image assets
        # after the character + the distinguishing feature.
        "naming": f"{c['name']}_<feature>",
        "prompts": prompts,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(prompts)


def _existing_refs(project_dir: Path, cast_id: str) -> list[str]:
    cast_dir = project_dir / "cast" / cast_id
    if not cast_dir.is_dir():
        return []
    refs = [p for p in cast_dir.glob("ref_*.png")]
    refs += [p for p in cast_dir.glob("ref_*.jpg")]
    return sorted(str(p.relative_to(project_dir)) for p in refs)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)

    project = args.project
    if project.is_file() and project.name == "series.py":
        series_py = project
        project_dir = project.parent
    else:
        project_dir = project
        series_py = project / "series.py"
    if not series_py.is_file():
        return _err(f"no series.py found at {series_py}")

    src = series_py.read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    begin_idx = end_idx = None
    for i, line in enumerate(lines):
        if line.strip() == BEGIN_MARKER and begin_idx is None:
            begin_idx = i
        elif line.strip() == END_MARKER and end_idx is None:
            end_idx = i
    if begin_idx is None or end_idx is None or end_idx <= begin_idx:
        return _err(
            f"series.py has no intact {BEGIN_MARKER!r} / {END_MARKER!r} "
            "markers — cast-book only edits the block it owns. Add the "
            "markers around the CAST assignment (see the dramacode "
            "project skeleton) and re-run."
        )

    try:
        span, *cast = _parse_cast(src)
    except (SyntaxError, ValueError) as exc:
        return _err(f"could not parse series.py CAST: {exc}")

    # Safety: the CAST assignment must live inside the guarded block —
    # otherwise regenerating the block would duplicate it.
    # 0-based marker indexes vs 1-based ast line numbers: the assignment
    # must start strictly below the BEGIN line and end at or above the
    # END line.
    lo, hi = span["_span"]
    if not (begin_idx + 1 < lo and hi <= end_idx):
        return _err(
            "the CAST assignment is not inside the CAST-BOOK markers — "
            "move it between them; cast-book never edits outside the block"
        )

    if args.add:
        missing = [f for f in ("name", "look", "voice")
                   if getattr(args, f) is None]
        if missing:
            return _err(f"--add requires --{' --'.join(missing)}")
        if any(c["id"] == args.add for c in cast):
            return _err(f"cast id {args.add!r} already exists")
        cast.append({
            "id": args.add, "name": args.name, "look": args.look,
            "voice": args.voice, "ref_images": [],
        })

    # Optional: render the turnaround-sheet pixels (front t2i + edited views).
    render_note = None
    rs = client = None
    style = "photoreal-drama"
    if args.render:
        rs = _import_refstack()
        if rs is None:
            render_note = ("dramapy not importable — install/run from the "
                           "source tree to render pixels; wrote prompts only")
        else:
            style = _parse_series_style(src)
            try:
                factory = CLIENT_FACTORY or _default_client
                client = factory()
            except Exception as exc:  # e.g. missing FAL_KEY
                client = None
                render_note = f"could not start the render client ({exc}); wrote prompts only"

    # Sync: prompts out, rendered pixels (if any), existing reference images in.
    result = []
    for c in cast:
        n = _write_ref_prompts(project_dir, c)
        rendered = 0
        if args.render and rs is not None and client is not None:
            try:
                rendered = _render_pixels(project_dir, c, rs, client, style, args.budget)
            except Exception as exc:
                render_note = render_note or f"render failed for {c['id']}: {exc}"
        c["ref_images"] = _existing_refs(project_dir, c["id"])
        entry = {"id": c["id"], "ref_prompts": n}
        if args.render:
            entry["rendered"] = rendered
        result.append(entry)

    # Guarded rewrite: only the text between the markers changes.
    new_src = "".join([
        *lines[: begin_idx + 1],
        _render_block(cast) + "\n",
        *lines[end_idx:],
    ])
    if new_src != src:
        series_py.write_text(new_src, encoding="utf-8")

    out: dict = {"ok": True, "cast": result}
    if render_note:
        out["render_note"] = render_note
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
