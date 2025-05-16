from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Iterable

from ada.base.units import Units
from ada.config import logger

if TYPE_CHECKING:
    from ada import Part, Beam, Plate, Node

_MM = 1_000.0  # m -> mm


def _coord_e_n_u(pt: Node) -> str:
    """convert coordinate (x,y,z) in [m] -> 'E ...mm N ...mm U ...mm'"""
    x, y, z = (c * _MM if pt.units == Units.M else c for c in pt)
    return f"E {x:.0f}mm N {y:.0f}mm U {z:.0f}mm"


def _gtype_from_section(sec_key: str) -> str:
    """
    Extract the leading letters before the first digit. For example,
    "HEA220" --> "HEA", "IPE300" --> "IPE", "SHS150x10" --> "SHS"
    """
    return re.match(r"[A-Za-z]+", sec_key).group(0).upper()


def _plate_vertices(plate: Plate) -> tuple[list[str], float]:
    """
    Return (list_of_E-N-U_strings, thickness_mm) for the given plate.
    """
    # vertices
    verts: list[Node] = plate.nodes

    # enforce CCW order in E-N plane so SJUS UTOP works the same everywhere
    verts_ccw = list(verts) if _area_2d_z(verts) >= 0 else list(reversed(verts))

    enu = [_coord_e_n_u(v) for v in verts_ccw]

    # thickness (mm) – pick the first attribute that exists
    t_mm = plate.t * _MM if plate.units == Units.M else plate.t

    return enu, float(t_mm)


def _area_2d_z(nodes: list[Node]) -> float:
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
    # Beams
    for beam in getattr(node, "beams", []):
        yield beam
    # Plates
    for plate in getattr(node, "plates", []):
        yield plate


@dataclass
class E3DWriter:
    spec_map: dict[str, str]
    panel_spec_map: dict[str, str]
    material_map: dict[str, str]
    panel_material_map: dict[str, str]

    e3d_level_by_depth: ClassVar[dict[int, str]] = {1: "SITE", 2: "ZONE", 3: "STRU", 4: "FRMW", 5: "SBFR", 6: "PART"}

    # ----- geometry -----------------------------------------------------------------
    def create_beam_pml(self, beam: Beam) -> str:
        sec_key = beam.section.name  # "HEA220"
        gtype = _gtype_from_section(sec_key)  # "HEA"

        mat_key = getattr(beam.material, "name", str(beam.material))  # --> "S355"
        matref = self.material_map.get(mat_key, mat_key)

        spref = self.spec_map.get(sec_key, sec_key)

        start_pos = _coord_e_n_u(beam.n1)  # --> "E 79925mm N 289143mm U 518352mm"
        end_pos = _coord_e_n_u(beam.n2)  # --> "E 80418mm N 289143mm U 518352mm"

        # PML code to create beam
        lines = [
            "new GENSEC",
            f"GTYPE {gtype}",
            "JUSLINE CTOP",
            f"MATREF {matref}",
            f"SPREF {spref}",
            f"POSITION {start_pos}",
            "new SPINE",
            "new POINSP",
            f"  POSITION {start_pos}",
            "new POINSP",
            f"  POSITION {end_pos}",
            "\n",
        ]
        return "\n".join(lines)

    def create_plate_pml(self, plate: Plate) -> str:
        # Note that PANEL can be created under STRU/FRMW/SBFR level
        mat_key = getattr(plate.material, "name", str(plate.material))  # --> "S355"
        matref = self.panel_material_map.get(mat_key, mat_key)  # e.g. "/VLE36_hca_va"

        sec_key = getattr(plate, "spec", plate.name)  # "PL10", "PL12", ...
        spref = self.panel_spec_map.get(sec_key, sec_key)  # e.g. "/HCA_VA/VLE36PL030"

        verts_enu, panel_thick = _plate_vertices(plate)

        panel_pos = verts_enu[0]  # first vertex = insertion point

        # PML code to create panel
        lines = [
            "new PANEL",
            f"POSITION {panel_pos}",
            f"--ORI Y is W and Z is N",
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

        hierarchy_str = (
            f"--* === {level}: {part.name} === *--\n"
            f"if ({part.name}.exists()) then\n"
            f"   return\n"
            f"else\n"
            f"   new {level} {level_name}\n"
            f"endif\n\n"
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
                logger.warning("Unsupported object %s", type(obj))

    def write_macro(self, part: Part) -> str:
        """
        Write a macro file for the assembly.
        """
        out: list[str] = [f"--* Macro generated from Assembly {part.name} *--\n"]
        self._emit_part(part, out)
        return "\n".join(out)
