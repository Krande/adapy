from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from ada.base.physical_objects import BackendGeom
from ada.base.types import BaseEnum
from ada.core.vector_utils import unit_vector

if TYPE_CHECKING:
    from ada import Beam, Node, Plate, PrimExtrude, PrimSweep
    from ada.geom.curves import CurveOpen3d


class Bolts(BackendGeom):
    """

    TODO: Create a bolt class based on the IfcMechanicalFastener concept.

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcmechanicalfastener.htm

    Which in turn should likely be inside another element components class

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcelementcomponent.htm

    """

    def __init__(self, name, p1, p2, normal, members, parent=None):
        super(Bolts, self).__init__(name, parent=parent)


class WeldType(BaseEnum):
    """Weld type catalog mirroring the 27-value set from upstream weld libraries.

    Names are stripped of the ``WELD_TYPE_`` prefix; values match the
    names. `from_str` accepts both stripped and prefixed forms case-
    insensitively.
    """

    NONE = "NONE"
    EDGE_FLANGE = "EDGE_FLANGE"
    SQUARE_GROOVE_SQUARE_BUTT = "SQUARE_GROOVE_SQUARE_BUTT"
    BEVEL_GROOVE_SINGLE_V_BUTT = "BEVEL_GROOVE_SINGLE_V_BUTT"
    BEVEL_GROOVE_SINGLE_BEVEL_BUTT = "BEVEL_GROOVE_SINGLE_BEVEL_BUTT"
    SINGLE_V_BUTT_WITH_BROAD_ROOT_FACE = "SINGLE_V_BUTT_WITH_BROAD_ROOT_FACE"
    SINGLE_BEVEL_BUTT_WITH_BROAD_ROOT_FACE = "SINGLE_BEVEL_BUTT_WITH_BROAD_ROOT_FACE"
    U_GROOVE_SINGLE_U_BUTT = "U_GROOVE_SINGLE_U_BUTT"
    J_GROOVE_J_BUTT = "J_GROOVE_J_BUTT"
    BEVEL_BACKING = "BEVEL_BACKING"
    FILLET = "FILLET"
    PLUG = "PLUG"
    SPOT = "SPOT"
    SEAM = "SEAM"
    SLOT = "SLOT"
    FLARE_BEVEL_GROOVE = "FLARE_BEVEL_GROOVE"
    FLARE_V_GROOVE = "FLARE_V_GROOVE"
    CORNER_FLANGE = "CORNER_FLANGE"
    PARTIAL_PENETRATION_SINGLE_BEVEL_BUTT_PLUS_FILLET = "PARTIAL_PENETRATION_SINGLE_BEVEL_BUTT_PLUS_FILLET"
    PARTIAL_PENETRATION_SQUARE_GROOVE_PLUS_FILLET = "PARTIAL_PENETRATION_SQUARE_GROOVE_PLUS_FILLET"
    MELT_THROUGH = "MELT_THROUGH"
    STEEP_FLANKED_BEVEL_GROOVE_SINGLE_V_BUTT = "STEEP_FLANKED_BEVEL_GROOVE_SINGLE_V_BUTT"
    STEEP_FLANKED_BEVEL_GROOVE_SINGLE_BEVEL_BUTT = "STEEP_FLANKED_BEVEL_GROOVE_SINGLE_BEVEL_BUTT"
    EDGE = "EDGE"
    ISO_SURFACING = "ISO_SURFACING"
    FOLD = "FOLD"
    INCLINED = "INCLINED"

    @classmethod
    def from_str(cls, value: str) -> WeldType:
        if isinstance(value, cls):
            return value
        key = value.upper().removeprefix("WELD_TYPE_")
        # Legacy short aliases for backwards compatibility with older callers.
        # "V" used to be the only enum value before the 27-value catalog
        # landed; map it to its closest equivalent.
        legacy = {"V": "BEVEL_GROOVE_SINGLE_V_BUTT"}
        key = legacy.get(key, key)
        for member in cls:
            if member.value == key:
                return member
        raise ValueError(
            f"Unknown weld type {value!r}; expected one of: {sorted(m.value for m in cls)}"
        )


# Back-compat alias for the previous, much smaller enum.
WeldProfileEnum = WeldType


@dataclass(frozen=True)
class IntermittentSpec:
    """Intermittent weld pattern: weld for `length_on`, skip `length_off`, repeat with `pitch` centre-to-centre."""

    pitch: float
    length_on: float
    length_off: float | None = None


def build_profile(
    weld_type: WeldType | str,
    throat: float,
    *,
    leg1: float | None = None,
    leg2: float | None = None,
    groove_angle: float | None = None,
    root_gap: float | None = None,
    root_face: float | None = None,
) -> list[tuple[float, float]]:
    """Derive a 2D weld cross-section profile from parametric inputs.

    Only fillet is implemented today. Other weld types raise
    NotImplementedError — callers must pass `profile=` explicitly to
    `Weld(...)` until the other shapes land.
    """
    if isinstance(weld_type, str):
        weld_type = WeldType.from_str(weld_type)

    if weld_type is WeldType.FILLET:
        l1 = leg1 if leg1 is not None else throat
        l2 = leg2 if leg2 is not None else throat
        return [(0, 0), (-l1, 0), (0, l2)]

    raise NotImplementedError(
        f"build_profile not implemented for weld_type={weld_type.name}; "
        "pass profile= explicitly to Weld(...)"
    )


class Weld(BackendGeom):
    """First-class weld object.

    Geometric placement is always required: ``p1``/``p2`` (linear extrude)
    or ``sweep_curve`` (curved sweep). ``xdir`` is also required — it
    orients the profile cross-section in 3D, which member geometry alone
    cannot disambiguate (a fillet between the same members has two valid
    fill sides).

    The profile is either supplied explicitly (``profile=``) or derived
    from parametric inputs (``weld_type + throat`` and optionally
    ``leg1/2/groove_angle/root_gap/root_face``) via ``build_profile``.
    """

    def __init__(
        self,
        name,
        p1=None,
        p2=None,
        weld_type: WeldType | str = WeldType.FILLET,
        members=(),
        profile: list[tuple] | None = None,
        xdir: tuple | None = None,
        groove: list[tuple] | None = None,
        parent=None,
        *,
        throat: float | None = None,
        leg1: float | None = None,
        leg2: float | None = None,
        groove_angle: float | None = None,
        root_gap: float | None = None,
        root_face: float | None = None,
        sided: Literal["one", "two"] = "one",
        intermittent: IntermittentSpec | None = None,
        sweep_curve: CurveOpen3d | Any | None = None,
        profile_normal: tuple | None = None,
        profile_ydir: tuple | None = None,
    ):
        super().__init__(name, parent=parent)
        from ada import Node, PrimExtrude, PrimSweep

        if xdir is None:
            raise ValueError("Weld requires `xdir` to orient the profile cross-section")

        if isinstance(weld_type, str):
            weld_type = WeldType.from_str(weld_type)

        if profile is None:
            if throat is None:
                raise ValueError(
                    "Weld profile is missing — supply either `profile=` explicitly or `throat=` "
                    "(and optionally leg1/leg2/...) to derive it from weld_type"
                )
            profile = build_profile(
                weld_type,
                throat,
                leg1=leg1,
                leg2=leg2,
                groove_angle=groove_angle,
                root_gap=root_gap,
                root_face=root_face,
            )

        if sweep_curve is not None:
            sweep_kwargs: dict[str, Any] = {
                "sweep_curve": sweep_curve,
                "profile_curve_outer": profile,
                "profile_xdir": xdir,
            }
            if profile_normal is not None:
                sweep_kwargs["profile_normal"] = profile_normal
            if profile_ydir is not None:
                sweep_kwargs["profile_ydir"] = profile_ydir
            geom = PrimSweep(f"{self.name}_geom", **sweep_kwargs)
            geom.parent = self
            p1_node = None
            p2_node = None
        else:
            if p1 is None or p2 is None:
                raise ValueError("Weld requires either `sweep_curve=` or both `p1` and `p2`")
            p1_node = Node(p1) if not isinstance(p1, Node) else p1
            p2_node = Node(p2) if not isinstance(p2, Node) else p2
            geom = PrimExtrude.from_2points_and_curve(
                f"{self.name}_geom", p1_node.p, p2_node.p, profile, xdir
            )
            geom.parent = self

        if groove is not None and p1_node is not None and p2_node is not None:
            vec = unit_vector(p2_node.p - p1_node.p)
            p_start = p1_node.p - p1_node.p * vec * 0.02
            p_end = p2_node.p + p2_node.p * vec * 0.02
            groove = PrimExtrude.from_2points_and_curve(
                f"{self.name}_groove", p_start, p_end, groove, xdir
            )
            groove.parent = self
        elif groove is not None:
            raise NotImplementedError("Groove geometry on swept welds not implemented yet")

        members_tuple = tuple(members)
        for m in members_tuple:
            if not isinstance(m, BackendGeom):
                raise TypeError(
                    f"Weld members must be BackendGeom subclasses (Beam, Plate, ...); got {type(m).__name__}"
                )

        self._xdir = xdir
        self._geom = geom
        self._groove = groove
        self._p1 = p1_node
        self._p2 = p2_node
        self._members = members_tuple
        self._weld_type = weld_type
        self._throat = throat
        self._leg1 = leg1
        self._leg2 = leg2
        self._groove_angle = groove_angle
        self._root_gap = root_gap
        self._root_face = root_face
        self._sided = sided
        self._intermittent = intermittent
        self._sweep_curve = sweep_curve

    @property
    def type(self) -> WeldType:
        return self._weld_type

    @property
    def p1(self) -> Node | None:
        return self._p1

    @property
    def p2(self) -> Node | None:
        return self._p2

    @property
    def members(self) -> tuple[BackendGeom, ...]:
        return self._members

    def other_members(self, of: BackendGeom) -> list[BackendGeom]:
        return [m for m in self._members if m is not of]

    @property
    def geometry(self) -> PrimExtrude | PrimSweep:
        return self._geom

    def solid_geom(self):
        # Delegate to the wrapped PrimSweep/PrimExtrude so the
        # tessellator's BackendGeom path produces the weld bead's
        # mesh. Without this, Weld inherits BackendGeom's
        # NotImplementedError stub and the GLB ships no weld
        # geometry — only the metadata pinned via _weld_metadata.
        return self._geom.solid_geom()

    @property
    def groove(self) -> PrimExtrude | None:
        return self._groove

    @property
    def throat(self) -> float | None:
        return self._throat

    @property
    def leg1(self) -> float | None:
        return self._leg1

    @property
    def leg2(self) -> float | None:
        return self._leg2

    @property
    def sided(self) -> str:
        return self._sided

    @property
    def intermittent(self) -> IntermittentSpec | None:
        return self._intermittent

    @property
    def sweep_curve(self):
        return self._sweep_curve
