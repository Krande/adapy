"""Shell-only FE models: every beam and plate meshed as conformal shell elements.

Beams go in through their shell geometry (web and flange mid-surfaces) and plates
through their mid-surface. Within one *body* every surface is fragmented against
every other one in a single OCC boolean, so flanges, webs and deck plates that meet
share their intersection edges and the mesh is connected through the joints.
Separate bodies (e.g. a structure and an impactor) are fragmented independently, so
they stay separate meshes that interact only through contact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

from ada.config import logger

from .concepts import GmshOptions, GmshSession

if TYPE_CHECKING:
    from ada import FEM, Beam, Plate


def _fragment_body(gs: GmshSession, objs: Sequence[Beam | Plate]) -> None:
    owners, tags = [], []
    for obj in objs:
        for ent in gs.model_map[obj].entities:
            owners.append(obj)
            tags.append(ent)
    if len(tags) < 2:
        return
    _, out_map = gs.model.occ.fragment(tags[:1], tags[1:])
    gs.model.occ.synchronize()

    # out_map follows the input order (object list, then tool list). A face shared
    # by two inputs (e.g. a deck plate lying on a flange) appears under both owners;
    # it is meshed once and assigned to the first owner only.
    new_entities = {obj: [] for obj in objs}
    claimed = set()
    for obj, children in zip(owners, out_map):
        for child in children:
            if child[0] != 2 or child in claimed:
                continue
            claimed.add(child)
            new_entities[obj].append(child)
    for obj in objs:
        gs.model_map[obj].entities = new_entities[obj]
    logger.info(f"shell body: {len(tags)} surfaces fragmented into {len(claimed)}")


def mesh_shell_bodies(
    bodies: Sequence[Sequence[Beam | Plate]],
    mesh_size: float,
    name: str = "ShellModel",
    use_quads: bool = False,
    options: GmshOptions = None,
) -> FEM:
    """Mesh each body's beams and plates as one conformal shell mesh; return one FEM for all bodies.

    Every element keeps a reference to the beam/plate it came from (``elem.refs``), and each
    object gets its elements in ``obj.elem_refs`` — use those to build sets per body.
    """
    options = GmshOptions() if options is None else options
    with GmshSession(silent=True, options=options) as gs:
        for body in bodies:
            for obj in body:
                gs.add_obj(obj, geom_repr="shell")
        for body in bodies:
            _fragment_body(gs, body)
        gs.mesh(mesh_size, use_quads=use_quads)
        return gs.get_fem(name)
