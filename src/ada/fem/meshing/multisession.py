from typing import List

from ada import FEM
from ada.core.utils import Counter

from .concepts import GmshSession, GmshTask


def multisession_gmsh_tasker(fem: FEM, gmsh_tasks: List[GmshTask]):
    """Run multiple meshing operations within a single GmshSession."""

    model_names = Counter(1, "gmsh")
    with GmshSession(silent=True) as gs:
        for gtask in gmsh_tasks:
            gs.model.add(next(model_names))
            gs.options = gtask.options
            for obj in gtask.ada_obj:
                gs.add_obj(obj, gtask.geom_repr)
            gs.mesh(gtask.mesh_size)

            # TODO: Add operand type += for FEM
            tmp_fem = gs.get_fem()
            tmp_fem.parent = fem.parent
            fem += tmp_fem
            gs.model_map = dict()
    return fem
