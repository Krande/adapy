import pathlib

# requires mamba install python-dotenv
from dotenv import load_dotenv

import ada
from ada.config import logger
from ada.fem.exceptions import FEASolverNotInstalled
from ada.fem.meshing import GmshOptions

load_dotenv()
SCRATCH = pathlib.Path("temp")


def make_pl_fem(geom_repr) -> ada.Assembly:
    pl = ada.Plate("MyPlate", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, n=(0, 0, 1), xdir=(1, 0, 0), mat="S420")
    assembly = ada.Assembly("MyAssembly") / [ada.Part("MyPart") / pl]
    part = pl.parent
    part.fem = pl.to_fem_obj(0.1, geom_repr, options=GmshOptions(Mesh_ElementOrder=1), use_quads=True, use_hex=True)
    nodes = [part.fem.nodes.get_by_volume(p.p, single_member=True) for p in pl.nodes]
    assembly.fem.add_bc(ada.fem.Bc("Fixed", ada.fem.FemSet("bc_nodes", nodes), [1, 2, 3, 4, 5, 6]))
    assembly.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=10))
    return assembly


def run(fem_format):
    a = make_pl_fem("shell")
    res = a.to_fem(f"plate_{fem_format}_EIG_sh", fem_format, overwrite=True, execute=True, scratch_dir="temp")
    res.show(new_glb_file="temp/plate_fem.glb", server_args=["--debug"], update_only=True, warp_scale=1)
    res.to_vtu(f"temp/plate_{fem_format}_fem.vtu")


if __name__ == "__main__":
    run("code_aster")
    for fea in ["calculix", "code_aster", "sesam", "abaqus"]:
        try:
            run(fea)
        except FEASolverNotInstalled as e:
            logger.warning(e)
            continue
