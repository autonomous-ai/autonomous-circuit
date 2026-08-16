"""``build_board()`` — the public §1 entry point of circuitpy.

Board TSX -> verified artifacts, per ``docs/circuit-interfaces.md`` §1:

  0. compile   — ``tscircuit-cli build`` (mirror copy of the project inside
                 ``.circuit/build/``, so ``dist/`` and the CLI's caches never
                 pollute the workspace the snapshotter watches)
  1. scan      — ``*_error`` / ``*_warning`` elements in circuit.json
  2. re-check  — @tscircuit/checks over the same JSON (independent codepath)
  3. substrate — kicad_sch/kicad_pcb conversion + kicad-cli ERC/DRC
                 (kicad absent -> one ``kicad_unavailable`` info)
  4. DFM+BOM   — the fab profile's limit table + orderability + lock drift
  5. fab       — gerbers (kicad-cli when available, else tscircuit +
                 ``unverified_gerbers``), bom.csv/cpl.csv, ORDER.md when
                 fab-ready, board.glb best-effort
  6. review    — ``_review/`` images, the ``.board.json`` sidecar, and the
                 circuit.json artifact of record moved into place LAST

Standing rules (frozen): every gate parses produced artifacts, never exit
codes; the sidecar lands BEFORE the artifact of record; checks never raise;
severity is the driver's only gate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Sequence

from circuitpy import checks
from circuitpy import circuit_normalize
from circuitpy import diffpair
from circuitpy import enclosure as enclosure_mod
from circuitpy import export_cache
from circuitpy import fab as fab_mod
from circuitpy import kicad_normalize
from circuitpy import kicad_schematic
from circuitpy import powerwidth
from circuitpy import review as review_mod
from circuitpy import router_bridge
from circuitpy import spec as spec_mod
from circuitpy import status as status_mod
from circuitpy import toolchain
from circuitpy import verify_bridge
from circuitpy.errors import (
    BuildError,
    CompileError,
    ExportError,
    ProjectShapeError,
    SpecValidationError,
    ToolchainError,
)
from circuitpy.source_hash import BoardSourceHash, board_source_hash

__all__ = [
    "build_board",
    "BuildError",
    "ProjectShapeError",
    "SpecValidationError",
    "CompileError",
    "ToolchainError",
    "ExportError",
]

GENERATOR_NAME = "circuitpy"
DEFAULT_FAB = "jlcpcb"
FAB_ENV = "CIRCUIT_FAB"
FORCE_ENV = "CIRCUIT_FORCE_REGEN"
PARTS_ENGINE_ENV = "CIRCUIT_PARTS_ENGINE"

# ---------------------------------------------------------------------------
# Wall clock is a safety valve, never a budget.
# ---------------------------------------------------------------------------
#
# This number used to be 600s, and at 600s the machine decided the board.
# Measured on hydrate-coaster at ``autorouterEffortLevel="1x"``, three runs
# each, by ``toolchain/determinism/run_repeat.py``:
#
#   2026-08-15, 24 busy processes: 102.6 / 106.1 / 104.0s quiet against
#                                  526.7 / 573.1 / 545.6s loaded — 5.2x
#   2026-08-16, 16 busy processes:  95.9 /  95.5 /  97.6s quiet against
#                                  291.9 / 273.9 / 288.7s loaded — 2.9x
#
# The multiple depends on how busy the machine is, which is the point: it is
# not a property of the board. The board ships at "10x", several times this.
# A budget a loaded machine blows through turns "did this board build" into
# "was the machine free", which is the same defect as a lucky route wearing a
# different hat.
#
# North-star's exchange rate settles what to do about it: a fab round trip is
# two weeks and ~$85, so hours of compute are free. The timeout stays only to
# stop a hung process holding a slot forever, and it is sized for the worst
# case we have measured (terminal-keyboard at "5x", ~17 minutes quiet, times
# the 5.2x load factor, times a margin) rather than for the good case.
DEFAULT_BUILD_TIMEOUT_S = 5400.0
EXPORT_TIMEOUT_S = 180.0
KICAD_TIMEOUT_S = 120.0

# ---------------------------------------------------------------------------
# Determinism: the same board source must produce the same bytes.
# ---------------------------------------------------------------------------
#
# What the router does is already reproducible, and the wall-clock diagnosis
# is wrong. Reading first: ``tscircuit-cli`` executes a single 25.9 MB bundle
# (``@tscircuit/cli/dist/cli/main.js``) that requires nothing from
# node_modules, so the standalone ``@tscircuit/capacity-autorouter`` package —
# where the ``performance.now()`` and ``Date.now()`` calls were counted — is
# never loaded at all. Inside the bundle every solver loop is
# ``for(;!this.solved&&!this.failed;)this.step()`` against MAX_ITERATIONS, and
# each clock read around it feeds ``timeToSolve`` or an iterations-per-second
# progress event. Exactly one real time budget exists in the whole bundle: a
# ``step()``-until-elapsed wrapper around PackSolver2, whose budget comes from
# ``platform.pcbPackSolverTimeoutMs`` — a name that appears once, with nothing
# assigning it, so the wrapper degenerates to a plain ``solve()``.
#
# Then measurement, which is what actually settles it. hydrate-coaster at
# "1x", six builds: three quiet at ~96s and three at ~285s with 16 busy
# processes alongside — a 2.9x wall-clock change — returned the byte-identical
# route (``pcb_trace`` + ``pcb_via`` elements, ids included) all six times.
# Slowing the machine down by a factor of three does not move one trace.
# ``test_determinism.py`` pins the reading half so a toolchain bump cannot
# quietly reintroduce a clock budget.
#
# What is *not* reproducible is everything the parts engine touches. It
# resolves supplier parts concurrently, appends one
# ``supplier_footprint_mismatch_warning`` per part **in network-completion
# order**, and stamps each with a random 10-character id. On hydrate-coaster
# that is 27 of 1812 elements, and they are the only elements in the file
# whose id is not a counter — which is why all six builds above agreed on the
# route and disagreed on the file, six times out of six. Nothing downstream
# could be cached, diffed or golden-filed against a file that never repeats.
#
# Two fixes, because neither covers the other:
#
# * ``toolchain/determinism/deterministic-run.mjs`` seeds ``Math.random`` from
#   the board's own source fingerprint, so every minted id is a function of
#   the board. This kills the randomness at its source.
# * ``_canonicalise_race_order`` below puts the racing elements in content
#   order inside the slots they already occupy. Seeding cannot do this: the id
#   *stream* is fixed but which warning gets which id still depends on who
#   answered the network first.
DETERMINISM_ENV = "CIRCUIT_DETERMINISM"
SEED_ENV = "CIRCUIT_DETERMINISTIC_SEED"
#: Stage 0c, the differential-pair route pass (`diffpair.py`). Off-switchable
#: the same way the other stages are, because a pass that cannot be turned off
#: cannot be measured against its own absence.
DIFFPAIR_ENV = "CIRCUIT_DIFFPAIR"
POWERWIDTH_ENV = "CIRCUIT_POWERWIDTH"
PRELOAD_REL = ("determinism", "deterministic-run.mjs")
#: The line the preload writes to stderr when it actually seeds. Kept in step
#: with ``DETERMINISM_MARKER`` in ``deterministic-run.mjs``; a test pins that
#: the two agree, because a marker that drifts turns the effect check into a
#: permanent "not seeded".
DETERMINISM_MARKER = "[determinism] Math.random seeded"

OUTPUT_SUFFIX = ".circuit.json"

_MIRROR_SKIP_DIRS = {
    ".circuit",
    ".claude",
    ".git",
    ".tscircuit",
    "__pycache__",
    "dist",
    "inputs",
    "node_modules",
}
_MIRROR_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".json"}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes"}


def _parts_engine_off() -> bool:
    return (os.environ.get(PARTS_ENGINE_ENV) or "").strip().lower() in {
        "off",
        "0",
        "false",
        "no",
    }


# ---------------------------------------------------------------------------
# Autorouter effort escalation.
# ---------------------------------------------------------------------------
#
# The router has an effort dial — ``<board autorouterEffortLevel>``, one of
# "1x" | "2x" | "5x" | "10x" | "100x" — and until 2026-08-11 we never set it,
# so every board ever built routed at the default. Measured on
# terminal-keyboard that day: "5x" took the same board, with no design change
# at all, from **46 errors to 18**. Build time went 4:45 to about 17 minutes.
#
# The wrong fix is a higher fixed default: it would tax every simple
# three-block board with twelve wasted minutes for a routing problem it does
# not have. The right one is a ladder — build at the normal effort, and only
# when the verdict comes back with *routing-class* errors, rebuild once at a
# higher effort before admitting defeat. The user gets one shot; the pipeline
# is allowed to try harder internally first.
#
# Two things this must not become:
#
# * **Silent.** A seventeen-minute build with no explanation is its own kind of
#   bad, so the attempt is reported in the sidecar (``build.autorouterEffort``,
#   ``build.blockingByAttempt``) and in the stdout result.
# * **Unbounded.** Exactly one escalation, one level, one timeout. If it does
#   not help, the cheaper result stands.
#
# Related measurement from the same board, recorded so nobody retries it:
# raising ``minTraceWidth``/clearance props as a routing lever made things
# *worse* (7 errors to 125). Those props gate the checker, not the router.

ROUTING_ESCALATION_ENV = "CIRCUIT_ROUTING_ESCALATION"
#: One rung. 10x/100x exist but the measured 5x gain is where the curve bends,
#: and the time cost past it is not something a chat loop can absorb.
ROUTING_ESCALATION_EFFORT = "5x"
#: Bounded, but bounded as a safety valve rather than as a budget. At 1500s
#: this was the second place the machine decided the board: terminal-keyboard
#: at "5x" is ~17 minutes (1020s) on a quiet machine, and the measured load
#: factor on this hardware is 5.2x (see DEFAULT_BUILD_TIMEOUT_S) — so on a busy
#: machine the retry was killed, the escalation reported "did not finish", and
#: the cheaper default-effort board shipped. Same source, same effort setting,
#: different board, decided by what else was running. Sized for the worst case
#: now, which costs nothing when the retry finishes early.
ROUTING_ESCALATION_TIMEOUT_S = 5400.0

#: Errors whose fix is "route it differently" — the only class a harder router
#: pass can address. A placement overlap or a missing footprint is not here:
#: escalating on those would burn twelve minutes to reproduce the same verdict.
ROUTING_ERROR_KINDS = frozenset(
    {
        "pcb_autorouting_error",
        "pcb_trace_error",
        "pcb_trace_missing_error",
        "pcb_port_not_connected_error",
        "pcb_port_not_matched_error",
        "pcb_trace_not_connected_error",
        "pcb_trace_clearance_error",
        # The router squeezing past a pad or a via it placed itself. Measured
        # 2026-08-11: these were three of terminal-keyboard's six blocking
        # errors and none of them escalated, because the set below listed the
        # trace-to-trace kind and not the pad and via ones. A harder pass is
        # exactly the remedy for all three.
        "pcb_pad_trace_clearance_error",
        "pcb_via_trace_clearance_error",
        "pcb_via_clearance_error",
        # A track threaded 0.115mm past a drill is a routing choice: the router
        # does not model holes and takes the shortest path through the gap.
        # This is the finding that blocked all three example boards.
        "dfm_hole_clearance",
        "dfm_trace_width",
        "dfm_trace_clearance",
    }
)

#: KiCad's own verdict on the same class of defect. `drc_violation` is one
#: kind carrying every DRC type in its text, so the kind alone cannot say
#: whether a harder router would help — the type tag can. Measured
#: 2026-08-11: an rp2040-core board came back with five blocking findings,
#: *all five* of them `drc_violation`, and the escalation never fired because
#: the kind was not in the set above. The second substrate was reporting a
#: routing failure and nothing was listening.
ROUTING_DRC_TYPES = frozenset(
    {
        "clearance",
        "shorting_items",
        "hole_clearance",
        "hole_near_hole",
        "track_dangling",
        "via_dangling",
        "unconnected_items",
        "solder_mask_bridge",
        "copper_edge_clearance",
        "starved_thermal",
    }
)

_DRC_TYPE_RE = re.compile(r"^\[(\w+)\]")


def _is_routing_class(warning: dict) -> bool:
    kind = warning.get("kind")
    if kind in ROUTING_ERROR_KINDS:
        return True
    detail = str(warning.get("detail") or "")
    # `pcb_placement_error` covers two unrelated things. A part hanging off
    # the board is placement and a harder route cannot help; **a via the
    # router dropped inside an SMD pad is routing** and it can. hydrate-coaster
    # shipped one of these (`C8.pin2`, 2026-08-11) and never retried, because
    # the kind alone said "placement".
    if kind == "pcb_placement_error":
        return detail.lower().startswith("via ")
    if kind != "drc_violation":
        return False
    match = _DRC_TYPE_RE.match(detail)
    return bool(match and match.group(1) in ROUTING_DRC_TYPES)

_BOARD_TAG = re.compile(r"<board\b")


def _routing_escalation_off() -> bool:
    return (os.environ.get(ROUTING_ESCALATION_ENV) or "").strip().lower() in {
        "off",
        "0",
        "false",
        "no",
    }


def _routing_blockers(warnings: Sequence[dict]) -> list[dict]:
    """The blocking warnings a harder routing pass could plausibly clear."""
    return [
        w for w in warnings
        if w.get("severity") == "error" and _is_routing_class(w)
    ]


# ---------------------------------------------------------------------------
# Determinism helpers.
# ---------------------------------------------------------------------------


def _diffpair_off() -> bool:
    return (os.environ.get(DIFFPAIR_ENV) or "").strip().lower() in {
        "off",
        "0",
        "false",
        "no",
    }


def _powerwidth_off() -> bool:
    return (os.environ.get(POWERWIDTH_ENV) or "").strip().lower() in {
        "off",
        "0",
        "false",
        "no",
    }


def _determinism_off() -> bool:
    return (os.environ.get(DETERMINISM_ENV) or "").strip().lower() in {
        "off",
        "0",
        "false",
        "no",
    }


def _preload_path() -> Path | None:
    """The seeded-``Math.random`` preload, or None when it is not installed.

    Never raises and never blocks a build: a missing preload costs byte
    stability, not a board.
    """
    try:
        candidate = toolchain.toolchain_dir().joinpath(*PRELOAD_REL)
    except RuntimeError:
        return None
    return candidate if candidate.is_file() else None


@contextmanager
def _deterministic_env(seed: str):
    """Seed the CLI subprocess, then put the environment back.

    ``toolchain.subprocess_env()`` copies ``os.environ``, so this is the only
    seam a caller has into the Node process without owning that module.
    ``NODE_OPTIONS`` is appended to rather than replaced — a user or a wrapper
    may already have set it, and stamping over that would be a silent change
    to how their Node runs.
    """
    preload = _preload_path()
    if not seed or preload is None or _determinism_off():
        yield False
        return
    previous: dict[str, str | None]
    previous = {key: os.environ.get(key) for key in ("NODE_OPTIONS", SEED_ENV)}
    existing = (previous["NODE_OPTIONS"] or "").strip()
    flag = f"--import {preload.as_uri()}"
    os.environ["NODE_OPTIONS"] = f"{existing} {flag}".strip() if existing else flag
    os.environ[SEED_ENV] = seed
    try:
        yield True
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


#: An id the toolchain minted with ``Math.random``: ten characters from
#: ``[A-Za-z0-9]``, appended to the element's own type. circuit-json's
#: ``getZodPrefixedIdWithDefault`` is the only thing in the bundle that makes
#: ids this shape; counter ids (``pcb_via_0``, ``source_net_16_0``) do not
#: match, so canonicalisation cannot touch an element the toolchain numbered
#: itself.
_MINTED_ID_RE = re.compile(r"^[a-z0-9_]+_[A-Za-z0-9]{10}$")


def _canonicalise_race_order(elements: list) -> dict[str, int]:
    """Put concurrently-produced elements in content order, in place.

    The parts engine resolves suppliers concurrently and appends one warning
    per part as each answer lands, so the *set* of elements is stable and the
    *sequence* is not. Measured on a four-part fixture, 2026-08-15: two builds
    produced the same four ``supplier_footprint_mismatch_warning`` elements in
    two different orders, and that alone made the file bytes differ.

    The rewrite is deliberately the smallest one that fixes it:

    * only element types every one of whose members carries a minted random
      id are eligible — a counter id means the toolchain already ordered them
      and we must not second-guess it;
    * an eligible group is skipped entirely if any of its ids appears anywhere
      else in the document, because then something references it and reseating
      the id would break that reference;
    * members are sorted by their content with their own id removed, and
      written back into **the slots they already occupied**, so no element
      changes position relative to any element of another type. The ids of the
      group are sorted and re-paired to the sorted members, which invents no
      new id and cannot collide.

    Byte stability needs the seeded ``Math.random`` as well: this function
    makes the *order* canonical, the seed makes the *id strings* reproducible.
    Returns ``{type: count}`` for what it reordered — empty when nothing did.
    """
    by_type: dict[str, list[int]] = {}
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        kind = element.get("type")
        if isinstance(kind, str):
            by_type.setdefault(kind, []).append(index)

    # How many times each string appears anywhere in the document. An id that
    # appears exactly once is only its own definition; anything higher means
    # something points at it and the group is off limits. Counted once for the
    # whole file rather than once per type.
    seen: dict[str, int] = {}

    def _count(value: object) -> None:
        if isinstance(value, dict):
            for sub in value.values():
                _count(sub)
        elif isinstance(value, list):
            for sub in value:
                _count(sub)
        elif isinstance(value, str):
            seen[value] = seen.get(value, 0) + 1

    _count(elements)

    reordered: dict[str, int] = {}
    for kind, slots in by_type.items():
        if len(slots) < 2:
            continue
        id_key = f"{kind}_id"
        ids: list[str] = []
        for slot in slots:
            value = elements[slot].get(id_key)
            if not isinstance(value, str) or not _MINTED_ID_RE.match(value):
                ids = []
                break
            ids.append(value)
        if not ids or any(seen.get(i, 0) != 1 for i in ids):
            continue

        members = [dict(elements[slot]) for slot in slots]
        for member in members:
            member.pop(id_key, None)
        keys = [
            json.dumps(member, sort_keys=True, separators=(",", ":"))
            for member in members
        ]
        order = sorted(range(len(members)), key=keys.__getitem__)
        changed = False
        for position, (slot, minted) in enumerate(zip(slots, sorted(ids))):
            member = members[order[position]]
            member[id_key] = minted
            if member != elements[slot]:
                changed = True
            elements[slot] = member
        if changed:
            reordered[kind] = len(slots)
    return reordered


def _split_top_level_blocks(text: str) -> list[tuple[int, int]] | None:
    """``(start, end)`` of each top-level element in a circuit.json array.

    A depth scan that understands strings and escapes — not a JSON parser, and
    not a regex. It exists so canonicalisation can move an element without
    re-serialising the file: ``JSON.stringify`` and ``json.dumps`` disagree on
    float exponents (``1e-7`` against ``1e-07``, ``0.00001`` against
    ``1e-05``), so a Python round-trip would rewrite numbers on every board it
    touched. Returns None on anything unexpected, and the caller then leaves
    the file exactly as the toolchain wrote it.
    """
    depth = 0
    in_string = False
    escaped = False
    start: int | None = None
    blocks: list[tuple[int, int]] = []
    for index, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth == 2 and start is None:
                start = index
        elif ch in "]}":
            depth -= 1
            if depth == 1 and start is not None:
                blocks.append((start, index + 1))
                start = None
            elif depth == 0:
                return blocks if not in_string and start is None else None
    return None


def _canonicalise_file(path: Path) -> dict[str, int]:
    """Rewrite ``path`` so concurrently-produced elements land in a fixed order.

    Byte-conservative by construction: every element that does not move keeps
    the exact bytes the toolchain wrote, and an element that does move keeps
    its own bytes apart from its id token. Any surprise — a block count that
    disagrees with the element count, a block that does not re-parse to the
    element it should be, an id token that is not unique inside its block —
    abandons the rewrite and leaves the file untouched. Returns ``{type:
    count}`` describing what moved.
    """
    try:
        text = path.read_text(encoding="utf-8")
        elements = json.loads(text)
    except (OSError, ValueError):
        return {}
    if not isinstance(elements, list):
        return {}

    before = [
        json.dumps(e, sort_keys=True, separators=(",", ":")) if isinstance(e, dict) else None
        for e in elements
    ]
    canonical = [dict(e) if isinstance(e, dict) else e for e in elements]
    reordered = _canonicalise_race_order(canonical)
    if not reordered:
        return {}

    blocks = _split_top_level_blocks(text)
    if blocks is None or len(blocks) != len(elements):
        return {}

    # Where did each slot's new content come from? Content is unchanged apart
    # from the id, so match on the id-free body.
    def _body(element: object) -> str | None:
        if not isinstance(element, dict):
            return None
        kind = element.get("type")
        stripped = {k: v for k, v in element.items() if k != f"{kind}_id"}
        return json.dumps(stripped, sort_keys=True, separators=(",", ":"))

    source_of: dict[int, int] = {}
    for slot, (old, new) in enumerate(zip(elements, canonical)):
        if before[slot] == json.dumps(new, sort_keys=True, separators=(",", ":")):
            continue
        wanted = _body(new)
        origin = next(
            (i for i, e in enumerate(elements) if _body(e) == wanted and i not in source_of.values()),
            None,
        )
        if origin is None:
            return {}
        source_of[slot] = origin

    pieces = [text[s:e] for s, e in blocks]
    rewritten = list(pieces)
    for slot, origin in source_of.items():
        kind = canonical[slot].get("type")
        id_key = f"{kind}_id"
        old_id = elements[origin].get(id_key)
        new_id = canonical[slot].get(id_key)
        piece = pieces[origin]
        if not isinstance(old_id, str) or not isinstance(new_id, str):
            return {}
        if piece.count(f'"{old_id}"') != 1:
            return {}
        rewritten[slot] = piece.replace(f'"{old_id}"', f'"{new_id}"')

    out = list(text)
    for (start, end), piece in zip(reversed(blocks), reversed(rewritten)):
        out[start:end] = piece
    new_text = "".join(out)

    # The rewrite must be a permutation, never an edit: same elements, same
    # count, only the arrangement changed.
    try:
        check = json.loads(new_text)
    except ValueError:
        return {}
    if sorted(
        json.dumps(e, sort_keys=True, separators=(",", ":")) for e in check
    ) != sorted(
        json.dumps(e, sort_keys=True, separators=(",", ":")) for e in canonical
    ):
        return {}
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError:
        return {}
    return reordered


#: 1980-01-01 00:00:00 — the earliest timestamp the zip format can hold, and
#: the value every reproducible-build toolchain settled on for the same reason.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _zip_deterministic(dest: Path, members: Sequence[tuple[Path, str]]) -> None:
    """Write a zip whose bytes depend only on its contents.

    ``ZipFile.write`` stamps each entry with the source file's mtime and
    permission bits, so two packets built from identical geometry an hour apart
    are different files. That is the same defect as a lucky route one layer
    out: it defeats caching, it defeats a golden-file test on the packet, and
    it makes "did this change?" unanswerable for the artifact a customer
    actually receives.

    Entries are written in name order with a fixed timestamp and fixed mode.
    """
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as bundle:
        for source, arcname in sorted(members, key=lambda m: m[1]):
            info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, source.read_bytes())


_EFFORT_RE = re.compile(r'autorouterEffortLevel\s*=\s*"([^"]+)"')


def _declared_effort(board_source: Path) -> str | None:
    """The effort the board's own source asks for, if it asks."""
    try:
        match = _EFFORT_RE.search(board_source.read_text(encoding="utf-8"))
    except OSError:
        return None
    return match.group(1) if match else None


def _set_autorouter_effort(board_source: Path, effort: str) -> bool:
    """Add ``autorouterEffortLevel`` to the mirrored board source.

    Only ever touches the copy inside ``.circuit/build/`` — the user's file is
    never rewritten, so "generated files are never hand-edited, nothing is
    overwritten" still holds. Returns False when the author already set the
    prop (their choice wins) or when the file has no ``<board>`` tag.
    """
    try:
        text = board_source.read_text(encoding="utf-8")
    except OSError:
        return False
    if "autorouterEffortLevel" in text or "routingDisabled" in text:
        return False
    patched, count = _BOARD_TAG.subn(
        f'<board autorouterEffortLevel="{effort}"', text, count=1
    )
    if not count:
        return False
    try:
        board_source.write_text(patched, encoding="utf-8")
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Project discovery.
# ---------------------------------------------------------------------------


def _resolve_source(source_path: Path) -> Path:
    if source_path.is_dir():
        script_path = source_path / "boards" / "main.tsx"
        if not script_path.is_file():
            raise ProjectShapeError(
                f"directory input must contain boards/main.tsx: {source_path}"
            )
        return script_path
    if not source_path.is_file():
        raise ProjectShapeError(f"board source not found: {source_path}")
    if source_path.suffix != ".tsx":
        raise ProjectShapeError(
            f"board source must be a .tsx file or a project directory "
            f"(got {source_path.name})"
        )
    return source_path


def _find_project_root(script_path: Path) -> Path:
    """The nearest ancestor directory containing ``product.json``."""
    for candidate in (script_path.parent, *script_path.parent.parents):
        if (candidate / "product.json").is_file():
            return candidate
    raise ProjectShapeError(
        f"no product.json found in any parent of {script_path} — a circuit "
        "project root must contain the product definition"
    )


# ---------------------------------------------------------------------------
# Canonical sidecar JSON.
# ---------------------------------------------------------------------------


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _current_toolchain_block() -> dict[str, str]:
    """Sidecar ``toolchain`` block: kicadCli omitted when absent."""
    return {
        key: value for key, value in toolchain.versions().items() if value is not None
    }


# ---------------------------------------------------------------------------
# Mirror-copy build workspace (.circuit/build/<stem>-<pid>/).
# ---------------------------------------------------------------------------


def _mirror_project(project_root: Path, work: Path) -> None:
    """Copy the project's source surface (TSX/TS/JSON incl. blocks/, minus
    built artifacts) into the work dir so the CLI's ``dist/`` and caches stay
    inside ``.circuit/`` — the workspace the snapshotter watches never grows
    build litter."""
    for root, dirs, files in os.walk(project_root):
        root_path = Path(root)
        dirs[:] = [
            d
            for d in dirs
            if d not in _MIRROR_SKIP_DIRS
            and not d.endswith("_review")
            and not d.endswith("_fab")
        ]
        for name in files:
            source = root_path / name
            if source.suffix not in _MIRROR_SUFFIXES:
                continue
            if name.endswith(OUTPUT_SUFFIX) or name.endswith(".board.json"):
                continue
            rel = source.relative_to(project_root)
            target = work / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _cli_export(
    work: Path, circuit_json_path: Path, fmt: str, out_name: str, seed: str = ""
) -> Path:
    """``tscircuit-cli export`` writes ``-o`` **next to the input file** (a
    verified CLI behavior — absolute paths get mangled), so we pass a bare
    filename and collect the artifact from the input's directory.

    Seeded with the same board fingerprint the compile uses, so that every
    Node process in a build draws from the same stream rather than some of
    them being seeded and some not.

    **This does not make the KiCad export reproducible, and it was measured
    rather than assumed.** Two exports of one byte-identical circuit.json
    differ in exactly 1364 uuid tokens in the ``.kicad_pcb`` (663 in the
    ``.kicad_sch``) and in nothing else — renaming each uuid to its
    first-appearance index makes the two files byte-identical, and seeding
    ``Math.random`` changes neither hash, so those uuids come from
    ``crypto``, not from ``Math.random``. Closing that is a separate job
    (ledger #35's open half) because the packet's other zip, the gerbers,
    carries a ``%TF.CreationDate`` per file and needs the same treatment in
    an exporter this module does not own.
    """
    with _deterministic_env(seed):
        result = toolchain.run_cli(
            ["export", str(circuit_json_path), "-f", fmt, "-o", out_name],
            cwd=work,
            timeout=EXPORT_TIMEOUT_S,
            check=False,
        )
    produced = circuit_json_path.parent / out_name
    if not produced.is_file():
        tail = result.output.strip()[-800:]
        raise RuntimeError(
            f"export -f {fmt} produced no {out_name} (exit {result.returncode}): {tail}"
        )
    return produced


# ---------------------------------------------------------------------------
# The public entry point.
# ---------------------------------------------------------------------------


def build_board(
    source_path: Path | str,
    output_path: Path | str,
    *,
    fab: str | None = None,
    max_build_s: float | None = None,
) -> dict[str, object]:
    """Build one board per contract §1 and return the snake_case dict
    mirroring the CircuitcodeResult success fields (§3)."""
    source_p = Path(source_path).expanduser().resolve()
    output_p = Path(output_path).expanduser().resolve()
    if not output_p.name.endswith(OUTPUT_SUFFIX):
        raise ProjectShapeError(
            f"output_path must end in {OUTPUT_SUFFIX} (got {output_p.name})"
        )
    stem = output_p.name[: -len(OUTPUT_SUFFIX)]

    script_path = _resolve_source(source_p)
    project_root = _find_project_root(script_path)
    try:
        rel_entry = script_path.resolve().relative_to(project_root)
    except ValueError as exc:
        raise ProjectShapeError(
            f"board source {script_path} is outside its project root {project_root}"
        ) from exc

    product = spec_mod.load_product(project_root)
    parts = spec_mod.load_parts(project_root)
    fab_id = (
        fab
        if fab is not None
        else (os.environ.get(FAB_ENV, "").strip() or DEFAULT_FAB)
    )
    profile = fab_mod.get_profile(fab_id)

    identity = board_source_hash(script_path, project_root)

    # Safety envelope — refused at spec time, before any toolchain process.
    source_files = [
        project_root / f.path if not f.path.startswith("/") else Path(f.path)
        for f in identity.files
    ]
    spec_mod.preflight_safety(source_files, project_root, product)

    boards_dir = output_p.parent
    review_dir = boards_dir / f"{stem}_review"
    fab_dir = boards_dir / f"{stem}_fab"
    sidecar_path = boards_dir / f"{stem}.board.json"

    if not _truthy(os.environ.get(FORCE_ENV)):
        prior = _unchanged_prior_result(
            sidecar_path=sidecar_path,
            identity=identity,
            output_p=output_p,
            boards_dir=boards_dir,
            fab_dir=fab_dir,
        )
        if prior is not None:
            return prior

    progress = status_mod.BuildStatus(project_root, stem=stem)
    progress.stage("compile")

    # -- Stage 0: compile in the mirror work dir. ----------------------------
    work = project_root / ".circuit" / "build" / f"{stem}-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    _mirror_project(project_root, work)
    # Anchor the CLI's output resolution. `tscircuit-cli` places `dist/` beside
    # the nearest ancestor package.json; with none in the work dir it can pick
    # a directory far outside the project (observed: /Users/d/code), leaving us
    # to report a COMPILE_ERROR for a board that built perfectly. A private
    # stub stops the search here. Board projects intentionally carry no
    # node_modules — the toolchain is repo-level — so this file exists purely
    # as a boundary marker.
    anchor = work / "package.json"
    if not anchor.exists():
        anchor.write_text(
            json.dumps(
                {"name": "circuit-build-workspace", "private": True, "version": "0.0.0"},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    build_args = ["build", rel_entry.as_posix(), "--schematic-png", "--pcb-png"]
    if _parts_engine_off():
        build_args.append("--disable-parts-engine")
    built_dir = work / "dist" / rel_entry.parent / rel_entry.stem
    built_circuit_json = built_dir / "circuit.json"

    #: Filled in by the compile: whether the seeded RNG was in force, and what
    #: canonicalisation had to move. Reported in the sidecar so a byte-unstable
    #: build is visible rather than mysterious.
    determinism_block: dict[str, object] = {"seeded": False, "canonicalised": {}}

    #: One entry per compile attempt, so the attempt the build *keeps* is the
    #: one whose repair gets reported. Escalation can throw an attempt away.
    circuit_normalizations: list[circuit_normalize.CircuitNormalization] = []
    diffpair_results: list[diffpair.DiffPairResult] = []
    powerwidth_results: list[powerwidth.WidenResult] = []
    #: Stage 0b, one entry per compile attempt. Empty engine="off" rows are
    #: dropped from the sidecar; a stage that did nothing should not be noise.
    router_reports: list[dict] = []

    def _compile_once(timeout_s: float) -> list:
        with _deterministic_env(identity.source_fingerprint) as requested:
            try:
                build_result = toolchain.run_cli(
                    build_args, cwd=work, timeout=timeout_s, check=False
                )
            except TimeoutError as exc:
                raise ToolchainError(str(exc)) from exc
            except RuntimeError as exc:
                raise ToolchainError(str(exc)) from exc
        # Observed, never assumed. `NODE_OPTIONS` is a Node mechanism and
        # `node_modules/.bin/tscircuit-cli` runs under bun instead whenever bun
        # is on PATH — in which case the preload is ignored, silently, and the
        # ids go back to being random while we still claim to seed them. The
        # preload announces itself on stderr (captured here, `stderr=STDOUT`),
        # so this flag reports what reached the process that did the work.
        determinism_block["seeded"] = bool(
            requested and DETERMINISM_MARKER in build_result.output
        )
        if requested and not determinism_block["seeded"]:
            determinism_block["seed_not_applied"] = (
                "the preload did not run in the CLI process — NODE_OPTIONS is "
                "ignored when tscircuit-cli picks bun as its runner"
            )

        if not built_circuit_json.is_file():
            tail = build_result.output.strip()[-800:]
            # A build that logged success but left nothing here means the CLI
            # resolved `dist/` somewhere else — it walks up to the nearest
            # package.json, so without an anchor it can escape the work dir
            # entirely and we would report a compile failure for a board that
            # compiled fine. Say which of the two actually happened.
            if "✓" in build_result.output or "Done" in build_result.output:
                raise CompileError(
                    f"tscircuit reported success for {rel_entry.as_posix()} but "
                    f"wrote no circuit.json under {built_dir} — the CLI resolved "
                    f"its output directory outside the work dir (it walks up to "
                    f"the nearest package.json). Output tail: {tail or 'none'}"
                )
            raise CompileError(
                f"tscircuit eval failed for {rel_entry.as_posix()} "
                f"(exit {build_result.returncode}): {tail or 'no output'}"
            )
        # Before anything reads it. Every later stage — the checks, the KiCad
        # conversion, the export cache key, the artifact of record itself —
        # takes this file, so the canonical order has to exist here or not at
        # all.
        moved = _canonicalise_file(built_circuit_json)
        for kind, count in moved.items():
            existing = determinism_block["canonicalised"]
            assert isinstance(existing, dict)
            existing[kind] = count
        # Stage 0a: open any polygon pad whose ring is closed. A repeated
        # closing vertex is a zero-length edge, and the upstream via-in-pad
        # check reports "inside" for *every* point in the plane against a
        # zero-length edge — so one redundant vertex on a USB-C pad blocks the
        # board over a via 0.3mm outside it. No closed ring, no rewrite.
        circuit_normalizations.append(
            circuit_normalize.normalize_circuit_json(built_circuit_json)
        )
        # Stage 0b: hand the whole board to our own router, if asked. Off by
        # default — `routerlib` knows rules the shipped autorouter cannot
        # represent (drill clearance, a plane as a net, net classes) and does
        # not yet match it on completeness, so it is measured before it is
        # switched on. `CIRCUIT_ROUTER=portfolio` keeps our copper only when it
        # connects at least as many nets; `portfolio-force` always keeps it.
        # Before stage 0c, so the pair pass can still improve what we laid
        # down, and so the two stages compose in the same order either way.
        router_reports.append(
            router_bridge.route_board(built_circuit_json, profile)
        )
        # Stage 0c: route every two-terminal differential pair as a pair.
        # The autorouter solves D+ and D- as two unrelated nets, which is what
        # the first human EE review called out (2026-08-15, finding 3). This
        # replaces that copper with one coupled pair, in the space the
        # autorouter's own detour frees, and refuses rather than regress —
        # see `diffpair.py` for why it cannot run *before* the autorouter.
        if not _diffpair_off():
            def _grade(elements: list, at: Path) -> list[dict]:
                """The same gate the board is judged by, handed to the pair
                pass so it is graded by our ruler and not by its own model."""
                found: list[dict] = []
                found.extend(checks.harvest_circuit_json(elements))
                found.extend(checks.run_tscircuit_checks(at))
                found.extend(checks.dfm_warnings(elements, product, profile))
                return found

            diffpair_results.append(
                diffpair.route_diff_pairs(
                    built_circuit_json, profile, grade=_grade
                )
            )
        # Stage 0d: give the power and ground rails the copper their class
        # deserves. EE review 2026-08-15, finding 4 — the autorouter has one
        # width for the whole board, so a rail carrying everything gets the
        # same 0.2mm as a button. This never moves a trace; it only takes the
        # gap the router already left, and it is graded by the same gate as
        # the pair pass. After 0c on purpose: the pair's geometry is a
        # clearance rule the widening has to respect, not the other way round.
        if not _powerwidth_off():
            def _grade_power(elements: list, at: Path) -> list[dict]:
                found: list[dict] = []
                found.extend(checks.harvest_circuit_json(elements))
                found.extend(checks.run_tscircuit_checks(at))
                found.extend(checks.dfm_warnings(elements, product, profile))
                return found

            powerwidth_results.append(
                powerwidth.widen_power_traces(
                    built_circuit_json, profile, grade=_grade_power
                )
            )
        try:
            elements = json.loads(built_circuit_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CompileError(f"built circuit.json unreadable: {exc}") from exc
        if not isinstance(elements, list):
            raise CompileError(
                f"built circuit.json is not an element array "
                f"(got {type(elements).__name__})"
            )
        return elements

    def _scan(elements: list) -> list[dict]:
        """Stages 1, 2 and 4a — everything judgeable straight off the geometry,
        and therefore everything the escalation decision can be based on."""
        found: list[dict] = []
        found.extend(checks.harvest_circuit_json(elements))
        found.extend(checks.schematic_truth_warnings(elements))
        found.extend(checks.run_tscircuit_checks(built_circuit_json))
        found.extend(checks.iou_warnings(elements, profile))
        found.extend(checks.dfm_warnings(elements, product, profile))
        return found

    first_timeout = (
        max_build_s if max_build_s is not None else DEFAULT_BUILD_TIMEOUT_S
    )
    circuit_json = _compile_once(first_timeout)

    progress.stage("scan")
    warnings: list[dict] = _scan(circuit_json)

    # -- Stage 0b: escalate the router once, if routing is what is wrong. ----
    # See the ROUTING_ESCALATION notes at the top of this module. One rung, one
    # rebuild, and the cheaper result stands unless the harder one is strictly
    # better — escalation may never make a board worse.
    # What the *author* asked for, if anything. Reporting "default" for a
    # board whose source says `autorouterEffortLevel="5x"` is a sidecar that
    # disagrees with the board it describes, and it is how the same effort
    # question got re-litigated twice.
    routing_effort = _declared_effort(work / rel_entry) or "default"
    blocking_by_attempt = [
        sum(1 for w in warnings if w.get("severity") == "error")
    ]
    escalation_note: dict | None = None
    kept_attempt = 0
    if _routing_blockers(warnings) and not _routing_escalation_off():
        entry_copy = work / rel_entry
        if _set_autorouter_effort(entry_copy, ROUTING_ESCALATION_EFFORT):
            # Keep attempt 1's whole output directory — circuit.json *and* the
            # review PNGs. Every downstream stage reads from built_dir, so
            # "keep the better attempt" has to mean the files too, not just
            # the parsed elements. Copying beats rebuilding: a third compile
            # to undo a retry would cost more than the retry did.
            kept = built_dir.with_name(built_dir.name + "__attempt1")
            shutil.rmtree(kept, ignore_errors=True)
            shutil.copytree(built_dir, kept)

            progress.stage("compile")
            budget = max(
                ROUTING_ESCALATION_TIMEOUT_S,
                max_build_s if max_build_s is not None else 0.0,
            )
            retry_json: list | None = None
            retry_warnings: list[dict] = []
            try:
                retry_json = _compile_once(budget)
                progress.stage("scan")
                retry_warnings = _scan(retry_json)
            except (ToolchainError, CompileError) as exc:
                # Escalation is best-effort by construction: attempt 1 is
                # already a real answer, so a failed retry is information,
                # never a build failure.
                escalation_note = checks.check_failed(
                    f"the {ROUTING_ESCALATION_EFFORT} routing retry did not "
                    f"finish ({exc}); reporting the default-effort build"
                )

            keep_retry = False
            if retry_json is not None:
                retry_blocking = sum(
                    1 for w in retry_warnings if w.get("severity") == "error"
                )
                blocking_by_attempt.append(retry_blocking)
                keep_retry = retry_blocking < blocking_by_attempt[0]

            if keep_retry:
                circuit_json = retry_json  # type: ignore[assignment]
                warnings = retry_warnings
                routing_effort = ROUTING_ESCALATION_EFFORT
                kept_attempt = len(circuit_normalizations) - 1
                shutil.rmtree(kept, ignore_errors=True)
            else:
                # Escalation may never make a board worse: put attempt 1's
                # artifacts back and report its verdict.
                shutil.rmtree(built_dir, ignore_errors=True)
                shutil.move(str(kept), str(built_dir))

    progress.stage("dfm")
    if escalation_note is not None:
        warnings.append(escalation_note)

    # What stage 0a had to repair on the attempt this build actually keeps.
    if circuit_normalizations:
        normalized = circuit_normalizations[min(kept_attempt,
                                                len(circuit_normalizations) - 1)]
        if normalized.changed:
            warnings.append(
                {
                    "part": "board",
                    "kind": "circuit_normalized",
                    "detail": (
                        "the compiled board was repaired before it was judged: "
                        f"{normalized.summary()}"
                    ),
                    "severity": "info",
                }
            )
        for note in normalized.notes:
            warnings.append(checks.check_failed(note))

    # What our own router did to the attempt this build keeps. A stage that
    # ran and declined has to say so: "we routed this board ourselves" and "we
    # tried and kept the autorouter's answer" are different boards.
    kept_router = (
        router_reports[min(kept_attempt, len(router_reports) - 1)]
        if router_reports
        else {"engine": "off"}
    )
    if kept_router.get("engine") != "off":
        after = kept_router.get("after") or {}
        before = kept_router.get("before") or {}
        warnings.append({
            "part": "board",
            "kind": "router_applied" if kept_router.get("applied")
            else "router_declined",
            "detail": (
                f"{kept_router.get('engine')} "
                f"{'routed' if kept_router.get('applied') else 'did not route'} "
                f"this board: {after.get('connectedNets')}/"
                f"{after.get('routableNets')} nets against the autorouter's "
                f"{before.get('connectedNets')}"
                + (f" — {kept_router['reason']}" if kept_router.get("reason") else "")
            ),
            "severity": "info",
        })

    # What the pair pass did to the attempt this build keeps, in the same
    # place and the same shape. A pair it refused is reported too: silence
    # would read as "there was nothing to do".
    if diffpair_results:
        paired = diffpair_results[min(kept_attempt, len(diffpair_results) - 1)]
        if paired.changed:
            warnings.append({
                "part": "board",
                "kind": "diffpair_routed",
                "detail": paired.summary(),
                "severity": "info",
            })
        for pair in paired.pairs:
            if pair.status == "refused":
                warnings.append({
                    "part": f"{pair.net_p},{pair.net_n}",
                    "kind": "diffpair_not_routed",
                    "detail": (
                        "this pair kept the autorouter's copper: "
                        f"{pair.reason}"
                    ),
                    "severity": "info",
                })
        for note in paired.notes:
            warnings.append(checks.check_failed(note))

    # What the widening pass did to the attempt this build keeps. Reported on
    # success as well as refusal: "GND is 0.5mm for 26% of its run" is the
    # number a reviewer needs, and `dfm_power_trace_width` cannot carry it —
    # that check reports the narrowest point, which is a different fact.
    if powerwidth_results:
        widened = powerwidth_results[
            min(kept_attempt, len(powerwidth_results) - 1)
        ]
        warnings.extend(widened.findings())

    build_block: dict[str, object] = {
        "autorouterEffort": routing_effort,
        "attempts": len(blocking_by_attempt),
        "blockingByAttempt": blocking_by_attempt,
        # Whether this board's bytes are reproducible, and what it took.
        # `seeded: false` means the preload was missing or switched off, and
        # the ids in this artifact are random — the file will not compare
        # equal to another build of the same source.
        "determinism": dict(determinism_block),
    }
    if kept_router.get("engine") != "off":
        build_block["router"] = kept_router
    if diffpair_results:
        build_block["diffPair"] = diffpair_results[
            min(kept_attempt, len(diffpair_results) - 1)
        ].as_dict()
    if powerwidth_results:
        build_block["powerWidth"] = powerwidth_results[
            min(kept_attempt, len(powerwidth_results) - 1)
        ].as_dict()

    tool_versions = toolchain.versions()
    circuit_json_sha = export_cache.sha256_file(built_circuit_json)

    def _cached(fmt: str, out_name: str, suffix: str) -> Path:
        key = export_cache.export_key(
            circuit_json_sha=circuit_json_sha,
            kind=fmt,
            versions=tool_versions,
            fab=profile.id,
        )
        hit = export_cache.lookup(project_root, key, suffix)
        if hit is not None:
            target = built_circuit_json.parent / out_name
            shutil.copy2(hit, target)
            return target
        produced = _cli_export(
            work, built_circuit_json, fmt, out_name, identity.source_fingerprint
        )
        export_cache.store(project_root, key, suffix, produced)
        return produced

    # -- Exporter packet (BOM/CPL source; gerber fallback). ------------------
    ts_gerbers_zip: Path | None = None
    bom_rows: list[dict] = []
    cpl_text = ""
    try:
        ts_gerbers_zip = _cached("gerbers", "ts-gerbers.zip", ".gerbers.zip")
        with zipfile.ZipFile(ts_gerbers_zip) as packet:
            names = set(packet.namelist())
            if "bom.csv" in names:
                bom_rows = fab_mod.parse_exporter_bom(
                    packet.read("bom.csv").decode("utf-8", "replace")
                )
            if "pick_and_place.csv" in names:
                cpl_text = packet.read("pick_and_place.csv").decode("utf-8", "replace")
    except (RuntimeError, TimeoutError, OSError, zipfile.BadZipFile) as exc:
        has_errors = any(w.get("severity") == "error" for w in warnings)
        if not has_errors:
            raise ExportError(f"gerber/BOM export failed: {exc}") from exc
        warnings.append(
            checks.check_failed(f"fab packet export skipped (board has errors): {exc}")
        )
    bom_rows = fab_mod.merge_parts_lock(bom_rows, parts)

    # -- Stage 4b: BOM gate. -------------------------------------------------
    if bom_rows:
        warnings.extend(checks.bom_gate(bom_rows, assembly=product.assembly))
    elif product.assembly:
        warnings.append(
            checks.check_failed("assembly requested but no BOM rows were produced")
        )

    # -- Stage 4c: the standalone checks (packages/verify). ------------------
    # Assembly/DFA, net-class current capacity, the DC operating point, the
    # electrical design review and thermal dissipation. All five read the
    # compiled circuit.json, share no code with anything above them, and cost
    # about a second between them on a 130-part board. The fab profile decides
    # which of their findings block `fab.ready` — see fab.apply_verify_policy.
    warnings.extend(
        verify_bridge.check_circuit_json(
            built_circuit_json,
            profile=profile,
            assembly_order=product.assembly,
        )
    )

    progress.stage("substrate")

    # -- Stage 3 + 5: second substrate + shipping gerbers. -------------------
    gerber_source = "tscircuit"
    kicad_gerbers_zip: Path | None = None
    if toolchain.kicad_cli_exe() is not None:
        kicad_sch: Path | None = None
        kicad_pcb: Path | None = None
        try:
            kicad_sch = _cached("kicad_sch", "board.kicad_sch", ".kicad_sch")
            kicad_pcb = _cached("kicad_pcb", "board.kicad_pcb", ".kicad_pcb")
        except (RuntimeError, TimeoutError) as exc:
            warnings.append(checks.check_failed(f"kicad conversion failed: {exc}"))
        if kicad_pcb is not None:
            # Stage 3a: hold the converted board to the fab's floors before
            # anything reads it. `circuit-json-to-kicad` emits silkscreen text
            # at 0.2-0.67mm against JLCPCB's 1.0mm minimum and leaves the
            # stroke implicit, so KiCad plots it at 0.033mm — on every board it
            # has ever produced. This is the one place every board passes
            # through between the converter we do not own and the plotter, so
            # one change here fixes the whole catalogue. Idempotent.
            normalization = kicad_normalize.normalize_for_fab(kicad_pcb, profile)
            if normalization.changed:
                warnings.append(
                    {
                        "part": "board",
                        "kind": "kicad_normalized",
                        "detail": (
                            "the converted board was held to the fab's "
                            f"silkscreen floors before plotting: "
                            f"{normalization.summary()}"
                        ),
                        "severity": "info",
                    }
                )
            for note in normalization.notes:
                warnings.append(checks.check_failed(note))
        if kicad_sch is not None:
            # Stage 3b: hold the converted *schematic* to the design's own
            # netlist before anyone reads it, the same posture as 3a for the
            # board. `circuit-json-to-kicad` draws no symbol at all for a part
            # whose `symbol_name` is null — every USB-C receptacle we ship —
            # so KiCad puts all 16 pins on the origin and reads VBUS, GND, D+
            # and D- as one net; and the schematic router drops wires, which
            # is why the first human EE review met two dead pushbuttons.
            # Measured on the 2026-08-15 exports: 65/118/72 of 354/220/168
            # pins disagreed with the design on the three boards, 0 after, in
            # 6-12 seconds each, idempotent on all three.
            truth_pass = kicad_schematic.normalize_schematic_truth(
                kicad_sch,
                circuit_json,
                read_netlist=kicad_schematic.kicad_netlist,
            )
            if truth_pass.measured:
                warnings.append(
                    {
                        "part": "board",
                        "kind": "schematic_truth_normalized",
                        "detail": (
                            "the exported schematic was held to the design's "
                            f"netlist: {truth_pass.summary()}"
                        ),
                        "severity": (
                            "warning" if truth_pass.wrong_after else "info"
                        ),
                    }
                )
            for line in truth_pass.remaining:
                warnings.append(checks.check_failed(
                    f"exported schematic still disagrees with the design: {line}"
                ))
            for note in truth_pass.notes:
                warnings.append(checks.check_failed(note))

            erc_json = built_dir / "erc.json"
            try:
                toolchain.run_kicad(
                    [
                        "sch",
                        "erc",
                        "--format",
                        "json",
                        "--severity-all",
                        "--exit-code-violations",
                        "-o",
                        str(erc_json),
                        str(kicad_sch),
                    ],
                    timeout=KICAD_TIMEOUT_S,
                    ok_codes=(0, 5),
                )
                warnings.extend(
                    checks.parse_kicad_report(erc_json, kind="erc_violation")
                )
            except (RuntimeError, TimeoutError) as exc:
                warnings.append(checks.check_failed(f"kicad ERC failed: {exc}"))
        if kicad_pcb is not None:
            # Give kicad this fab's design rules before asking its opinion.
            # Without the project file KiCad grades the board against its own
            # stock defaults and buries the real findings (see
            # fab.kicad_project_json for the measured before/after).
            try:
                fab_mod.write_kicad_project(kicad_pcb, profile)
            except OSError as exc:
                warnings.append(
                    checks.check_failed(f"kicad project file not written: {exc}")
                )
            drc_json = built_dir / "drc.json"
            try:
                toolchain.run_kicad(
                    [
                        "pcb",
                        "drc",
                        "--schematic-parity",
                        "--all-track-errors",
                        "--format",
                        "json",
                        "--severity-all",
                        "--exit-code-violations",
                        "-o",
                        str(drc_json),
                        str(kicad_pcb),
                    ],
                    timeout=KICAD_TIMEOUT_S,
                    ok_codes=(0, 5),
                )
                warnings.extend(
                    checks.parse_kicad_report(drc_json, kind="drc_violation")
                )
            except (RuntimeError, TimeoutError) as exc:
                warnings.append(checks.check_failed(f"kicad DRC failed: {exc}"))
            # Shipping gerbers come from the converted board (the verified path).
            gerber_dir = built_dir / "kicad-gerbers"
            try:
                gerber_dir.mkdir(parents=True, exist_ok=True)
                toolchain.run_kicad(
                    ["pcb", "export", "gerbers", "-o", str(gerber_dir) + os.sep, str(kicad_pcb)],
                    timeout=KICAD_TIMEOUT_S,
                )
                toolchain.run_kicad(
                    ["pcb", "export", "drill", "-o", str(gerber_dir) + os.sep, str(kicad_pcb)],
                    timeout=KICAD_TIMEOUT_S,
                )
                kicad_gerbers_zip = fab_mod.zip_directory_gerbers(
                    gerber_dir, built_dir / "kicad-gerbers.zip"
                )
                gerber_source = "kicad-cli"
            except (RuntimeError, TimeoutError, OSError) as exc:
                warnings.append(
                    checks.check_failed(f"kicad gerber export failed: {exc}")
                )
    else:
        warnings.append(checks.kicad_unavailable_warning())

    if gerber_source != "kicad-cli":
        warnings.append(
            {
                "part": "board",
                "kind": "unverified_gerbers",
                "detail": "gerbers were exported by tscircuit without kicad-cli "
                "verification — do not ship this packet (install KiCad and rebuild)",
                "severity": "warning",
            }
        )

    warnings = checks.dedupe(warnings)

    progress.stage("export")

    # -- Stage 5: write the fab packet. --------------------------------------
    shutil.rmtree(fab_dir, ignore_errors=True)
    glb_path: Path | None = None
    cpl_path: Path | None = None
    bom_path: Path | None = None
    gerbers_path: Path | None = None
    try:
        if kicad_gerbers_zip is not None:
            fab_dir.mkdir(parents=True, exist_ok=True)
            gerbers_path = fab_dir / profile.zip_name
            shutil.copy2(kicad_gerbers_zip, gerbers_path)
        elif ts_gerbers_zip is not None:
            gerbers_path = fab_mod.repackage_gerbers(
                ts_gerbers_zip, fab_dir / profile.zip_name
            )
        if bom_rows:
            bom_path = fab_mod.write_bom_csv(
                bom_rows,
                fab_dir / profile.bom_name,
                profile,
                described=fab_mod.describe_components(circuit_json),
            )
            # A blank column is indistinguishable from "we do not know", and
            # that is how every BOM this project ever shipped went out with an
            # empty Footprint: the runtime that builds boards resolved the
            # catalog to a directory that does not exist, and silence read as
            # an answer. Say it out loud instead.
            if fab_mod.catalog_root() is None:
                warnings.append({
                    "part": "bom.csv",
                    "kind": "bom_catalog_missing",
                    "detail": (
                        "the JLCPCB parts catalog was not found, so the BOM's "
                        "Footprint column is blank on every line. JLC's "
                        "parts-match table uses that column to cross-check the "
                        "part number against what the designer thought they "
                        "were buying. Set CIRCUIT_PARTS_CATALOG, or re-run "
                        "scripts/build/build-skill-runtimes.sh"
                    ),
                    "severity": "warning",
                })
        if product.assembly and cpl_text:
            cpl_path = fab_mod.write_cpl_csv(cpl_text, fab_dir / profile.cpl_name, profile)
    except OSError as exc:
        raise ExportError(f"failed to write fab packet: {exc}") from exc
    try:
        glb_path = _cached("glb", profile.glb_name, ".glb")
        fab_dir.mkdir(parents=True, exist_ok=True)
        target = fab_dir / profile.glb_name
        shutil.copy2(glb_path, target)
        glb_path = target
    except (RuntimeError, TimeoutError, OSError):
        glb_path = None  # best-effort by contract

    # The enclosure brief: the exact facts the printed body needs, taken from
    # The KiCad project, zipped into the packet. We already convert the board
    # to `.kicad_sch`/`.kicad_pcb` to run the second-substrate check and were
    # throwing them away — but they are the only artifact in the packet a
    # person can open in a real EDA tool to review or edit the design. Gerbers
    # are for the fab; this is for the engineer.
    kicad_zip_path: Path | None = None
    if kicad_pcb is not None:
        try:
            kicad_zip_path = fab_dir / "kicad-project.zip"
            fab_dir.mkdir(parents=True, exist_ok=True)
            members: list[tuple[Path, str]] = [(kicad_pcb, f"{stem}.kicad_pcb")]
            project_file = kicad_pcb.with_suffix(".kicad_pro")
            if project_file.is_file():
                members.append((project_file, f"{stem}.kicad_pro"))
            if kicad_sch is not None and kicad_sch.is_file():
                members.append((kicad_sch, f"{stem}.kicad_sch"))
            _zip_deterministic(kicad_zip_path, members)
        except (OSError, zipfile.BadZipFile) as exc:
            kicad_zip_path = None
            warnings.append(
                checks.check_failed(f"kicad project not bundled: {exc}")
            )

    # the same geometry that produced the gerbers so the two cannot disagree.
    enclosure_path: Path | None = None
    try:
        enclosure_path = enclosure_mod.write_enclosure_spec(
            circuit_json, fab_dir / "enclosure.json", board_name=stem
        )
    except (OSError, ValueError) as exc:
        warnings.append(checks.check_failed(f"enclosure spec not written: {exc}"))

    # -- Stage 5b: the packet the fab actually receives. ---------------------
    # This cannot run any earlier: the zip does not exist until stage 5. It is
    # also the only check in the whole pipeline that inspects what JLCPCB is
    # sent rather than what we meant to send, so a bug in the export itself —
    # a missing layer, a drill in the wrong units, a footprint dropped between
    # the board and the plot — has nowhere else to be caught.
    if gerbers_path is not None and gerbers_path.is_file():
        warnings.extend(
            verify_bridge.check_packet(
                built_circuit_json,
                gerbers_path,
                profile=profile,
                assembly_order=product.assembly,
            )
        )

    warnings = checks.dedupe(warnings)
    ready = fab_mod.fab_ready(warnings, gerber_source)
    order_path: Path | None = None
    board_el = next(
        (
            e
            for e in circuit_json
            if isinstance(e, dict) and e.get("type") == "pcb_board"
        ),
        None,
    )
    width_mm = float((board_el or {}).get("width") or 0)
    height_mm = float((board_el or {}).get("height") or 0)
    layers = int((board_el or {}).get("num_layers") or product.layers)
    bom_block = fab_mod.bom_summary(bom_rows)
    if ready:
        try:
            order_path = fab_mod.write_order_md(
                fab_dir / profile.order_name,
                product_name=product.name,
                assembly=product.assembly,
                profile=profile,
                board_width_mm=width_mm,
                board_height_mm=height_mm,
                layers=layers,
                bom=bom_block,
            )
        except OSError as exc:
            raise ExportError(f"failed to write ORDER.md: {exc}") from exc

    progress.stage("render")

    # -- Stage 6: review images. ---------------------------------------------
    shutil.rmtree(review_dir, ignore_errors=True)
    try:
        review_written = review_mod.write_review(
            circuit_json_path=built_circuit_json,
            review_dir=review_dir,
            built_schematic_png=built_dir / "schematic.png",
            built_pcb_png=built_dir / "pcb.png",
            double_sided=review_mod.is_double_sided(circuit_json),
        )
    except (RuntimeError, TimeoutError) as exc:
        raise ExportError(f"failed to write review images: {exc}") from exc

    # -- Sidecar (camelCase canonical JSON), then the IR lands LAST. ---------
    artifacts: dict[str, str] = {
        "schematicPng": f"{stem}_review/_schematic.png",
        "pcbPng": f"{stem}_review/_pcb.png",
    }
    if gerbers_path is not None:
        artifacts["gerbers"] = f"{stem}_fab/{profile.zip_name}"
    if bom_path is not None:
        artifacts["bom"] = f"{stem}_fab/{profile.bom_name}"
    if cpl_path is not None:
        artifacts["cpl"] = f"{stem}_fab/{profile.cpl_name}"
    if order_path is not None:
        artifacts["order"] = f"{stem}_fab/{profile.order_name}"
    if glb_path is not None:
        artifacts["glb"] = f"{stem}_fab/{profile.glb_name}"
    if enclosure_path is not None:
        artifacts["enclosure"] = f"{stem}_fab/enclosure.json"
    if kicad_zip_path is not None:
        artifacts["kicadProject"] = f"{stem}_fab/kicad-project.zip"

    validation: dict[str, object] = {}
    if warnings:
        validation["warnings"] = warnings
    sidecar_payload: dict[str, object] = {
        "generator": GENERATOR_NAME,
        "entryKind": "board",
        "source": {
            "kind": "tsx",
            "path": identity.source_path,
            "hash": identity.source_hash,
            "fingerprint": identity.source_fingerprint,
        },
        "board": {
            "path": output_p.name,
            "name": product.name,
            "widthMm": width_mm,
            "heightMm": height_mm,
            "layers": layers,
        },
        "toolchain": _current_toolchain_block(),
        # How the board was routed. Present on every build so a 17-minute
        # escalated build is visible rather than mysterious, and so a reader
        # can tell "clean at the default effort" from "clean only at 5x" —
        # the second is a board one upstream change away from failing.
        "build": build_block,
        "bom": bom_block,
        "fab": {
            "profile": profile.id,
            "ready": ready,
            "assembly": product.assembly,
            "gerberSource": gerber_source,
            "packet": f"{stem}_fab/",
        },
        "validation": validation,
        "artifacts": artifacts,
    }
    try:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        sidecar_path.write_text(_canonical_json(sidecar_payload), encoding="utf-8")
    except OSError as exc:
        raise ExportError(f"failed to write metadata sidecar: {exc}") from exc

    # Ordering rule: the sidecar is on disk; the circuit.json artifact of
    # record appears (and gets its mtime) last, so artifact_changed fires
    # after metadata is readable.
    try:
        if _is_same_filesystem(built_circuit_json, boards_dir):
            os.replace(built_circuit_json, output_p)
        else:  # pragma: no cover — project and .circuit share a disk
            shutil.copy2(built_circuit_json, output_p)
            built_circuit_json.unlink(missing_ok=True)
        os.utime(output_p, None)
    except OSError as exc:
        raise ExportError(f"failed to move circuit.json into place: {exc}") from exc

    shutil.rmtree(work, ignore_errors=True)

    result: dict[str, object] = {
        "circuit_json_path": str(output_p),
        "metadata_path": str(sidecar_path),
        "schematic_png": str(review_written["_schematic.png"]),
        "pcb_png": str(review_written["_pcb.png"]),
        "board": {"width_mm": width_mm, "height_mm": height_mm, "layers": layers},
        "bom": _bom_result_block(bom_block),
        "fab": {"profile": profile.id, "ready": ready, "packet_dir": str(fab_dir)},
        "build": {
            "autorouter_effort": build_block["autorouterEffort"],
            "attempts": build_block["attempts"],
            "blocking_by_attempt": build_block["blockingByAttempt"],
        },
        "warnings": warnings,
    }
    progress.finish(
        ok=True,
        detail=f"{sum(1 for w in warnings if w.get('severity') == 'error')} blocking",
    )
    return result


def _bom_result_block(bom_block: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {
        "lines": bom_block.get("lines", 0),
        "orderable": bom_block.get("orderable", 0),
    }
    if "estimatedCostUsd" in bom_block:
        out["estimated_cost_usd"] = bom_block["estimatedCostUsd"]
    return out


def _unchanged_prior_result(
    *,
    sidecar_path: Path,
    identity: BoardSourceHash,
    output_p: Path,
    boards_dir: Path,
    fab_dir: Path,
) -> dict[str, object] | None:
    """Return the §3-shaped result reconstructed from the existing sidecar
    when the prior build is provably still valid; None means build for real.
    Conservative on every doubt: unreadable sidecar, fingerprint or toolchain
    drift, or any missing artifact falls through to a full build."""
    try:
        prior = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(prior, dict) or prior.get("generator") != GENERATOR_NAME:
        return None
    source = prior.get("source") or {}
    if source.get("fingerprint") != identity.source_fingerprint:
        return None
    try:
        if (prior.get("toolchain") or {}) != _current_toolchain_block():
            return None
    except RuntimeError:
        return None
    if not output_p.is_file():
        return None
    artifacts = prior.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        return None
    for rel in artifacts.values():
        if not isinstance(rel, str) or not (boards_dir / rel).exists():
            return None

    board = prior.get("board") or {}
    fab_meta = prior.get("fab") or {}
    bom_meta = prior.get("bom") or {}
    validation = prior.get("validation") or {}
    result: dict[str, object] = {
        "circuit_json_path": str(output_p),
        "metadata_path": str(sidecar_path),
        "schematic_png": str(boards_dir / str(artifacts.get("schematicPng"))),
        "pcb_png": str(boards_dir / str(artifacts.get("pcbPng"))),
        "board": {
            "width_mm": float(board.get("widthMm") or 0),
            "height_mm": float(board.get("heightMm") or 0),
            "layers": int(board.get("layers") or 0),
        },
        "bom": _bom_result_block(bom_meta if isinstance(bom_meta, dict) else {}),
        "fab": {
            "profile": str(fab_meta.get("profile") or ""),
            "ready": bool(fab_meta.get("ready")),
            "packet_dir": str(fab_dir),
        },
        "warnings": list(validation.get("warnings") or []),
        "unchanged": True,
    }
    return result


def _is_same_filesystem(path_a: Path, path_b: Path) -> bool:
    try:
        return path_a.stat().st_dev == path_b.stat().st_dev
    except OSError:
        return False
