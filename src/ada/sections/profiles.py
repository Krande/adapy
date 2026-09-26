from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from functools import lru_cache

from ada.api.curves import CurveOpen2d, CurvePoly2d
from ada.config import get_logger
from ada.core.utils import roundoff as rd
from ada.sections.categories import BaseTypes

from .concept import GeneralProperties, Section, SectionParts

logger = get_logger()


@dataclass
class SectionProfile:
    sec: Section
    is_solid: bool
    outer_curve: CurvePoly2d = None
    inner_curve: CurvePoly2d = None
    outer_curve_disconnected: list[CurveOpen2d] = None
    inner_curve_disconnected: list[CurveOpen2d] = None
    disconnected: bool = None
    shell_thickness_map: list[tuple[str, float]] = None


def build_section_profile(sec: Section, is_solid) -> SectionProfile:
    if sec.type in [BaseTypes.TUBULAR, BaseTypes.CIRCULAR, BaseTypes.GENERAL]:
        logger.info("Tubular profiles do not need curve representations")
        return SectionProfile(sec, is_solid)

    build_map = {
        BaseTypes.ANGULAR: angular,
        BaseTypes.IPROFILE: iprofiles,
        BaseTypes.TPROFILE: tprofiles,
        BaseTypes.BOX: box,
        BaseTypes.FLATBAR: flatbar,
        BaseTypes.CHANNEL: channel,
    }

    section_builder = build_map.get(sec.type, None)

    if section_builder is None and sec.poly_outer is None:
        raise ValueError("Currently geometry build is unsupported for profile type {ptype}".format(ptype=sec.type))

    if section_builder is not None:
        section_profile = section_builder(sec, is_solid)
    else:
        section_profile = SectionProfile(
            sec,
            outer_curve=sec.poly_outer,
            inner_curve=sec.poly_inner,
            is_solid=is_solid,
            disconnected=False,
        )

    return section_profile


build_props = dict(origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))


def build_disconnected(input_curve: list[tuple[tuple, tuple]]) -> list[CurveOpen2d]:
    return [CurveOpen2d(x, **build_props) for x in input_curve]


def build_joined(input_curve: list[tuple]) -> CurvePoly2d:
    return CurvePoly2d(input_curve, **build_props)


@lru_cache
def angular(sec: Section, return_solid) -> SectionProfile:
    h = sec.h
    wbtn = sec.w_btn
    p1 = (0.0, 0.0)
    p2 = (0.0, -h)
    p3 = (wbtn, -h)

    outer_curve_disconnected = None
    outer_curve = None
    shell_thick_map = None

    if return_solid is False:
        disconnected = True
        outer_curve_disconnected = build_disconnected([(p1, p2), (p2, p3)])
        shell_thick_map = [(SectionParts.WEB, sec.t_w), (SectionParts.BTN_FLANGE, sec.t_fbtn)]
    else:
        disconnected = False
        tf = sec.t_fbtn
        tw = sec.t_w
        p4 = (wbtn, -h + tf)
        p5 = (tw, -h + tf)
        p6 = (tw, 0.0)
        outer_curve = build_joined([p1, p2, p3, p4, p5, p6])

    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        outer_curve_disconnected=outer_curve_disconnected,
        disconnected=disconnected,
        shell_thickness_map=shell_thick_map,
    )


#: Shortest segment `CurvePoly2d` will keep (its `_points_to_segments` default).
#: A feature smaller than this cannot survive into the curve at all, so a flange
#: thinner than it is not geometry — it is a placeholder for "no flange".
CURVE_SEGMENT_TOL = 1e-3


def _top_flange_is_absent(wtop, tw, tftop) -> bool:
    """Is this I-section's top flange a placeholder rather than a flange?

    True only when it contributes nothing that could be built: no overhang
    beyond the web on either side, AND a thickness below the curve tolerance.
    A genuinely thin but wide flange keeps both tests from firing, so it is
    still drawn.
    """
    if wtop is None or tw is None or tftop is None:
        return False
    return (wtop - tw) / 2 <= CURVE_SEGMENT_TOL and tftop <= CURVE_SEGMENT_TOL


def drop_coincident(points, tol=1e-9):
    """Drop consecutive duplicate vertices from a closed outline.

    A zero-length edge is not something OCCT will build a face from: it fails
    the whole profile with ``StdFail_NotDone: BRep_API: command not done``, and
    since the profile is what a beam is swept from, one degenerate vertex pair
    costs the entire member rather than the millimetre it describes.

    This is reachable from real data. Sesam's ``GIORH`` card has no T variant,
    so a T-bar is written as an I-section whose top flange IS the web: flange
    width equal to the web thickness, flange thickness a near-zero sentinel. The
    two upper web/flange junctions then land exactly on the two top-flange
    corners, and the outline carries two zero-length edges.

    Points may be ``(x, y)`` or ``(x, y, fillet_radius)``; only the position
    decides coincidence, and the first of a coincident run is the one kept, so
    a fillet radius on the survivor is preserved.
    """
    kept = []
    for point in points:
        if kept and abs(point[0] - kept[-1][0]) <= tol and abs(point[1] - kept[-1][1]) <= tol:
            continue
        kept.append(point)
    # Closed outline: the last point may also coincide with the first.
    while len(kept) > 1 and abs(kept[-1][0] - kept[0][0]) <= tol and abs(kept[-1][1] - kept[0][1]) <= tol:
        kept.pop()
    return kept


@lru_cache
def iprofiles(sec: Section, return_solid) -> SectionProfile:
    h = sec.h
    wbtn = sec.w_btn
    wtop = sec.w_top

    # top flange
    c1 = (-wtop / 2, h / 2)
    c2 = (wtop / 2, h / 2)
    # web
    p3 = (0.0, h / 2)
    p4 = (0.0, -h / 2)
    # bottom flange
    c3 = (-wbtn / 2, -h / 2)
    c4 = (wbtn / 2, -h / 2)

    outer_curve = None
    outer_curve_disconnected = None
    shell_thick_map = None
    if return_solid is False:
        disconnected = True
        input_curve = [(c1, c2), (p3, p4), (c3, c4)]
        outer_curve_disconnected = build_disconnected(input_curve)
        shell_thick_map = [
            (SectionParts.TOP_FLANGE, sec.t_ftop),
            (SectionParts.WEB, sec.t_w),
            (SectionParts.BTN_FLANGE, sec.t_fbtn),
        ]
    else:
        disconnected = False
        tfbtn = sec.t_fbtn
        tftop = sec.t_ftop
        tw = sec.t_w
        if _top_flange_is_absent(wtop, tw, tftop):
            # Sesam's GIORH card has no T variant, so a T-bar is written as an
            # I-section whose "top flange" is a placeholder: width equal to the
            # web thickness and thickness a near-zero sentinel. Building that
            # literally produces two zero-length edges (the flange corners land
            # on the web/flange junctions) and two sub-tolerance segments, and
            # the profile fails outright — costing the whole member to render a
            # flange that was never there.
            #
            # It is a T, so build a T: the web runs to full height and there is
            # no cap. Only the swept geometry is affected; section properties
            # come from the parameters, not from this outline.
            wtop = tw
            tftop = 0.0
        p3 = (wtop / 2, h / 2 - tftop)
        p4 = (tw / 2, h / 2 - tftop)
        p5 = (tw / 2, -h / 2 + tfbtn)
        p6 = (wbtn / 2, -h / 2 + tfbtn)
        p7 = (-wbtn / 2, -h / 2 + tfbtn)
        p8 = (-tw / 2, -h / 2 + tfbtn)
        p9 = (-tw / 2, h / 2 - tftop)
        p10 = (-wtop / 2, h / 2 - tftop)
        # Round the four web/flange junctions (p4,p5,p8,p9) when the section carries a fillet
        # radius (IfcIShapeProfileDef.FilletRadius, stored in ``r``). CurvePoly2d reads the 3rd
        # tuple element as a per-vertex fillet radius and inserts the arc. A radius that won't
        # fit (>= the flange overhang or half the web depth) is skipped — it would self-
        # intersect the outline.
        r = getattr(sec, "r", None)
        overhang = (wtop - tw) / 2
        web_half = h / 2 - max(tftop, tfbtn)
        if r and 0 < r < min(overhang, web_half):

            def _fil(pt):
                return (pt[0], pt[1], r)

            p4, p5, p8, p9 = _fil(p4), _fil(p5), _fil(p8), _fil(p9)
        input_curve = drop_coincident([c1, c2, p3, p4, p5, p6, c4, c3, p7, p8, p9, p10])
        outer_curve = build_joined(input_curve)

    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        outer_curve_disconnected=outer_curve_disconnected,
        disconnected=disconnected,
        shell_thickness_map=shell_thick_map,
    )


@lru_cache
def tprofiles(sec: Section, return_solid) -> SectionProfile:
    h = sec.h
    wtop = sec.w_top

    # top flange
    c1 = (-wtop / 2, h / 2)
    c2 = (wtop / 2, h / 2)
    # web
    p3 = (0.0, h / 2)
    p4 = (0.0, -h / 2)

    outer_curve = None
    outer_curve_disconnected = None
    shell_thick_map = None
    if return_solid is False:
        disconnected = True
        input_curve = [(c1, c2), (p3, p4)]
        outer_curve_disconnected = build_disconnected(input_curve)
        shell_thick_map = [(SectionParts.TOP_FLANGE, sec.t_ftop), (SectionParts.WEB, sec.t_w)]
    else:
        disconnected = False
        tftop = sec.t_ftop
        tw = sec.t_w
        p3 = (wtop / 2, h / 2 - tftop)
        p4 = (tw / 2, h / 2 - tftop)
        p5 = (tw / 2, -h / 2)
        p8 = (-tw / 2, -h / 2)
        p9 = (-tw / 2, h / 2 - tftop)
        p10 = (-wtop / 2, h / 2 - tftop)
        input_curve = [c1, c2, p3, p4, p5, p8, p9, p10]
        outer_curve = build_joined(input_curve)

    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        outer_curve_disconnected=outer_curve_disconnected,
        disconnected=disconnected,
        shell_thickness_map=shell_thick_map,
    )


@lru_cache
def box(sec: Section, return_solid) -> SectionProfile:
    h = sec.h
    wtop = sec.w_top
    wbtn = sec.w_btn

    p1 = (rd(-wtop / 2), rd(h / 2))
    p2 = (rd(wtop / 2), rd(h / 2))
    p3 = (rd(wbtn / 2), rd(-h / 2))
    p4 = (rd(-wbtn / 2), rd(-h / 2))

    inner_curve = None
    if return_solid is False:
        outer_curve = build_joined([p1, p2, p3, p4])
    else:
        # t_ftop, not t_fbtn. Read from the bottom, an asymmetric box came back
        # with its BOTTOM flange thickness on both faces -- invisible on every
        # RHS/SHS, where the wall is uniform, and wrong by the difference on a
        # welded box girder, whose flanges are separate plates: a 35 mm top over
        # a 25 mm bottom came out 25/25.
        tftop = sec.t_ftop
        tfbtn = sec.t_fbtn
        tw = sec.t_w
        p5 = (rd(-wtop / 2 + tw), rd(h / 2 - tftop))
        p6 = (rd(wtop / 2 - tw), rd(h / 2 - tftop))
        p7 = (rd(wbtn / 2 - tw), rd(-h / 2 + tfbtn))
        p8 = (rd(-wbtn / 2 + tw), rd(-h / 2 + tfbtn))

        outer_curve = build_joined([p1, p2, p3, p4])
        inner_curve = build_joined([p5, p6, p7, p8])

    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        inner_curve=inner_curve,
        disconnected=False,
    )


@lru_cache
def flatbar(sec: Section, return_solid=False) -> SectionProfile:
    if return_solid is False:
        outer_curve = build_disconnected([((0, sec.h / 2), (sec.w_top / 2, -sec.h / 2))])
        return SectionProfile(
            sec,
            return_solid,
            outer_curve_disconnected=outer_curve,
            disconnected=True,
        )

    input_curve = [
        (-sec.w_top / 2, sec.h / 2),
        (sec.w_top / 2, sec.h / 2),
        (sec.w_top / 2, -sec.h / 2),
        (-sec.w_top / 2, -sec.h / 2),
    ]
    outer_curve = build_joined(input_curve)
    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        disconnected=False,
    )


@lru_cache
def channel(sec: Section, return_solid=False) -> SectionProfile:
    # top flange outer
    p1 = (sec.w_top, sec.h / 2)  # right corner
    # web
    p2 = (0, sec.h / 2)  # top of web
    p3 = (0, -sec.h / 2)  # bottom of web
    # bottom flange outer
    p4 = (sec.w_top, -sec.h / 2)  # right corner

    if return_solid is False:
        input_curve = [(p1, p2), (p2, p3), (p3, p4)]
        outer_curve = build_disconnected(input_curve)
        return SectionProfile(
            sec,
            return_solid,
            outer_curve_disconnected=outer_curve,
            disconnected=True,
        )

    input_curve = [
        p1,
        p2,
        p3,
        p4,
        (sec.w_top, -sec.h / 2 + sec.t_fbtn),
        (sec.t_w, -sec.h / 2 + sec.t_fbtn),
        (sec.t_w, sec.h / 2 - sec.t_fbtn),
        (sec.w_top, sec.h / 2 - sec.t_ftop),
    ]
    outer_curve = build_joined(input_curve)
    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        disconnected=False,
    )


# ---------------------------------------------------------------------------------------------
# Section -> Abaqus cross-section, for both the INP writer and the CAE script writer
# ---------------------------------------------------------------------------------------------
#
# One mapping, two consumers. The INP writer needs the ``*Beam Section`` ``section=`` keyword and
# its data line; the CAE writer needs the profile class on ``mdb.models[..]`` and its keyword
# arguments. They are the same decision, made once here, so the two cannot drift -- and so a
# section type added to one is added to both.
#
# Everything about the CAE side below was measured against an Abaqus 2025 kernel rather than
# recalled: each class was constructed with distinct values and its members read back, so the
# argument at position k is the one that came back holding value k.
#
# And a class existing is not evidence that it can be used. ``ChannelProfile`` is the proof: it is
# there, it is named after the shape, it builds -- and the deck CAE writes from it is refused by
# Abaqus' own preprocessor. So the question each mapping below had to answer was not "is there a
# class for this shape" but "does a solver accept the deck that comes out of it", and the authority
# for that is ``abaqus datacheck``, never the API surface. See
# :data:`CAE_PROFILE_CLASSES_THE_SOLVER_REJECTS`.

log_fin = "Please check your result and input. This is not a validated method of solving this issue"

#: Constructor arguments of every CAE profile class mapped below, in the kernel's own positional
#: order with ``name`` excluded. Probed; see the block comment above. A spec's ``cae_kwargs`` keys
#: must be exactly the entry for its ``cae_class`` -- a misspelt argument is a ``TypeError`` inside
#: Abaqus, and a *missing* one is worse: ``BoxProfile``'s ``uniformThickness`` decides whether
#: ``t2..t4`` are read at all, so leaving it out yields a box with one wall thickness on all four
#: walls and no error anywhere.
CAE_PROFILE_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "BoxProfile": ("a", "b", "uniformThickness", "t1", "t2", "t3", "t4"),
    "CircularProfile": ("r",),
    "GeneralizedProfile": ("area", "i11", "i12", "i22", "j", "gammaO", "gammaW"),
    "IProfile": ("l", "h", "b1", "b2", "t1", "t2", "t3"),
    "LProfile": ("a", "b", "t1", "t2"),
    "PipeProfile": ("r", "t"),
    "RectangularProfile": ("a", "b"),
    "TProfile": ("b", "h", "l", "tf", "tw"),
}

#: CAE profile classes that exist on ``mdb.models[..]``, build without complaint, and must still
#: never reach a :class:`ProfileSpec`, with the measurement that says so. They are listed here rather
#: than merely left out of :data:`CAE_PROFILE_ARGUMENTS` so that reaching for one -- which the API
#: surface positively invites, since the class is right there and named after the shape -- fails at
#: construction with the reason instead of failing in someone's solver run.
CAE_PROFILE_CLASSES_THE_SOLVER_REJECTS: dict[str, str] = {
    "ChannelProfile": (
        "Abaqus/Standard rejects the section=CHANNEL that Abaqus/CAE itself writes for one -- "
        '***ERROR: in keyword *BEAMSECTION ... Illegal value "CHANNEL" for parameter "section" -- '
        "and CAE's own getMassProperties() returns mass=None for a member carrying it. Measured on "
        "Abaqus 2025 via abaqus datacheck, identically for o=0 and o=0.05, so no argument avoids it. "
        "Use GeneralizedProfile: see ada.sections.profiles._channel_spec."
    ),
}

#: ``cae_kwargs`` entries whose value is an ``abaqusConstants`` symbol rather than a number. They
#: must be emitted as a bare name (``uniformThickness=OFF``); quoted, Abaqus rejects the call with
#: "found String, expecting ON or OFF". :meth:`ProfileSpec.cae_kwargs_source` handles this.
CAE_SYMBOL_ARGUMENTS = frozenset({"uniformThickness"})


@dataclass(frozen=True)
class ProfileSpec:
    """How one adapy :class:`~ada.Section` is expressed as a cross-section in Abaqus, both ways.

    ``inp_dims`` is the first ``*Beam Section`` data line in Abaqus' order for ``inp_kind``, and
    ``cae_kwargs`` the arguments of ``cae_class``. The two describe the same cross-section, and for
    most parametric profiles they carry the same numbers in the same order -- probed: a
    ``BoxProfile(a, b, OFF, t1, t2, t3, t4)`` asked to write itself out comes back as ``section=BOX``
    with exactly ``a, b, t1, t2, t3, t4``.

    They are separate fields because the two targets do not always take the same numbers: a T is an
    ``IProfile`` data line in the INP and a ``TProfile`` in CAE (see :func:`_tprofile_spec`), and a
    generalized section carries five numbers in the INP against seven in CAE (see
    :func:`_general_spec`).

    Numbers are passed through from the :class:`~ada.Section` unconverted, not coerced to ``float``,
    so a value that is an ``int`` still renders as ``0`` rather than ``0.0`` and the INP this writes
    is byte-for-byte what the writer produced before the mapping was extracted.
    """

    base_type: BaseTypes
    inp_kind: str
    inp_dims: tuple[float, ...]
    cae_class: str
    #: Values are numbers, except for the keys in :data:`CAE_SYMBOL_ARGUMENTS`, which carry the name
    #: of an ``abaqusConstants`` symbol as a ``str``.
    cae_kwargs: dict[str, float | str]
    #: The data rows after the first, for the one ``inp_kind`` whose data block runs to more than a
    #: single line: ``ARBITRARY``, which takes a row per wall segment. Empty for every other kind,
    #: where ``inp_dims`` is the whole data block.
    #:
    #: For an ``ARBITRARY`` section ``inp_dims`` is consequently that keyword's *first line* -- the
    #: segment count followed by the first segment -- and not a list of profile dimensions.
    inp_extra_rows: tuple[tuple[float, ...], ...] = ()

    def __post_init__(self):
        rejected = CAE_PROFILE_CLASSES_THE_SOLVER_REJECTS.get(self.cae_class)
        if rejected is not None:
            raise ValueError(f"CAE's {self.cae_class} cannot be used: {rejected}")
        expected = CAE_PROFILE_ARGUMENTS.get(self.cae_class)
        if expected is None:
            raise ValueError(f"No probed argument list for CAE profile class {self.cae_class!r}")
        if tuple(self.cae_kwargs) != expected:
            raise ValueError(
                f"{self.cae_class} takes {expected} in that order, but this spec passes "
                f"{tuple(self.cae_kwargs)}. Abaqus would either raise or silently build a different "
                f"cross-section, so it is the mapping that is wrong, not the check."
            )

    def inp_data_line(self) -> str:
        """The section's data block as it goes under the ``*Beam Section`` keyword.

        One line for every kind but ``ARBITRARY``, which gets one further line per wall segment.
        Continuation lines carry the one leading blank the writer has always used, so a channel's
        block is byte-for-byte what ``write_sections`` emitted before the mapping moved here.
        """
        rows = [self.inp_dims, *self.inp_extra_rows]
        return "\n ".join(", ".join(str(d) for d in row) for row in rows)

    def cae_kwargs_source(self) -> str:
        """``cae_kwargs`` as Python source, in the kernel's own argument order.

        The order comes from the dict, which is built in :data:`CAE_PROFILE_ARGUMENTS` order, so the
        emitted call reads like a CAE journal and is deterministic without sorting -- sorting it
        would in fact scramble it.
        """
        parts = []
        for key, value in self.cae_kwargs.items():
            parts.append(f"{key}={value}" if key in CAE_SYMBOL_ARGUMENTS else f"{key}={value!r}")
        return ", ".join(parts)


def _spec(
    base_type: BaseTypes,
    inp_kind: str,
    dims: tuple,
    cae_class: str,
    cae_kwargs: dict,
    extra_rows: tuple[tuple[float, ...], ...] = (),
) -> ProfileSpec:
    return ProfileSpec(
        base_type=base_type,
        inp_kind=inp_kind,
        inp_dims=tuple(dims),
        cae_class=cae_class,
        cae_kwargs=cae_kwargs,
        inp_extra_rows=tuple(tuple(row) for row in extra_rows),
    )


def _iprofile_spec(sec: Section) -> ProfileSpec:
    if sec.t_fbtn + sec.t_w > min(sec.w_top, sec.w_btn):
        logger.info(f"For {sec.name}: t_fbtn + t_w > min(w_top, w_btn). {log_fin}")
    # Abaqus' I-section: l, h, b1 (bottom flange), b2 (top flange), t1, t2, t3 (web). ``l`` is the
    # distance from the section origin to its bottom, and adapy's profile outlines are centred on
    # the beam axis, so it is h/2.
    dims = (sec.h / 2, sec.h, sec.w_btn, sec.w_top, sec.t_fbtn, sec.t_ftop, sec.t_w)
    return _spec(
        BaseTypes.IPROFILE,
        "I",
        dims,
        "IProfile",
        dict(zip(CAE_PROFILE_ARGUMENTS["IProfile"], dims)),
    )


def _tprofile_spec(sec: Section) -> ProfileSpec:
    """A T, which Abaqus spells as an I-section with no bottom flange.

    There is no ``section=T`` keyword: asked to write a ``TProfile`` out, Abaqus/CAE 2025 itself
    emits ``section=I`` with ``b1`` and ``t1`` zeroed and the flange in the ``b2``/``t2`` slots.
    That is where this encoding comes from -- the kernel's own, not an invention here. adapy's T
    outline puts the flange at the top too, so the two agree about which way up it is.
    """
    dims = (sec.h / 2, sec.h, 0.0, sec.w_top, 0.0, sec.t_ftop, sec.t_w)
    return _spec(
        BaseTypes.TPROFILE,
        "I",
        dims,
        "TProfile",
        dict(b=sec.w_top, h=sec.h, l=sec.h / 2, tf=sec.t_ftop, tw=sec.t_w),
    )


def _box_spec(sec: Section) -> ProfileSpec:
    if sec.t_w * 2 > min(sec.w_top, sec.w_btn):
        raise ValueError("Web thickness cannot be larger than section width")
    dims = (sec.w_top, sec.h, sec.t_w, sec.t_ftop, sec.t_w, sec.t_fbtn)
    a, b, t1, t2, t3, t4 = dims
    # uniformThickness=OFF is what makes Abaqus read t2, t3 and t4 at all.
    return _spec(
        BaseTypes.BOX,
        "BOX",
        dims,
        "BoxProfile",
        dict(a=a, b=b, uniformThickness="OFF", t1=t1, t2=t2, t3=t3, t4=t4),
    )


def _tubular_spec(sec: Section) -> ProfileSpec:
    dims = (sec.r, sec.wt)
    return _spec(BaseTypes.TUBULAR, "PIPE", dims, "PipeProfile", dict(r=sec.r, t=sec.wt))


def _circular_spec(sec: Section) -> ProfileSpec:
    return _spec(BaseTypes.CIRCULAR, "CIRC", (sec.r,), "CircularProfile", dict(r=sec.r))


def _flatbar_spec(sec: Section) -> ProfileSpec:
    dims = (sec.w_btn, sec.h)
    return _spec(BaseTypes.FLATBAR, "RECT", dims, "RectangularProfile", dict(a=sec.w_btn, b=sec.h))


def _angular_spec(sec: Section) -> ProfileSpec:
    dims = (sec.w_btn, sec.h, sec.t_fbtn, sec.t_w)
    return _spec(
        BaseTypes.ANGULAR,
        "L",
        dims,
        "LProfile",
        dict(zip(CAE_PROFILE_ARGUMENTS["LProfile"], dims)),
    )


def channel_midline_rows(sec: Section) -> tuple[tuple[float, ...], ...]:
    """A channel's three wall segments as ``SECTION=ARBITRARY`` rows: bottom flange, web, top flange.

    Thin-walled, by wall *centreline*: the web centreline sits on the local-2 axis (x=0) and each
    flange centreline at half a flange thickness inside the outer face, so the segment endpoints are
    ``w_btn - t_w/2`` and ``+/-(h - t_f)/2`` and every segment carries its own thickness. That is
    Abaqus' own convention for the keyword, and it is what makes the three numbers on each row a
    description of the shape rather than of a bounding box.

    The first row is ``n, x1, y1, x2, y2, t`` -- the segment count and the first segment's two
    endpoints -- and each row after it adds one endpoint and one thickness. The Abaqus reader
    recognises exactly this shape and rebuilds the channel from it
    (``read_sections.channel_from_arbitrary``), so the arithmetic here is half of a round trip and
    cannot be changed on its own.
    """
    tip = sec.w_btn - sec.t_w / 2
    y_btn = -(sec.h - sec.t_fbtn) / 2
    y_top = (sec.h - sec.t_ftop) / 2
    return (
        (3, tip, y_btn, 0.0, y_btn, sec.t_fbtn),
        (0.0, y_top, sec.t_w),
        (tip, y_top, sec.t_ftop),
    )


def _channel_spec(sec: Section) -> ProfileSpec:
    """A channel: ``section=ARBITRARY`` in the INP, a generalized section in CAE.

    The two routes part company here, and neither half of that is a preference. CAE does have a
    ``ChannelProfile``, and it is the only target that keeps the shape, which is what makes it
    tempting. It cannot be used. Abaqus/CAE 2025 builds one happily and writes it out as
    ``section=CHANNEL`` with ``l, h, b1, b2, t1, t2, t3, o`` -- and Abaqus/Standard then **rejects its
    own preprocessor's output**. Measured on a deck CAE exported from a ``ChannelProfile`` built by
    this mapping, run through ``abaqus datacheck``::

        ***ERROR: in keyword *BEAMSECTION, file "chan_job.inp", line 29: Illegal value
                  "CHANNEL" for parameter "section". The value may be misspelled, obsolete,
                  or invalid.
        ***ERROR: ELEMENT 1 INSTANCE CHANPART-1 IS MISSING A BEAM SECTION DEFINITION
        Abaqus Error: Analysis Input File Processor exited with an error

    Two independent confirmations that the shape is a picture and nothing more: the failure is
    identical for ``o=0`` and ``o=0.05``, so no argument of ``ChannelProfile`` avoids it, and CAE's
    own ``part.getMassProperties()`` returns ``mass=None, centerOfMass=(None, None, None)`` for a
    member carrying such a section -- the kernel will not integrate the profile either. So a
    ``ChannelProfile`` yields a model that opens in the GUI and cannot be meshed-and-solved, or handed
    to anyone, or weighed. The CAE route therefore takes a generalized section, which *analyses*, with
    the section properties adapy computed, at the cost of the outline in the viewer. The same
    ``abaqus datacheck``, on the deck CAE exports from the generalized section::

        *beamgeneralsection, elset=..., poisson=0.3, density=7850, material=S355, section=GENERAL
        ANALYSIS DATACHECK COMPLETE WITH 1 WARNING MESSAGES ON THE DAT FILE

    -- zero errors, and the one warning is that density is given on both the material and the
    section, which is how a ``BEFORE_ANALYSIS`` section has to be written.

    The INP route does better, and does not have to give the outline up: ``section=ARBITRARY`` exists,
    it is legal, and a channel is genuinely thin-walled, so the three wall segments of
    :func:`channel_midline_rows` describe it exactly. That is a strictly better deck than a
    generalized section, which keeps only five integrated numbers and loses the outline, the stress
    recovery points and the shear centre with it. So the two routes carry different things, and the
    warning below says which of them loses what rather than letting one pretend otherwise.
    """
    logger.warning(
        f"Section {sec.name}: an Abaqus INP carries this channel as section=ARBITRARY, which traces "
        f"its three walls exactly, but a CAE export carries it as a generalized section -- CAE's "
        f"ChannelProfile writes a section=CHANNEL that Abaqus/Standard's own preprocessor rejects, "
        f"and CAE cannot even compute the mass of a member holding one. So the CAE model has this "
        f"channel's stiffness and mass but not its shape."
    )
    rows = channel_midline_rows(sec)
    general = _general_spec(sec, BaseTypes.CHANNEL)
    return dataclasses.replace(general, inp_kind="ARBITRARY", inp_dims=rows[0], inp_extra_rows=rows[1:])


def _general_spec(sec: Section, base_type: BaseTypes) -> ProfileSpec:
    gp = eval_general_properties(sec)
    dims = (gp.Ax, gp.Iy, gp.Iyz, gp.Iz, gp.Ix)
    return _spec(
        base_type,
        "GENERAL",
        dims,
        "GeneralizedProfile",
        # gammaO (sectorial moment) and gammaW (warping constant) are CAE-only -- section=GENERAL
        # writes five numbers -- and zero is their neutral value.
        dict(area=gp.Ax, i11=gp.Iy, i12=gp.Iyz, i22=gp.Iz, j=gp.Ix, gammaO=0.0, gammaW=0.0),
    )


def _poly_spec(sec: Section) -> ProfileSpec:
    """A filled outline, as computed properties -- *not* as ``ArbitraryProfile``.

    ``ArbitraryProfile`` looks like the obvious home for a polygon and is not: it is a
    **thin-walled** profile, a polyline with a wall thickness per segment. Probed -- a 0.2 x 0.2
    square outline with t=0.01 on a 1 m member has a volume of 0.006 m3 where the same outline as a
    filled ``RectangularProfile(0.2, 0.2)`` has 0.04 m3, and Abaqus writes it as ``section=ARBITRARY``
    with an ``x, y, t`` triple per segment. adapy's POLY is a filled outline with no wall thickness
    to give it, so the faithful target is a generalized section: that loses the shape in the viewer
    and keeps the stiffness, which is the right way round. ``ArbitraryProfile`` would keep a picture
    of the shape and be wrong about every property of it.

    Which leaves the properties, and ``ada.sections.properties.calc_poly`` is a stub: it logs
    "not implemented" and returns **zeros** for all twenty of them. Run through the substitution in
    :func:`eval_general_properties` those zeros become ``Ax=0, Iy=2.0, Iz=2.0, Ix=1.0`` -- a beam
    with no area and the inertia of a 1.2 m solid square, which is not a refusal but a fabrication,
    and it would analyse. So POLY is refused until the properties exist. This raised before this
    mapping was extracted too; the difference is that the reason is now the true one.
    """
    gp = sec.properties
    if gp is None or gp.Ax is None or gp.Ax <= 0.0 or gp.Iy is None or gp.Iy <= 0.0:
        raise NotImplementedError(
            f"Section {sec.name!r} is a POLY (a filled outline) and carries no section properties: "
            f"ada.sections.properties.calc_poly is a stub that returns zeros. Abaqus' only polygonal "
            f"profile, ArbitraryProfile, is thin-walled and cannot represent a filled outline, so the "
            f"target is a generalized section -- which needs real properties. Implement calc_poly and "
            f"this works in both the INP and the CAE writer with no further change."
        )
    return _general_spec(sec, BaseTypes.POLY)


_SPEC_BUILDERS = {
    BaseTypes.IPROFILE: _iprofile_spec,
    BaseTypes.TPROFILE: _tprofile_spec,
    BaseTypes.BOX: _box_spec,
    BaseTypes.TUBULAR: _tubular_spec,
    BaseTypes.CIRCULAR: _circular_spec,
    BaseTypes.FLATBAR: _flatbar_spec,
    BaseTypes.ANGULAR: _angular_spec,
    BaseTypes.CHANNEL: _channel_spec,
    BaseTypes.GENERAL: lambda sec: _general_spec(sec, BaseTypes.GENERAL),
    BaseTypes.POLY: _poly_spec,
}


def profile_spec(section: Section) -> ProfileSpec:
    """Map a :class:`~ada.Section` onto its Abaqus cross-section, for the INP and CAE writers alike.

    Every member of :class:`~ada.sections.categories.BaseTypes` is covered, so there is no fallback
    branch: a new section type must be added here, and is then available to both writers at once.
    """
    builder = _SPEC_BUILDERS.get(section.type)
    if builder is None:
        raise NotImplementedError(
            f'Section type "{section.type}" has no Abaqus cross-section mapping. Add it to '
            f"ada.sections.profiles._SPEC_BUILDERS, which serves both the INP and the CAE writer."
        )
    return builder(section)


def eval_general_properties(section: Section) -> GeneralProperties:
    """The section properties to write on a ``*Beam General Section``, with missing ones filled in.

    Returns a **copy**. ``Section.properties`` caches its result, so writing into it would make an
    Abaqus export permanently change the model: a Sesam or IFC export later in the same process
    would then inherit whatever this function substituted.

    ``None`` is the only marker for "never computed" -- every field of
    :class:`~ada.sections.concept.GeneralProperties` defaults to ``None``. **Zero is a computed
    answer and is kept.** That distinction is the whole point of this function's shape: ``Iyz`` is
    exactly ``0`` for every section symmetric about an axis (box, tubular, I, circular, flatbar,
    channel -- only ``calc_angular`` returns a non-zero one), and treating that zero as missing used
    to substitute ``(Iy + Iz) / 2``. That value is the *largest* a product of inertia may legally
    take, so it then failed the positive-definiteness test below and inflated ``Iy`` as well: a
    UNP200 channel went out with ``Iy`` 4.8x too large and a fabricated ``I12``, and a UNP300 5.6x.
    Both numbers reached the deck behind a log line, which is the worst way for a section to be
    wrong.

    Where a value genuinely is unknown, the substitute is the neutral one -- ``0.0`` for ``Iyz`` --
    not the extreme of its range.
    """
    gp = dataclasses.replace(section.properties)
    name = section.name

    # A real cross-section has none of these at or below zero, so here 0.0 does mean "no data".
    for attr, fallback in (("Ix", 1.0), ("Iy", 2.0), ("Iz", 2.0)):
        value = getattr(gp, attr)
        if value is None or value <= 0.0:
            setattr(gp, attr, fallback)
            logger.warning(f"Section {name} {attr} is {value}. Substituting {fallback}. {log_fin}")

    if gp.Iyz is None:
        gp.Iyz = 0.0
        logger.warning(f"Section {name} has no Iyz. Substituting 0.0, i.e. symmetric. {log_fin}")

    # With a real Iyz this cannot fail for a physically possible section, so a failure means the
    # input is inconsistent. Say so instead of adjusting Iy until the inequality holds -- a section
    # quietly made 10% stiffer is the failure this function used to produce.
    if gp.Iy * gp.Iz - gp.Iyz**2 < 0 or not -(gp.Iy + gp.Iz) / 2 < gp.Iyz <= (gp.Iy + gp.Iz) / 2:
        raise ValueError(
            f"Section {name}: I(11)*I(22) - I(12)**2 must be positive and I(12) must lie within "
            f"+/-(I(11) + I(22))/2, but Iy={gp.Iy}, Iz={gp.Iz}, Iyz={gp.Iyz}. These properties "
            f"describe no real cross-section, so Abaqus would reject the section."
        )
    return gp
