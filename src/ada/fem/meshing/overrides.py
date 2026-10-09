"""Mesh overrides: per-object meshing rules.

Assign one to an object's ``mesh_override``::

    pl.mesh_override = refine_near_booleans(size=0.02, dist_max=0.3)
    fem = pl.to_fem_obj(0.1, "shell", use_quads=True)

An override is ``override(gs, obj, geom_repr) -> None``. Whichever session meshes the object -- ``to_fem_obj`` for the
object alone, ``Part.to_fem_obj`` for a whole part -- calls it after the objects are added, partitioned against each
other and have their concept points embedded, and before meshing. ``gs.model_map[obj].entities`` are the object's
gmsh ``(dim, tag)`` pairs as partitioning left them. The override sets rules on them:

* sizes: register a size field with ``gs.add_size_field`` (they are combined, so overrides compose), or set point
  sizes with ``gs.model.mesh.setSize``;
* structure: ``setTransfiniteSurface`` / ``setRecombine`` on its own entities, with ``gs.skip_default_meshing(obj)``
  so the default quad/hex treatment leaves them alone;
* element options through ``gs.options`` -- these are session-wide, so in a part they apply to every object.

It does not mesh: the session meshes all objects at once, which is what makes an overridden object conform to its
neighbours (a shared curve gets one mesh, graded to the finer side).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from ada.base.types import GeomRepr

if TYPE_CHECKING:
    from ada.base.physical_objects import BackendGeom

    from .concepts import GmshSession

MeshOverride = Callable[["GmshSession", "BackendGeom", GeomRepr], None]


def boolean_curves(gs: GmshSession, obj: BackendGeom, tol: float = 1e-4) -> list[int]:
    """The gmsh curve tags a cutout of ``obj`` left behind: the curves inside each boolean primitive's bounding box.

    Found from the model rather than from gmsh, so it works on anything with booleans whatever its shape.
    """
    curves: list[int] = []
    for boolean in obj.booleans:
        bbox = boolean.primitive.bbox()
        lo = [min(bbox.p1[i], bbox.p2[i]) - tol for i in range(3)]
        hi = [max(bbox.p1[i], bbox.p2[i]) + tol for i in range(3)]
        curves += [tag for _, tag in gs.model.getEntitiesInBoundingBox(*lo, *hi, dim=1)]
    return sorted(set(curves))


def refine_curves(
    gs: GmshSession, curves: list[int], *, size: float, max_size: float, dist_min: float, dist_max: float
) -> int:
    """Register a size field: ``size`` within ``dist_min`` of ``curves``, growing to ``max_size`` at ``dist_max``.

    Returns the field's tag. Beyond ``dist_max`` the field asks for ``max_size``; the session's maximum element size
    still caps it.
    """
    field = gs.model.mesh.field
    distance = field.add("Distance")
    field.setNumbers(distance, "CurvesList", curves)
    field.setNumber(distance, "Sampling", 100)
    threshold = field.add("Threshold")
    field.setNumber(threshold, "InField", distance)
    field.setNumber(threshold, "SizeMin", size)
    field.setNumber(threshold, "SizeMax", max_size)
    field.setNumber(threshold, "DistMin", dist_min)
    field.setNumber(threshold, "DistMax", dist_max)
    gs.add_size_field(threshold, size)
    return threshold


def refine_near_booleans(
    size: float,
    dist_max: float,
    max_size: float | None = None,
    dist_min: float | None = None,
) -> MeshOverride:
    """An override with elements of ``size`` along every cutout, growing to ``max_size`` at ``dist_max`` from it.

    ``max_size`` defaults to the session's ``Mesh_MeshSizeMax`` (the caller's mesh size), ``dist_min`` to ``size``:
    how far the fine size holds before it starts to grow. An object without booleans is left to the default meshing.
    """
    if size <= 0 or (max_size is not None and max_size < size):
        raise ValueError(f"need 0 < size <= max_size, got size={size}, max_size={max_size}")
    dist_min = size if dist_min is None else dist_min

    def override(gs: GmshSession, obj: BackendGeom, geom_repr: GeomRepr) -> None:
        curves = boolean_curves(gs, obj)
        if not curves:
            return
        top = max_size if max_size is not None else gs.options.Mesh_MeshSizeMax
        refine_curves(gs, curves, size=size, max_size=top, dist_min=dist_min, dist_max=dist_max)

    override.__name__ = f"refine_near_booleans(size={size}, dist_max={dist_max})"
    return override


def element_size(size: float) -> MeshOverride:
    """An override that meshes its object at ``size``: a constant size field on the object's own entities and their
    boundary, so a neighbour sharing an edge with it is graded to match along that edge."""
    if size <= 0:
        raise ValueError(f"need size > 0, got {size}")

    def override(gs: GmshSession, obj: BackendGeom, geom_repr: GeomRepr) -> None:
        entities = gs.model_map[obj].entities
        field = gs.model.mesh.field
        constant = field.add("Constant")
        field.setNumber(constant, "VIn", size)
        lists = {1: "CurvesList", 2: "SurfacesList", 3: "VolumesList"}
        for dim, name in lists.items():
            tags = [tag for d, tag in entities if d == dim]
            if tags:
                field.setNumbers(constant, name, tags)
        field.setNumber(constant, "IncludeBoundary", 1)
        gs.add_size_field(constant, size)

    override.__name__ = f"element_size({size})"
    return override
