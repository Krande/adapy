"""Base spatial-topology entities: spaces, openings and equipment.

These are plain pydantic value objects describing axis-aligned regions and the
features placed on/in them. They carry geometry (origin + extents), an optional
side-exclude set, a priority for overlap resolution, and helpers to derive the
corner points of a box side. They hold no CAD-kernel dependency.

``TopoSpace`` doubles as cell metadata for :mod:`ada.topology.graph`: it exposes
the same ``name``/``get`` duck-typed surface as
:class:`~ada.topology.metadata.TopologyMetadata`, so a cell can carry either.

The shared base keeps an opaque ``parent_config`` back-reference (any object that
provides ``get_space(name, structure_name)``); domain layers attach their own
config there. Nothing here imports that config type, so the entities stay
domain-neutral.
"""

from __future__ import annotations

from functools import cached_property
from typing import Annotated, Any, ClassVar, Literal, Optional, get_args

from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator

import ada
from ada.api.spatial.eq_types import EquipRepr
from ada.config import logger
from ada.sections.categories import BaseTypes

__all__ = [
    "EquipRepr",
    "TopoSpace",
    "TopoOpening",
    "TopoEquipment",
    "beam_section_description_with_examples",
    "from_ada_obj",
    "from_ada_meta",
]


def beam_section_description_with_examples(desc: str, example_map: dict[BaseTypes, str]) -> str:
    examples = "\n".join(f"{t.name}: {ex}" for t, ex in example_map.items())
    return f"{desc}\nValid syntax examples:\n{examples}"


class _TopoConfigBoundModel(BaseModel):
    """Base for topology value objects bound to an (opaque) parent config.

    ``parent_config`` is duck-typed: any object exposing ``get_space(name,
    structure_name)`` (and, for the config subclasses that live in the domain
    layer, the richer lookups). Typed ``Any`` here so this base names no
    domain config type.
    """

    _PARENT_CONFIG: Optional[Any] = PrivateAttr(default=None)
    HIDE_IN_EXCEL: ClassVar[list[str]] = []
    ORIENTATION: ClassVar[str] = "HORIZONTAL"

    @property
    def parent_config(self) -> Optional[Any]:
        return self._PARENT_CONFIG

    @parent_config.setter
    def parent_config(self, value: Any) -> None:
        self._PARENT_CONFIG = value

    @classmethod
    def get_function_options(cls) -> list[str]:
        """
        If the subclass defines a field named "FUNCTION" whose annotation is
        Literal[...], return the list of allowed string values.
        Otherwise, return an empty list.
        """
        # Pydantic v2 stores fields in `model_fields`
        if "FUNCTION" not in cls.model_fields:
            return []

        literal_type = cls.model_fields["FUNCTION"].annotation
        return list(get_args(literal_type))


class TopoSpace(_TopoConfigBoundModel):
    SHEET_NAME: ClassVar[str] = "Spaces"
    TAB_COLOR: ClassVar[str] = "92D050"  # HEX string without '#'
    HIDE_IN_EXCEL: ClassVar[list[str]] = ["IS_COMPLEX_SHAPE", "SE"]

    STRUCTURE_NAME: Annotated[str | None, Field(description="Name of Structure the space belongs to")] = None
    INCLUDE: Annotated[bool | None, Field(description="Should Include space in structure")] = False
    NAME: Annotated[str, Field(description="Name of space")]
    FUNCTION: Annotated[Literal["space", "cantilevered_deck"], Field(description="Function of the Topology")] = "space"
    AREA: Annotated[str | None, Field(description="Name of area in which the space is located in")] = "NoArea"
    X: Annotated[float, Field(description="X-coordinate of origin")] = None
    Y: Annotated[float, Field(description="Y-coordinate of origin")] = None
    Z: Annotated[float, Field(description="Z-coordinate of origin")] = None
    DX: Annotated[float, Field(description="Length of spaces in X direction")] = None
    DY: Annotated[float, Field(description="Length of spaces in Y direction")] = None
    DZ: Annotated[float, Field(description="Length of spaces in Z direction")] = None
    FLIP_FLOOR: Annotated[bool, Field(description="Flip floor")] = False
    GRID_X_CREATE: Annotated[bool, Field(description="Use in building X grid")] = True
    GRID_Y_CREATE: Annotated[bool, Field(description="Use in building Y grid")] = True
    GRID_Z_CREATE: Annotated[bool, Field(description="Use in building Z grid")] = True
    SWITCH_BM_DIR_VERTICAL: Annotated[bool, Field(description="Switch the vertical beam direction")] = False
    SWITCH_BM_DIR_HORIZONTAL: Annotated[bool, Field(description="Switch the horizontal beam direction")] = False

    SE: Annotated[
        list[int] | None,
        Field(
            description="List of 1–6 integers (0–5), from a comma-separated string that defines which side of the space to exclude"
        ),
    ] = None

    SE0: Annotated[bool | None, Field(description="Exclude side 0 (BOTTOM -Z) of the space")] = None
    SE1: Annotated[bool | None, Field(description="Exclude side 1 (TOP +Z) of the space")] = None
    SE2: Annotated[bool | None, Field(description="Exclude side 2 (FRONT -Y) of the space")] = None
    SE3: Annotated[bool | None, Field(description="Exclude side 3 (BACK +Y) of the space")] = None
    SE4: Annotated[bool | None, Field(description="Exclude side 4 (LEFT -X) of the space")] = None
    SE5: Annotated[bool | None, Field(description="Exclude side 5 (RIGHT +X) of the space")] = None

    GIRDER_AS_PLATE: Annotated[Literal["x", "y", "z", "false"] | None, Field(description="Girder as Plate")] = None
    DESCRIPTION: Annotated[str | None, Field(description="Description of the space")] = None
    PRIORITY: Annotated[
        int | None, Field(description="Priority of the space in case it is overlapping with another space")
    ] = 0
    IS_COMPLEX_SHAPE: Annotated[bool, Field(description="The shape is not a box, but a complex shape")] = False

    # --- cell-metadata duck-typing (parity with TopologyMetadata) ----------
    @property
    def name(self) -> str:
        return self.NAME

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        return key in type(self).model_fields

    @staticmethod
    def _comma_separated_validator(v):
        if v is None:
            return v

        # Handle comma-separated string
        if isinstance(v, str):
            v = [s.strip() for s in v.split(",") if s.strip()]
            try:
                v = [int(x) for x in v]
            except ValueError:
                raise ValueError("All values must be integers.")

        if not isinstance(v, list):
            raise ValueError("Value must be a list or comma-separated string.")

        if not (1 <= len(v) <= 6):
            raise ValueError("Must contain 1 to 6 integers.")

        if any(not (0 <= x <= 5) for x in v):
            raise ValueError("Each integer must be between 0 and 5.")

        if len(set(v)) != len(v):
            raise ValueError("Integers must be unique.")

        return v

    @field_validator("SE", mode="before")
    def parse_and_validate_exclude(cls, v):
        return cls._comma_separated_validator(v)

    def get_exclude_indices(self) -> list[int]:
        """Returns the list of indices to skip."""
        skip_indices = []
        if self.SE is not None:
            skip_indices.extend(self.SE)

        for index in [0, 1, 2, 3, 4, 5]:
            if getattr(self, "SE{}".format(index), None) is True:
                skip_indices.append(index)

        return skip_indices

    def get_p1(self) -> ada.Point:
        """Returns the start point of the space."""
        return ada.Point(self.X, self.Y, self.Z)

    def get_p2(self) -> ada.Point:
        """Returns the end point of the space."""
        return ada.Point(self.X + self.DX, self.Y + self.DY, self.Z + self.DZ)

    @cached_property
    def is_cantilevered_deck(self) -> bool:
        """Check if the space represents a cantilevered deck."""
        if self.FUNCTION == "cantilevered_deck":
            return True

        # automatic set True if based on SE (side exclude) options
        # include_idx = self.get_include_indices()
        exclude_idx = set(self.get_exclude_indices())
        all_indices = {0, 1, 2, 3, 4, 5}
        all_minus_excluded = all_indices - exclude_idx

        # if len(include_idx) == 1 and (include_idx[0] == 1 or include_idx[0] == 0):
        #    return True

        if len(all_minus_excluded) == 1:
            remain = all_minus_excluded.pop()
            if remain == 1 or remain == 0:
                return True

        return False

    def get_side_points(
        self,
        side: Annotated[
            Literal["-X", "X", "-Y", "Y", "-Z", "Z"], Field(description="Space side in the global coordinate system")
        ],
    ) -> list[ada.Point]:
        """Return the 4 corner points of the given side of the box."""

        p1 = self.get_p1()
        p2 = self.get_p2()

        x1, y1, z1 = p1.x, p1.y, p1.z
        x2, y2, z2 = p2.x, p2.y, p2.z

        if side == "-X":
            return [ada.Point(x1, y1, z1), ada.Point(x1, y2, z1), ada.Point(x1, y2, z2), ada.Point(x1, y1, z2)]
        elif side == "X":
            return [ada.Point(x2, y1, z1), ada.Point(x2, y2, z1), ada.Point(x2, y2, z2), ada.Point(x2, y1, z2)]
        elif side == "-Y":
            return [ada.Point(x1, y1, z1), ada.Point(x2, y1, z1), ada.Point(x2, y1, z2), ada.Point(x1, y1, z2)]
        elif side == "Y":
            return [ada.Point(x1, y2, z1), ada.Point(x2, y2, z1), ada.Point(x2, y2, z2), ada.Point(x1, y2, z2)]
        elif side == "-Z":
            return [ada.Point(x1, y1, z1), ada.Point(x2, y1, z1), ada.Point(x2, y2, z1), ada.Point(x1, y2, z1)]
        elif side == "Z":
            return [ada.Point(x1, y1, z2), ada.Point(x2, y1, z2), ada.Point(x2, y2, z2), ada.Point(x1, y2, z2)]
        else:
            raise ValueError(f"Invalid side: {side}")


class TopoEquipment(_TopoConfigBoundModel):
    SHEET_NAME: ClassVar[str] = "Equipments"
    TAB_COLOR: ClassVar[str] = "836e8f"  # HEX string without '#'
    HIDE_IN_EXCEL: ClassVar[list[str]] = ["DESCRIPTION"]

    STRUCTURE_NAME: Annotated[str | None, Field(description="Name of Structure")] = None

    CONDITION_NAMES: Annotated[
        list[str] | None, Field(description="Condition names (multiple comma separated names allowed)")
    ] = None

    INCLUDE: Annotated[bool, Field(description="Include Equipment in build")] = False
    NAME: Annotated[str, Field(description="Name of equipment")]
    SPACE_NAME: Annotated[str, Field(description="Name of space the equipment is located in")]
    SPACE_LOC: Annotated[Literal["FLOOR", "ROOF"], Field(description="Location of the equipment in the space")]
    X: Annotated[float | None, Field(description="X-coordinate of origin")] = None
    Y: Annotated[float | None, Field(description="Y-coordinate of origin")] = None
    Z: Annotated[float | None, Field(description="Z-coordinate of origin")] = None
    LX: Annotated[float | None, Field(description="Length of equipment in X direction")] = None
    LY: Annotated[float | None, Field(description="Length of equipment in Y direction")] = None
    LZ: Annotated[float | None, Field(description="Length of equipment in Z direction")] = None
    GLOBAL_COORDS: Annotated[bool | None, Field(description="Use global coordinate system")] = False
    COGx: Annotated[float, Field(description="X-coordinate of COG offset from equipment X-centroid")]
    COGy: Annotated[float, Field(description="Y-coordinate of COG offset from equipment Y-centroid")]
    COGz: Annotated[float, Field(description="Z-coordinate of COG offset from equipment base")]
    massDry: Annotated[float, Field(description="Mass of equipment (dry weight)")]
    massCont: Annotated[float, Field(description="Mass of equipment (content weight)")]
    REINFORCEMENT_SECTION: Annotated[
        str | None,
        Field(
            description=beam_section_description_with_examples(
                "Reinforcement Section", BaseTypes.get_valid_example_map()
            )
        ),
    ] = None

    FUNCTION: Literal["equipment"] = Field("equipment", frozen=True)
    EQ_DUMMY_SEC: Annotated[
        str | None,
        Field(description=beam_section_description_with_examples("Dummy Section", BaseTypes.get_valid_example_map())),
    ] = "SHS200x10"

    DESCRIPTION: Annotated[str | None, Field(description="Description of the equipment")] = None

    EQ_REPR: Annotated[
        EquipRepr | None,
        Field(
            description="Equipment representation: AS_IS, LINE_LOAD, BEAM_MASS, ECCENTRIC_MASS, FOOTPRINT_MASS, VERTICAL_BEAM_MASS"
        ),
    ] = EquipRepr.AS_IS

    LOADCASE_NAME: Annotated[str | None, Field(description="Loadcase to place Equipment")] = ""

    _origin: ada.Point = PrivateAttr(default=None)
    _geom_resolved: bool = PrivateAttr(default=False)

    @field_validator("CONDITION_NAMES", mode="before")
    @classmethod
    def parse_condition_names(cls, v):
        if v is None:
            return None

        if isinstance(v, str):
            items = [s.strip() for s in v.split(",") if s.strip()]
            return items or None

        if isinstance(v, list):
            return v or None

        raise TypeError("CONDITION_NAMES must be str, list[str], or None")

    def _resolve_geometry_from_space_if_needed(self) -> None:
        """
        If any of X,Y,Z,LX,LY,LZ is None, populate ALL of them from the parent space (except LZ which is set to COGz*2).
        Runs once (lazy) so it works even if parent_config is attached after model creation.
        """
        if self._geom_resolved:
            return

        fields = ("X", "Y", "Z", "LX", "LY", "LZ")
        if not any(getattr(self, f) is None for f in fields):
            self._geom_resolved = True
            return

        space = self.get_space()
        if space is None:
            raise ValueError(
                f"Equipment '{self.NAME}' references SPACE_NAME='{self.SPACE_NAME}', "
                f"STRUCTURE_NAME='{self.STRUCTURE_NAME}', but no matching space was found."
            )

        # Lengths map from space.D* -> equipment.L*
        self.LX = space.DX
        self.LY = space.DY

        # Normal case: COGz defines height
        if self.COGz > 0:
            self.LZ = self.COGz * 2
        else:
            self.LZ = 0.001
            logger.warning(f"Topo Equipment {self.NAME} specified with COGz=0, to avoid crash, value set to 0.001")

        # Coordinates:
        # - If equipment uses local coords (GLOBAL_COORDS=False), origin is space.get_p1() so offsets should be 0.
        # - If equipment uses global coords, origin is (0,0,0) so use the space's absolute position.
        if self.GLOBAL_COORDS:
            self.X = space.X
            self.Y = space.Y
            self.Z = space.Z
        else:
            self.X = 0.0
            self.Y = 0.0
            self.Z = 0.0

        self._geom_resolved = True

    def get_space(self) -> TopoSpace | None:
        """Returns the space where the equipment is located."""
        return self.parent_config.get_space(self.SPACE_NAME, self.STRUCTURE_NAME)

    def get_origin(self) -> ada.Point:
        """Returns the origin point of the equipment."""
        # Ensure we can safely compute origin/roof offsets and later geometry.
        # (Even though origin itself doesn't require X/Y/Z/L*, it DOES require space when GLOBAL_COORDS=False.)
        if self._origin is None:
            space = self.get_space()

            if self.GLOBAL_COORDS is False:
                if space is None:
                    raise ValueError("Space not found for equipment origin (GLOBAL_COORDS=False).")
                origin = space.get_p1()
            else:
                origin = ada.Point(0, 0, 0)

            if self.SPACE_LOC == "ROOF":
                if space is None:
                    raise ValueError("Space not found for ROOF placement.")
                origin += ada.Point(0, 0, space.DZ)

            self._origin = origin

        return self._origin

    def get_p1(self) -> ada.Point:
        """Returns the start point of the equipment."""
        self._resolve_geometry_from_space_if_needed()
        return self.get_origin() + ada.Point(self.X, self.Y, self.Z)

    def get_p2(self) -> ada.Point:
        """Returns the end point of the equipment."""
        self._resolve_geometry_from_space_if_needed()
        return self.get_origin() + ada.Point(self.X + self.LX, self.Y + self.LY, self.Z + self.LZ)

    def get_footing(self) -> tuple[ada.Point, ada.Point, ada.Point, ada.Point]:
        self._resolve_geometry_from_space_if_needed()
        fp1 = self.get_p1()
        fp2 = fp1 + ada.Point(self.LX, 0, 0)
        fp3 = fp1 + ada.Point(self.LX, self.LY, 0)
        fp4 = fp1 + ada.Point(0, self.LY, 0)
        return fp1, fp2, fp3, fp4

    def get_cog(self):
        self._resolve_geometry_from_space_if_needed()
        return self.get_p1() + ada.Point(self.LX / 2, self.LY / 2, 0) + ada.Point(self.COGx, self.COGy, self.COGz)


class TopoOpening(_TopoConfigBoundModel):
    SHEET_NAME: ClassVar[str] = "Openings"
    TAB_COLOR: ClassVar[str] = "836e8f"  # HEX string without '#'

    STRUCTURE_NAME: Annotated[str | None, Field(description="Name of Structure")] = None
    INCLUDE: Annotated[bool, Field(description="Include Opening in build")] = False
    NAME: Annotated[str, Field(description="Name of opening")]
    FUNCTION: Annotated[Literal["opening", "door"], Field(description="Function of the opening")] = "opening"
    SPACE_NAME: Annotated[str, Field(description="Name of space the opening is located in")] = None
    SPACE_SIDE: Annotated[
        Literal["-X", "X", "-Y", "Y", "-Z", "Z"], Field(description="Space side in the global coordinate system")
    ] = None
    POS_X: Annotated[float, Field(description="X-Position in local XY plane. Space side is the origin")] = None
    POS_Y: Annotated[float, Field(description="Y-Position in local XY plane. Space side is the origin")] = None
    SIZE_X: Annotated[float, Field(description="Size in x-direction in local XY plane")] = None
    SIZE_Y: Annotated[float, Field(description="Size in y-direction in local XY plane")] = None
    DEPTH: Annotated[float, Field(description="Depth of opening")] = None
    DESCRIPTION: Annotated[str | None, Field(description="Description of the opening")] = None
    REINFORCEMENT_SECTION: Annotated[
        str | None,
        Field(
            description=beam_section_description_with_examples(
                "Reinforcement Section", BaseTypes.get_valid_example_map()
            )
        ),
    ] = None

    USE_GLOBAL_COORDS: Annotated[bool, Field(description="Use global coordinates")] = False
    X: Annotated[float, Field(description="X-coordinate of origin (if using USE_GLOBAL_COORDS=True)")] = None
    Y: Annotated[float, Field(description="Y-coordinate of origin (if using USE_GLOBAL_COORDS=True)")] = None
    Z: Annotated[float, Field(description="Z-coordinate of origin (if using USE_GLOBAL_COORDS=True)")] = None
    DX: Annotated[float, Field(description="Length of opening in X direction (if using USE_GLOBAL_COORDS=True)")] = None
    DY: Annotated[float, Field(description="Length of opening in Y direction (if using USE_GLOBAL_COORDS=True)")] = None
    DZ: Annotated[float, Field(description="Length of opening in Z direction (if using USE_GLOBAL_COORDS=True)")] = None

    _p1: ada.Point = PrivateAttr(default=None)
    _p2: ada.Point = PrivateAttr(default=None)

    def get_space(self) -> TopoSpace | None:
        return self.parent_config.get_space(self.SPACE_NAME, self.STRUCTURE_NAME)

    def _calculate_p1_p2_from_local(self) -> tuple[ada.Point, ada.Point]:
        space = self.get_space()
        if self.SPACE_SIDE == "Z":
            p1 = ada.Point(self.POS_X, self.POS_Y, -self.DEPTH / 2 + space.DZ)
            p2 = ada.Point(self.POS_X + self.SIZE_X, self.POS_Y + self.SIZE_Y, self.DEPTH / 2 + space.DZ)
        elif self.SPACE_SIDE == "-Z":
            p1 = ada.Point(self.POS_X, self.POS_Y, -self.DEPTH / 2)
            p2 = ada.Point(self.POS_X + self.SIZE_X, self.POS_Y + self.SIZE_Y, self.DEPTH / 2)
        elif self.SPACE_SIDE == "-Y":
            p1 = ada.Point(self.POS_X, -self.DEPTH / 2, self.POS_Y)
            p2 = ada.Point(self.POS_X + self.SIZE_X, self.DEPTH / 2, self.POS_Y + self.SIZE_Y)
        elif self.SPACE_SIDE == "Y":
            p1 = ada.Point(self.POS_X, -self.DEPTH / 2 + space.DY, self.POS_Y)
            p2 = ada.Point(self.POS_X + self.SIZE_X, self.DEPTH / 2 + space.DY, self.POS_Y + self.SIZE_Y)
        elif self.SPACE_SIDE == "-X":
            p1 = ada.Point(-self.DEPTH / 2, self.POS_X, self.POS_Y)
            p2 = ada.Point(self.DEPTH / 2, self.POS_X + self.SIZE_X, self.POS_Y + self.SIZE_Y)
        elif self.SPACE_SIDE == "X":
            p1 = ada.Point(-self.DEPTH / 2 + space.DX, self.POS_X, self.POS_Y)
            p2 = ada.Point(self.DEPTH / 2 + space.DX, self.POS_X + self.SIZE_X, self.POS_Y + self.SIZE_Y)
        else:
            raise NotImplementedError(f"SPACE side {self.SPACE_SIDE} is not implemented")

        if space is None:
            space_origin = ada.Point(0, 0, 0)
        else:
            space_origin = space.get_p1()

        p1_global = space_origin + p1
        p2_global = space_origin + p2

        return p1_global, p2_global

    def get_p1(self) -> ada.Point:
        if self._p1 is not None:
            return self._p1

        if self.USE_GLOBAL_COORDS:
            self._p1 = ada.Point(self.X, self.Y, self.Z)
        else:
            self._p1, _ = self._calculate_p1_p2_from_local()

        return self._p1

    def get_p2(self) -> ada.Point:
        if self._p2 is not None:
            return self._p2

        if self.USE_GLOBAL_COORDS:
            self._p2 = ada.Point(self.X + self.DX, self.Y + self.DY, self.Z + self.DZ)
        else:
            _, self._p2 = self._calculate_p1_p2_from_local()

        return self._p2


def from_ada_obj(obj: ada.PrimBox) -> TopoSpace | TopoOpening | TopoEquipment:
    topo_obj = obj.metadata.get("PM_TOPO_OBJ", None)
    if topo_obj is not None:
        if isinstance(topo_obj, (TopoSpace, TopoOpening, TopoEquipment)):
            return topo_obj
        else:
            logger.warning(f"Unknown topo object type: {topo_obj}")

    function = obj.metadata["FUNCTION"]
    origin = obj.placement.get_absolute_placement().origin
    p1 = origin + obj.p1.copy()
    p2 = origin + obj.p2.copy()
    size = p2 - p1

    space_options = TopoSpace.get_function_options()
    opening_options = TopoOpening.get_function_options()
    equipment_options = TopoEquipment.get_function_options()

    if function in space_options:
        extra_props = {}
        for key, value in obj.metadata.items():
            if key in ("FUNCTION", "NAME", "AREA"):  # This is a special case
                continue
            if key in TopoSpace.__annotations__:
                extra_props[key] = value

        return TopoSpace(
            NAME=obj.metadata.get("NAME", obj.name),
            FUNCTION=obj.metadata["FUNCTION"],
            AREA=obj.metadata.get("AREA", obj.parent.name),
            X=p1.x,
            Y=p1.y,
            Z=p1.z,
            DX=size.x,
            DY=size.y,
            DZ=size.z,
            **extra_props,
        )
    elif function in opening_options:
        extra_props = {}
        for key, value in obj.metadata.items():
            if key in ("FUNCTION", "NAME"):  # This is a special case
                continue
            if key in TopoOpening.__annotations__:
                extra_props[key] = value

        return TopoOpening(
            NAME=obj.metadata.get("NAME", obj.name),
            FUNCTION=obj.metadata["FUNCTION"],
            USE_GLOBAL_COORDS=True,
            X=p1.x,
            Y=p1.y,
            Z=p1.z,
            DX=size.x,
            DY=size.y,
            DZ=size.z,
            **extra_props,
        )
    elif function in equipment_options:
        extra_props = {}
        for key, value in obj.metadata.items():
            if key in ("FUNCTION", "NAME"):
                continue

            if key in TopoEquipment.__annotations__:
                extra_props[key] = value
        return TopoEquipment(
            NAME=obj.metadata.get("NAME", obj.name),
            FUNCTION=obj.metadata["FUNCTION"],
            X=p1.x,
            Y=p1.y,
            Z=p1.z,
            LX=size.x,
            LY=size.y,
            LZ=size.z,
            **extra_props,
        )
    else:
        raise ValueError(f"Unknown function: {function}")


_func_map: dict[str, TopoSpace | TopoOpening | TopoEquipment] = {}
for base_model in [TopoSpace, TopoOpening, TopoEquipment]:
    options = base_model.get_function_options()
    for option in options:
        _func_map[option] = base_model


def from_ada_meta(metadata: dict):
    """Imports spaces from ada metadata."""
    func = metadata.get("FUNCTION").lower()
    parent_config = metadata.get("_PARENT_CONFIG")

    if func not in _func_map:
        raise ValueError(f"Unknown function: {func}")

    func_obj = _func_map.get(func)
    opts = {}
    for key in func_obj.__annotations__:
        if key.startswith("__"):
            continue
        value = metadata.get(key)
        if value is not None:
            opts[key] = value

    try:
        output_meta_obj = func_obj(**opts)
    except ValidationError as exc:
        errors = exc.errors()
        raise ValueError(errors)

    if parent_config is not None:
        output_meta_obj.parent_config = parent_config

    return output_meta_obj
