from __future__ import annotations

import re
from dataclasses import dataclass
from math import atan2, degrees, isclose, sqrt
from typing import TYPE_CHECKING, Callable, ClassVar, Iterable

import numpy as np

from ada.api.beams.helpers import Justification, get_justification
from ada.base.units import Units
from ada.config import logger
from ada.sections.categories import BaseTypes

if TYPE_CHECKING:
    from ada import Beam, Node, Part, Plate, Point

_MM = 1_000.0  # m -> mm


def vector_to_orientation(v: np.ndarray, tol: float = 1e-6) -> str:
    """
    Turn a 3D vector v (in global coords) into an AVEVA E3D orientation string.
     - Horizontal bearing from East=0°, anticlockwise through N, W, S.
     - Tilt above/below horizontal → U/D.
    """
    x, y, z = v
    h = sqrt(x * x + y * y)

    # pure vertical
    if h < tol:
        return "U" if z > 0 else "D"

    # bearing from East=0°, anticlockwise
    bearing = degrees(atan2(y, x)) % 360

    # map cardinals to angles
    cardinal_angles = {
        "E": 0,
        "N": 90,
        "W": 180,
        "S": 270,
    }

    def horiz_str(b: float) -> str:
        # exact cardinal?
        for dir_, ang in cardinal_angles.items():
            if isclose(b, ang, abs_tol=tol):
                return dir_
        # inter-cardinal: find adjacent cardinals
        sorted_cards = sorted(cardinal_angles.items(), key=lambda kv: kv[1])
        sorted_cards.append(("E", 360))
        for (d1, a1), (d2, a2) in zip(sorted_cards, sorted_cards[1:]):
            if a1 < b < a2:
                delta = b - a1
                return f"{d1}{delta:.0f}{d2}"
        return ""  # fallback

    # horizontal only?
    if abs(z) < tol:
        return horiz_str(bearing)

    # tilted
    tilt = degrees(atan2(abs(z), h))
    vert = "U" if z > 0 else "D"
    return f"{horiz_str(bearing)}{tilt:.0f}{vert}"


def matrix_to_orientation_map(matrix: np.ndarray) -> dict[str, str]:
    """
    Given a 4×4 transform, use its top-left 3×3:
     rows = directions of local X, Y, Z in global coords.
    """
    if matrix.shape != (4, 4):
        raise ValueError("Expected a 4×4 matrix")
    R = matrix[:3, :3]
    # now use rows, not columns
    axes = {
        "X": R[0, :],
        "Y": R[1, :],
        "Z": R[2, :],
    }
    return {ax: vector_to_orientation(vec) for ax, vec in axes.items()}


def matrix_to_orientation_str(mat4: np.ndarray) -> str:
    """
    Returns: "Y is <ori> and Z is <ori>"
    """

    omap = matrix_to_orientation_map(mat4)

    return f"Y is {omap['Y']} and Z is {omap['Z']}"


def _coord_e_n_u(pt: Node | Point, units: Units = Units.M) -> str:
    """convert coordinate (x,y,z) in [m] -> 'E ...mm N ...mm U ...mm'"""
    if len(pt) == 3:
        x, y, z = (c * _MM if units == Units.M else c for c in pt)
    elif len(pt) == 2:
        z = 0.0
        x, y = (c * _MM if units == Units.M else c for c in pt)
    else:
        raise ValueError(f"Invalid point {pt} with {len(pt)} coordinates")
    return f"E {x:.0f}mm N {y:.0f}mm U {z:.0f}mm"


def _gtype_from_section(sec_key: str) -> str:
    """
    Extract the leading letters before the first digit. For example,
    "HEA220" --> "HEA", "IPE300" --> "IPE", "SHS150x10" --> "SHS"
    """
    return re.match(r"[A-Za-z]+", sec_key).group(0).upper()


def _plate_vertices(plate: Plate) -> list[str]:
    """
    Return (list_of_E-N-U_strings, thickness_mm) for the given plate.
    """
    # vertices
    verts: list[Point] = plate.poly.points2d

    # enforce CCW order in E-N plane so SJUS UTOP works the same everywhere
    verts_ccw = list(verts) if _area_2d_z(verts) >= 0 else list(reversed(verts))

    return [_coord_e_n_u(v, plate.units) for v in verts_ccw]


def _area_2d_z(nodes: list[Node | Point]) -> float:
    """Signed area of the polygon projected to the EN-plane."""
    a = 0.0
    for i, v in enumerate(nodes):
        x1, y1 = v[0], v[1]
        x2, y2 = nodes[(i + 1) % len(nodes)][0], nodes[(i + 1) % len(nodes)][1]
        a += x1 * y2 - x2 * y1
    return a / 2


def walk_hierarchy(part: Part) -> Iterable[Part]:
    yield part
    for child in getattr(part, "parts", {}).values():
        yield from walk_hierarchy(child)


def _geometry_children(node: Part):
    """Yield Beam/Plate objects that are **direct** children of *node*."""
    for beam in getattr(node, "beams", []):
        yield beam
    for plate in getattr(node, "plates", []):
        yield plate


@dataclass
class E3DWriter:
    beam_spec_map: dict[str, str] | Callable[[Beam], str]
    panel_spec_map: dict[str, str] | Callable[[Plate], str]
    beam_material_map: dict[str, str] | Callable[[Beam], str]
    panel_material_map: dict[str, str] | Callable[[Plate], str]

    e3d_level_by_depth: ClassVar[dict[int, str]] = {1: "SITE", 2: "ZONE", 3: "STRU", 4: "FRMW", 5: "SBFR", 6: "PART"}

    def _get_beam_spec(self, beam: Beam) -> str:
        """Get the beam specification from the map or use the key itself."""
        if isinstance(self.beam_spec_map, dict):
            sec_key = beam.section.name
            return self.beam_spec_map.get(sec_key, sec_key)
        elif callable(self.beam_spec_map):
            return self.beam_spec_map(beam)
        else:
            raise ValueError("beam_spec_map must be a dict or callable")

    def _get_panel_spec(self, plate: Plate) -> str:
        """Get the panel specification from the map or use the key itself."""
        if isinstance(self.panel_spec_map, dict):
            pl_thick_mm = int(plate.t * _MM) if plate.units == Units.M else int(plate.t)
            sec_key = f"PL{pl_thick_mm:02d}"
            return self.panel_spec_map.get(sec_key, sec_key)
        elif callable(self.panel_spec_map):
            return self.panel_spec_map(plate)
        else:
            raise ValueError("panel_spec_map must be a dict or callable")

    def _get_beam_material(self, beam: Beam) -> str:
        """Get the beam material from the map or use the key itself."""
        if isinstance(self.beam_material_map, dict):
            mat_key = beam.material.name
            return self.beam_material_map.get(mat_key, mat_key)
        elif callable(self.beam_material_map):
            return self.beam_material_map(beam)
        else:
            raise ValueError("beam_material_map must be a dict or callable")

    def _get_panel_material(self, plate: Plate) -> str:
        """Get the panel material from the map or use the key itself."""
        if isinstance(self.panel_material_map, dict):
            mat_key = plate.material.name
            return self.panel_material_map.get(mat_key, mat_key)
        elif callable(self.panel_material_map):
            return self.panel_material_map(plate)
        else:
            raise ValueError("panel_material_map must be a dict or callable")

    def create_beam_pml(self, beam: Beam) -> str:
        from ada import Point

        sec_key = beam.section.name  # "HEA220"
        gtype = _gtype_from_section(sec_key)  # "HEA"

        matref = self._get_beam_material(beam)
        spref = self._get_beam_spec(beam)

        origin = _coord_e_n_u(beam.n1.p, beam.units)
        p1 = Point(0, 0, 0)
        p2 = beam.n2.p - beam.n1.p
        start_pos = _coord_e_n_u(p1, beam.n1.units)
        end_pos = _coord_e_n_u(p2, beam.n1.units)
        vec_up_str = vector_to_orientation(beam.up)

        bangle_str = ""
        just = get_justification(beam)
        if just == Justification.TOS:
            jus_str = "CTOP"
        elif just == Justification.NA:
            jus_str = "NA"
        else:
            logger.info(f"Unknown Justification: {just}")
            jus_str = "NA"

        if beam.section.type == BaseTypes.ANGULAR:
            jus_str = "CBOTTOM"  # angular sections are always bottom justified
            bangle_str = "bangle 180"
        # PML code to create beam
        lines = [
            f"new GENSEC /{beam.name}",
            f"GTYPE {gtype}",
            f"JUSLINE {jus_str}",
            f"MATREF {matref}",
            f"SPREF {spref}",
            f"POSITION {origin}",
            bangle_str,
            "new SPINE",
            f"ydir {vec_up_str}",
            "new POINSP",
            f"  POSITION {start_pos}",
            "new POINSP",
            f"  POSITION {end_pos}",
            "\n",
        ]
        return "\n".join(lines)

    def create_plate_pml(self, plate: Plate) -> str:
        # Note that PANEL can be created under STRU/FRMW/SBFR level
        matref = self._get_panel_material(plate)
        spref = self._get_panel_spec(plate)
        pl_ori_str = matrix_to_orientation_str(plate.poly.orientation.get_matrix4x4())
        verts_enu = _plate_vertices(plate)

        panel_thick = plate.t * _MM if plate.units == Units.M else plate.t
        panel_pos = _coord_e_n_u(plate.poly.origin, plate.units)

        # PML code to create panel
        lines = [
            f"new PANEL /{plate.name}",
            f"POSITION {panel_pos}",
            f"ORI {pl_ori_str}",
            f"SPREF {spref}",
            f"MATREF {matref}",
            "new PLOOP",
            f"HEIG {panel_thick:.0f}mm",
            "SJUS UTOP",
        ]

        for i, v in enumerate(verts_enu, start=1):
            lines += [
                "NEW PAVERT",
                f"  POS {v}",
                "END",
                "",
            ]

        return "\n".join(lines)

    # ----- hierarchy ----------------------------------------------------------------
    def create_hierarchy(self, part: Part) -> str:
        """
        Create a hierarchy string for the assembly.
        Build SITE -> ZONE -> STRU -> FRMW/SBFR -> PART nesting.
        """
        level = self._e3d_level(part)

        level_name = part.name
        if not level_name.startswith("/"):
            level_name = "/" + level_name

        within_check = ""
        if level != "SITE":
            within_check = f" FOR /{part.parent.name}"

        hierarchy_str = (
            f"--* === {level}: {part.name} === *--\n"
            f"var !count COLLECT ALL {level.upper()} WITH ( NAME EQ '{level_name}' ){within_check}\n"
            "if (!count.Size() EQ 1) then\n"
            f"   $P {level.lower()} [{level_name}] found\n"
            f"   !{level.lower()} = !count[1]\n"
            f"else\n"
            f"   $P {level.lower()} [{level_name}] NOT found\n"
            f"   new {level.lower()} {level_name}\n"
            f"   !{level.lower()} = {level_name}\n"
            f"endif\n\n"
            f"$!{level.lower()}\n\n"
        )
        return hierarchy_str

    def _e3d_level(self, obj: Part) -> str:
        """Return E3D hierarchy keyword based on ancestor depth (Assembly = SITE)."""
        depth = len(obj.get_ancestors())  # 0,1,2,...
        return self.e3d_level_by_depth.get(depth, "PART")

    # ----- Write Macro --------------------------------------------------------------

    def _emit_part(self, part: Part, out: list[str]) -> None:
        """Depth-first: write hierarchy, then recurse, then geometry."""
        from ada import Beam, Plate

        out.append(self.create_hierarchy(part))

        # first all child parts (this gives SITE -> ... -> SBFR in order)
        for child in getattr(part, "parts", {}).values():
            self._emit_part(child, out)

        # then geometry that belongs to *this* part
        for obj in _geometry_children(part):
            if isinstance(obj, Beam):
                out.append(self.create_beam_pml(obj))
            elif isinstance(obj, Plate):
                out.append(self.create_plate_pml(obj))
            else:
                logger.warning(f"Unsupported object {type(obj)}")

    def write_macro(self, part: Part) -> str:
        """
        Write a macro file for the assembly.
        """
        out: list[str] = [f"--* Macro generated from Assembly {part.name} *--\n"]
        self._emit_part(part, out)
        return "\n".join(out)
