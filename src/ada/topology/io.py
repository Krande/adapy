"""Ingest pipelines: build a CellGraph from adapy models / IFC.

These reuse adapy's own importers (``ada.from_ifc``) and CAD backend rather than
re-parsing or re-tessellating geometry. Because the imported geometry is B-rep
(not a triangle soup), the cells' faces are already clean — no coplanar-face
simplification step is needed, unlike a tessellation-based pipeline.

``from_section_loft`` is intentionally omitted here: lofting currently produces
a raw OCC shape (``ada.api.loft``), so it is OCC-only and its section inputs are
domain-specific — a domain layer can loft to solids and call
``CellGraph.from_cell_solids`` directly.
"""
from __future__ import annotations

from ada.topology.graph import CellGraph
from ada.topology.metadata import TopologyMetadata


def from_part(part) -> CellGraph:
    """Build a CellGraph from the ``PrimBox`` objects in an assembly/part."""
    import ada

    return CellGraph.from_prim_boxes(list(part.get_all_physical_objects(by_type=ada.PrimBox)))


def from_assembly(assembly, ifc_types=("IfcSpace", "IfcRoom", "IfcZone")) -> CellGraph:
    """Build a CellGraph from the B-rep solids of an imported assembly.

    Each physical shape becomes a cell solid; abutting cells are merged so they
    share faces. ``ifc_types`` filters by the original IFC entity type (resolved
    via the assembly's ifc_store); pass ``None`` to accept every shape. Each
    object's property sets + IFC type are carried onto its ``TopologyMetadata``.
    """
    import ada

    store = getattr(assembly, "ifc_store", None)
    pairs: list[tuple] = []
    for obj in assembly.get_all_physical_objects(by_type=ada.Shape):
        ifc_type = None
        if store is not None and getattr(obj, "guid", None):
            try:
                ifc_type = store.get_by_guid(obj.guid).is_a()
            except Exception:
                ifc_type = None
        if ifc_types is not None and ifc_type not in ifc_types:
            continue
        try:
            solid = obj.solid_occ()
        except Exception:
            # No usable geometry on this shape (e.g. geometry import disabled).
            continue
        if solid is None:
            continue
        props: dict = {}
        for pset in (obj.metadata or {}).values():
            if isinstance(pset, dict):
                props.update(pset)
        if ifc_type:
            props["IFC_type"] = ifc_type
        pairs.append((solid, TopologyMetadata(name=obj.name, properties=props)))

    if not pairs:
        raise ValueError(
            "from_assembly: no matching shape solids found "
            "(check ifc_types, or that geometry was imported)"
        )
    return CellGraph.from_cell_solids(pairs, merge=True)


def from_ifc(ifc_file, ifc_types=("IfcSpace", "IfcRoom", "IfcZone")) -> CellGraph:
    """Build a CellGraph from an IFC file via adapy's own importer.

    No ifcopenshell re-parse or re-tessellation: ``ada.from_ifc`` yields B-rep
    shapes whose solids are the cells. By default only space-like entities are
    used (``ifc_types``); pass ``None`` to use every shape.
    """
    import ada
    from ada.config import Config

    # adapy only attaches solid geometry to imported shapes when this is on
    # (read at IFC-read time), so obj.solid_occ() works downstream.
    Config().ifc_import_shape_geom = True
    assembly = ada.from_ifc(ifc_file)
    return from_assembly(assembly, ifc_types=ifc_types)
