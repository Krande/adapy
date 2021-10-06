from typing import List

from ada import FEM

from .concepts import GmshSession, GmshTask


def multisession_gmsh_tasker(gmsh_tasks: List[GmshTask]):
    fem = FEM("AdaFEM")
    for gtask in gmsh_tasks:
        with GmshSession(silent=True) as gs:
            gs.options = gtask.options
            for obj in gtask.ada_obj:
                gs.add_obj(obj, gtask.geom_repr)
            gs.mesh(gtask.mesh_size)
            # TODO: Add operand type += for FEM
            tmp_fem = gs.get_fem()
            fem += tmp_fem
    return fem
