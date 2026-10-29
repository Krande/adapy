"""Writing CSG primitives as swept solids: an export choice, off by default.

``IfcRightCircularCylinder`` and ``IfcRightCircularCone`` are valid IFC, but some geometry engines
(ifcopenshell's among them) cannot convert them, and a reader built on one drops the solid -- or a
hole cut by one. With ``metadata[SWEPT_PRIMITIVES_KEY]`` true on the assembly, a cylinder is written
as a circle profile extruded along its axis and a cone as a right triangle revolved about it: the
same solids, in a form every reader converts."""

from __future__ import annotations

SWEPT_PRIMITIVES_KEY = "ifc_swept_primitives"


def swept_primitives_wanted(shape) -> bool:
    try:
        return bool(shape.get_assembly().metadata.get(SWEPT_PRIMITIVES_KEY))
    except Exception:  # noqa: BLE001 - a shape outside an assembly is written as it always was
        return False
