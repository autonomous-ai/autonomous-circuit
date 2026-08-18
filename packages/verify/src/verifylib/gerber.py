"""An independent reader for the files we actually ship to the fab.

**Why write a parser rather than use one.** Nothing in the pipeline has ever
opened the zip. Every gate — the compiler's findings, ``@tscircuit/checks``,
KiCad ERC/DRC, our DFM table — runs *upstream* of the export, so a bug in the
export itself is invisible to all four and fatal at the fab: two weeks and $85
for a missing layer, a dropped drill file, a coordinate-format slip.

The survey (``docs/verification/gap-analysis.md`` §1.6) found no tool that
rule-checks gerbers. gerbv, tracespace and gerbonara all *render*; none of them
compares the output against the design that produced it. And a check that
re-derives the answer through the same library that wrote the file proves
nothing — the value here is entirely in being a second implementation.

**Scope.** RS-274X / X2 as KiCad 10 emits it, plus Excellon drill. Measured
against three real exports: flashes, linear draws, ``G36``/``G37`` region
contours (that is how a copper pour is plotted — ``hydrate-coaster`` has 39 of
them), and ``G85`` routed slots in the drill file (a USB-C receptacle's legs).
Circular interpolation (``G02``/``G03``) does not appear in any layer we
produce and raises a clean "unsupported" rather than being silently
mis-parsed — a parser that quietly drops geometry is the same failure as a
check that always passes.

Everything is returned in millimetres in the file's own coordinate frame. The
translation to board coordinates is solved separately, from the board outline
(see :mod:`verifylib.gerber_truth`), because deriving it that way turns a
units or scale error into a finding instead of a silent correction.
"""

from __future__ import annotations

import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from verifylib.model import Rect


class GerberError(Exception):
    """The file is not parseable as the dialect we support. Always surfaced as
    a finding — never swallowed, because unparsed geometry is unchecked
    geometry."""


# ---------------------------------------------------------------------------
# Apertures.
# ---------------------------------------------------------------------------


@dataclass
class Aperture:
    code: int
    shape: str                     # C, R, O, P, or a macro name
    params: tuple[float, ...] = ()
    #: Bounding size (w, h) in mm. For a macro this is the bbox of its
    #: primitives; for a circle both are the diameter.
    size: tuple[float, float] = (0.0, 0.0)
    macro: bool = False

    @property
    def smallest_dimension(self) -> float:
        w, h = self.size
        values = [v for v in (w, h) if v > 0]
        return min(values) if values else 0.0

    def rect_at(self, x: float, y: float) -> Rect:
        w, h = self.size
        return Rect.from_center(x, y, w, h)


@dataclass
class Flash:
    x: float
    y: float
    aperture: Aperture
    #: ``%LPC`` — this flash *removes* what is under it instead of adding ink.
    #: How a plotter subtracts one layer from another: KiCad's
    #: ``--subtract-soldermask`` knocks the pad shapes out of the silkscreen
    #: this way. A reader blind to it sees the strokes that were erased and
    #: reports ink that is not on the board.
    clear: bool = False

    @property
    def rect(self) -> Rect:
        return self.aperture.rect_at(self.x, self.y)


@dataclass
class Draw:
    x0: float
    y0: float
    x1: float
    y1: float
    aperture: Aperture

    @property
    def width(self) -> float:
        return self.aperture.smallest_dimension

    @property
    def length(self) -> float:
        return math.hypot(self.x1 - self.x0, self.y1 - self.y0)


@dataclass
class Region:
    """A ``G36``/``G37`` filled contour — how a copper pour is plotted.

    Read as a closed polyline, not as filled area: enough to bound it and to
    know it is there. Anything that needs the fill itself (pour-to-edge
    clearance, thermal relief spokes) is reported as uncovered rather than
    guessed at.
    """

    points: list[tuple[float, float]] = field(default_factory=list)

    @property
    def bounds(self) -> Rect | None:
        return Rect.bounding(self.points)


@dataclass
class GerberLayer:
    path: str
    #: The X2 ``TF.FileFunction`` attribute when present — the file's own claim
    #: about what it is, which is worth cross-checking against its extension.
    file_function: str | None = None
    units: str = "mm"
    int_digits: int = 4
    dec_digits: int = 6
    apertures: dict[int, Aperture] = field(default_factory=dict)
    flashes: list[Flash] = field(default_factory=list)
    draws: list[Draw] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    @property
    def bounds(self) -> Rect | None:
        points: list[tuple[float, float]] = []
        for flash in self.flashes:
            r = flash.rect
            points += [(r.x0, r.y0), (r.x1, r.y1)]
        for draw in self.draws:
            half = draw.width / 2
            points += [
                (min(draw.x0, draw.x1) - half, min(draw.y0, draw.y1) - half),
                (max(draw.x0, draw.x1) + half, max(draw.y0, draw.y1) + half),
            ]
        for region in self.regions:
            points += region.points
        return Rect.bounding(points)

    @property
    def centreline_bounds(self) -> Rect | None:
        """Extents ignoring aperture width — what an outline layer means by
        "the board edge"."""
        points: list[tuple[float, float]] = []
        for draw in self.draws:
            points += [(draw.x0, draw.y0), (draw.x1, draw.y1)]
        for flash in self.flashes:
            points.append((flash.x, flash.y))
        return Rect.bounding(points)

    @property
    def min_draw_width(self) -> float | None:
        widths = [d.width for d in self.draws if d.width > 0]
        return min(widths) if widths else None


_FS_RE = re.compile(r"^%FS([LT])([AI])X(\d)(\d)Y(\d)(\d)\*%$")
_AD_RE = re.compile(r"^%ADD(\d+)([A-Za-z_$][A-Za-z0-9_$.\-]*)(?:,(.*))?\*%$")
_COORD_RE = re.compile(
    r"(?:X(?P<x>[+-]?\d+))?(?:Y(?P<y>[+-]?\d+))?"
    r"(?:I(?P<i>[+-]?\d+))?(?:J(?P<j>[+-]?\d+))?"
    r"(?:D0?(?P<d>[123]))?\*$"
)
_DCODE_RE = re.compile(r"^(?:G\d+)?D(\d+)\*$")


def _macro_size(body: str, params: tuple[float, ...] = ()) -> tuple[float, float]:
    """Bounding size of an aperture macro, evaluated with its parameters.

    Handles the primitives KiCad emits: 1 (circle), 4 (outline), 20 (vector
    line), 21 (centre line).

    **The parameters are not optional.** KiCad defines `RotRect` and
    `RoundRect` once, parameterised, and supplies the real numbers at each
    `%ADD%`. An earlier version read only the definition, could not resolve a
    single `$n`, and returned a zero size — so **56 of 230 mask openings on
    harness-puck, a quarter of the layer and every fine-pitch QFN pad, were
    dropped before the sliver check ever saw them**. A check that silently
    skips the geometry most likely to be wrong is worse than no check.

    Rotation is applied, because a rotated pad's bounding box is not its
    unrotated one. The box still overstates a rotated shape, which understates
    the gap to its neighbour — the conservative direction.
    """
    xs: list[float] = []
    ys: list[float] = []

    def substitute(token: str) -> str:
        token = token.strip()
        for index in range(len(params), 0, -1):
            token = token.replace(f"${index}", repr(params[index - 1]))
        return token

    def number(token: str) -> float | None:
        token = substitute(token)
        if not token or "$" in token:
            return None
        try:
            return float(token)
        except ValueError:
            try:  # simple arithmetic appears in macro bodies ($1+$1)
                return float(eval(token, {"__builtins__": {}}, {}))  # noqa: S307
            except Exception:  # noqa: BLE001
                return None

    for raw in body.split("*"):
        line = raw.strip()
        if not line or line.startswith("0"):
            continue
        fields = line.split(",")
        code = number(fields[0])
        if code is None:
            continue
        try:
            if code == 1 and len(fields) >= 5:  # exposure, diameter, x, y
                d = number(fields[2]) or 0.0
                cx, cy = number(fields[3]) or 0.0, number(fields[4]) or 0.0
                xs += [cx - d / 2, cx + d / 2]
                ys += [cy - d / 2, cy + d / 2]
            elif code == 4 and len(fields) >= 5:  # exposure, n, x0, y0, ..., rot
                count = number(fields[2])
                coords = fields[3:]
                if count is not None:
                    coords = coords[: int(count) * 2 + 2]
                for i in range(0, len(coords) - 1, 2):
                    px, py = number(coords[i]), number(coords[i + 1])
                    if px is not None and py is not None:
                        xs.append(px)
                        ys.append(py)
            elif code == 20 and len(fields) >= 7:  # exposure, w, x0,y0, x1,y1
                w = number(fields[2]) or 0.0
                for i in (3, 5):
                    px, py = number(fields[i]), number(fields[i + 1])
                    if px is not None and py is not None:
                        xs += [px - w / 2, px + w / 2]
                        ys += [py - w / 2, py + w / 2]
            elif code == 21 and len(fields) >= 6:  # exposure, w, h, cx, cy, rot
                w = number(fields[2]) or 0.0
                h = number(fields[3]) or 0.0
                cx, cy = number(fields[4]) or 0.0, number(fields[5]) or 0.0
                rotation = number(fields[6]) if len(fields) >= 7 else 0.0
                corners = [
                    (cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2),
                    (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2),
                ]
                for px, py in _rotate(corners, rotation or 0.0):
                    xs.append(px)
                    ys.append(py)
        except (IndexError, TypeError):
            continue
    if not xs or not ys:
        return (0.0, 0.0)
    return (max(xs) - min(xs), max(ys) - min(ys))


def _rotate(points, degrees: float):
    if not degrees:
        return points
    theta = math.radians(degrees)
    cos, sin = math.cos(theta), math.sin(theta)
    return [(x * cos - y * sin, x * sin + y * cos) for x, y in points]


def _standard_size(shape: str, params: tuple[float, ...]) -> tuple[float, float]:
    if shape == "C" and params:
        return (params[0], params[0])
    if shape in ("R", "O") and len(params) >= 2:
        return (params[0], params[1])
    if shape == "P" and params:  # regular polygon, circumscribed diameter
        return (params[0], params[0])
    return (0.0, 0.0)


def parse_gerber(text: str, *, path: str = "<memory>") -> GerberLayer:
    """Parse one RS-274X layer. Raises :class:`GerberError` on a dialect we do
    not fully support, so unread geometry can never look like clean geometry."""
    layer = GerberLayer(path=path)
    macro_bodies: dict[str, str] = {}
    scale = 10.0**-layer.dec_digits
    current: Aperture | None = None
    x = y = 0.0
    pending_macro: list[str] | None = None
    pending_macro_name: str | None = None
    region: Region | None = None
    clear = False  # %LPC until the next %LPD

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        if pending_macro is not None:
            pending_macro.append(line)
            if line.endswith("%"):
                macro_bodies[pending_macro_name or ""] = "\n".join(
                    pending_macro
                ).rstrip("%")
                pending_macro = None
                pending_macro_name = None
            continue

        if line.startswith("%AM"):
            pending_macro_name = line[3:].rstrip("*")
            pending_macro = []
            if line.endswith("%"):
                macro_bodies[pending_macro_name.rstrip("*%")] = ""
                pending_macro = None
                pending_macro_name = None
            continue

        if line.startswith("G04") or line.startswith("%TF") or line.startswith("%TA"):
            if "TF.FileFunction," in line:
                layer.file_function = line.split("TF.FileFunction,", 1)[1].rstrip("*%")
            continue
        # `%LP` used to be skipped here with the attribute commands. It is not
        # an attribute — it decides whether what follows adds ink or removes
        # it — so it falls through to the polarity handler below.
        if line.startswith("%TO") or line.startswith("%TD"):
            continue
        if line.startswith("%MO"):
            layer.units = "in" if "IN" in line else "mm"
            continue

        match = _FS_RE.match(line)
        if match:
            if match.group(2) != "A":
                raise GerberError(f"{path}: incremental coordinates are not supported")
            layer.int_digits = int(match.group(3))
            layer.dec_digits = int(match.group(4))
            scale = 10.0**-layer.dec_digits
            continue

        match = _AD_RE.match(line)
        if match:
            code = int(match.group(1))
            shape = match.group(2)
            raw_params = match.group(3) or ""
            params = tuple(
                float(p) for p in raw_params.split("X") if p.strip() and _is_number(p)
            )
            if shape in ("C", "R", "O", "P"):
                aperture = Aperture(code, shape, params, _standard_size(shape, params))
            else:
                body = macro_bodies.get(shape, "")
                aperture = Aperture(
                    code, shape, params, _macro_size(body, params), macro=True
                )
            layer.apertures[code] = aperture
            continue

        if line.startswith("%LP"):
            # Layer polarity. Dark adds ink, clear removes it. Everything
            # plotted after the command carries that polarity until the next
            # one, so this is state, not geometry.
            clear = line[3:4].upper() == "C"
            continue

        if line.startswith("%"):
            continue  # any other extended command carries no geometry we read

        if line in ("M02*", "M00*"):
            break
        if line in ("G01*", "G75*", "G70*", "G71*", "G90*", "G91*"):
            continue
        if line.startswith("G36"):
            region = Region()
            continue
        if line.startswith("G37"):
            if region is not None and len(region.points) >= 3:
                layer.regions.append(region)
            region = None
            continue
        if line.startswith("G02") or line.startswith("G03"):
            layer.unsupported.append("circular interpolation (G02/G03)")
            continue

        match = _DCODE_RE.match(line)
        if match:
            code = int(match.group(1))
            if code >= 10:
                current = layer.apertures.get(code)
                continue

        match = _COORD_RE.search(line)
        if match and (match.group("x") or match.group("y") or match.group("d")):
            nx = float(match.group("x")) * scale if match.group("x") else x
            ny = float(match.group("y")) * scale if match.group("y") else y
            op = match.group("d")
            if region is not None:
                if op in ("1", "2"):
                    if not region.points:
                        region.points.append((x, y))
                    region.points.append((nx, ny))
                x, y = nx, ny
                continue
            if op == "1":
                if current is not None and not clear:
                    layer.draws.append(Draw(x, y, nx, ny, current))
            elif op == "3":
                if current is not None:
                    layer.flashes.append(Flash(nx, ny, current, clear=clear))
            x, y = nx, ny
            continue

    if layer.units != "mm":
        raise GerberError(f"{path}: units are {layer.units}, expected mm")
    return layer


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Excellon drill.
# ---------------------------------------------------------------------------


@dataclass
class DrillTool:
    code: int
    diameter_mm: float
    plated: bool = True
    function: str | None = None


@dataclass
class DrillHit:
    x: float
    y: float
    tool: DrillTool
    #: Second endpoint when the hit is a routed slot (``G85``). A pill-shaped
    #: pad — a USB-C receptacle's through-hole legs, for one — is drilled this
    #: way, and treating it as a round hole at ``(x, y)`` misplaces it by half
    #: the slot's travel. That mistake reported four phantom missing drills on
    #: every one of our example boards.
    x2: float | None = None
    y2: float | None = None

    @property
    def is_slot(self) -> bool:
        return self.x2 is not None and self.y2 is not None

    @property
    def center(self) -> tuple[float, float]:
        if self.x2 is None or self.y2 is None:
            return (self.x, self.y)
        return ((self.x + self.x2) / 2, (self.y + self.y2) / 2)

    @property
    def size(self) -> tuple[float, float]:
        """Overall drilled extent (w, h): the tool diameter grown along the
        slot's travel."""
        d = self.tool.diameter_mm
        if self.x2 is None or self.y2 is None:
            return (d, d)
        return (d + abs(self.x2 - self.x), d + abs(self.y2 - self.y))


@dataclass
class DrillFile:
    path: str
    tools: dict[int, DrillTool] = field(default_factory=dict)
    hits: list[DrillHit] = field(default_factory=list)
    units: str = "mm"
    unsupported: list[str] = field(default_factory=list)

    @property
    def bounds(self) -> Rect | None:
        points: list[tuple[float, float]] = []
        for hit in self.hits:
            points.append((hit.x, hit.y))
            if hit.x2 is not None and hit.y2 is not None:
                points.append((hit.x2, hit.y2))
        return Rect.bounding(points)


_TOOL_RE = re.compile(r"^T(\d+)C([0-9.]+)")
_HIT_RE = re.compile(
    r"^X(?P<x>[+-]?[0-9.]+)Y(?P<y>[+-]?[0-9.]+)"
    r"(?:G85X(?P<x2>[+-]?[0-9.]+)Y(?P<y2>[+-]?[0-9.]+))?"
)
_SELECT_RE = re.compile(r"^T(\d+)\s*$")


def parse_excellon(text: str, *, path: str = "<memory>") -> DrillFile:
    """Parse a KiCad-style Excellon file (METRIC, decimal, absolute)."""
    drill = DrillFile(path=path)
    plated_next = True
    function_next: str | None = None
    current: DrillTool | None = None
    in_header = True

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            if "TA.AperFunction," in line:
                attr = line.split("TA.AperFunction,", 1)[1]
                plated_next = not attr.upper().startswith("NONPLATED")
                function_next = attr.strip()
            continue
        if line in ("M48",):
            in_header = True
            continue
        if line == "%":
            in_header = False
            continue
        if line.upper() == "METRIC" or line.upper().startswith("METRIC,"):
            drill.units = "mm"
            continue
        if line.upper() == "INCH" or line.upper().startswith("INCH,"):
            drill.units = "in"
            continue
        if line in ("G90", "G05", "G00", "FMAT,2", "M30", "M95"):
            continue

        match = _TOOL_RE.match(line)
        if match and in_header:
            code = int(match.group(1))
            drill.tools[code] = DrillTool(
                code=code,
                diameter_mm=float(match.group(2)),
                plated=plated_next,
                function=function_next,
            )
            plated_next = True
            function_next = None
            continue

        match = _SELECT_RE.match(line)
        if match:
            current = drill.tools.get(int(match.group(1)))
            continue

        match = _HIT_RE.match(line)
        if match and current is not None:
            x2 = match.group("x2")
            y2 = match.group("y2")
            drill.hits.append(
                DrillHit(
                    float(match.group("x")),
                    float(match.group("y")),
                    current,
                    float(x2) if x2 is not None else None,
                    float(y2) if y2 is not None else None,
                )
            )
            continue

    if drill.units != "mm":
        raise GerberError(f"{path}: drill units are {drill.units}, expected mm")
    return drill


# ---------------------------------------------------------------------------
# The packet.
# ---------------------------------------------------------------------------


#: Extension -> the role the fab reads it as. Protel extensions plus KiCad's
#: ``.gbr`` fallback, matched on the filename stem's suffix.
_ROLE_BY_EXTENSION = {
    ".gtl": "copper_top",
    ".gbl": "copper_bottom",
    ".gts": "mask_top",
    ".gbs": "mask_bottom",
    ".gto": "silk_top",
    ".gbo": "silk_bottom",
    ".gtp": "paste_top",
    ".gbp": "paste_bottom",
    ".gko": "outline",
    ".gm1": "outline",
    ".gml": "outline",
    ".drl": "drill",
    ".xln": "drill",
    ".txt": "drill",
}

_ROLE_BY_STEM = (
    ("edge_cuts", "outline"),
    ("edgecuts", "outline"),
    ("f_cu", "copper_top"),
    ("b_cu", "copper_bottom"),
    ("f_mask", "mask_top"),
    ("b_mask", "mask_bottom"),
    ("f_silkscreen", "silk_top"),
    ("b_silkscreen", "silk_bottom"),
    ("f_paste", "paste_top"),
    ("b_paste", "paste_bottom"),
)


#: Layers KiCad plots that the fab does not consume. Listing them as "unknown
#: role" made the coverage report twelve lines long and said nothing; they are
#: not gaps, they are documentation the fab ignores.
_NOT_FAB_INPUT = (
    "adhesive", "courtyard", "_fab.", "margin", "user_comments",
    "user_drawings", "user_eco", ".gbrjob", "readme",
)


def is_fab_input(name: str) -> bool:
    lower = name.lower()
    return not any(needle in lower for needle in _NOT_FAB_INPUT)


def role_of(name: str) -> str | None:
    lower = name.lower()
    if not is_fab_input(lower):
        return None
    for needle, role in _ROLE_BY_STEM:
        if needle in lower:
            return role
    return _ROLE_BY_EXTENSION.get(Path(lower).suffix)


@dataclass
class Packet:
    """A parsed gerber zip: every layer we could read, keyed by role."""

    source: str
    layers: dict[str, GerberLayer] = field(default_factory=dict)
    drills: list[DrillFile] = field(default_factory=list)
    #: Members present in the zip that we did not read, and why.
    ignored: list[str] = field(default_factory=list)
    #: Members the fab does not consume at all (documentation plots).
    not_fab_input: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def roles(self) -> set[str]:
        roles = set(self.layers)
        if self.drills:
            roles.add("drill")
        return roles


def read_packet(zip_path: str | Path) -> Packet:
    """Open a gerber zip and parse every member we recognise.

    A member that fails to parse becomes an entry in ``errors``, never an
    exception — the caller turns that into a finding. An unparsed layer is an
    *unchecked* layer and has to be reported as such.
    """
    zip_path = Path(zip_path)
    packet = Packet(source=str(zip_path))
    with zipfile.ZipFile(zip_path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/"):
                continue
            role = role_of(name)
            if role is None:
                if not is_fab_input(name):
                    packet.not_fab_input.append(name)
                else:
                    packet.ignored.append(f"{name}: no known fab role for this name")
                continue
            try:
                text = archive.read(name).decode("utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                packet.errors.append(f"{name}: unreadable ({exc})")
                continue
            try:
                if role == "drill":
                    packet.drills.append(parse_excellon(text, path=name))
                else:
                    layer = parse_gerber(text, path=name)
                    if role in packet.layers:
                        packet.errors.append(
                            f"{name}: a second file claims the {role} role "
                            f"(already {packet.layers[role].path})"
                        )
                    packet.layers[role] = layer
            except GerberError as exc:
                packet.errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001
                packet.errors.append(f"{name}: parse failed ({type(exc).__name__}: {exc})")
    return packet
