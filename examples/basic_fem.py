import argparse
import pathlib

import ada
from ada.comms.fb.fb_base_gen import FilePurposeDC
from ada.config import logger
from ada.fem.meshing import GmshOptions
from ada.materials.metals import CarbonSteel
from ada.visit.renderer_manager import RenderParams

logger.setLevel("INFO")


def make_fem(
    geom_repr: str,
    element_order: int,
    use_quads: bool,
    use_hex: bool
) -> ada.Assembly:
    bm = ada.Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (5, 0.5, 0.5),
        "IPE400",
        ada.Material("S420", CarbonSteel("S420")),
    )
    assembly = ada.Assembly("MyAssembly") / [ada.Part("MyPart") / bm]
    part = bm.parent
    options = GmshOptions(Mesh_ElementOrder=element_order)
    part.fem = bm.to_fem_obj(
        0.1,
        geom_repr,
        options=options,
        use_quads=use_quads,
        use_hex=use_hex,
    )
    nodes = bm.bbox().sides.back(return_fem_nodes=True)
    assembly.fem.add_bc(
        ada.fem.Bc("Fixed", ada.fem.FemSet("bc_nodes", nodes), [1, 2, 3])
    )
    assembly.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=10))
    return assembly


def main():
    parser = argparse.ArgumentParser(
        description="Run FEM analysis with Calculix (ccx) or Code Aster (ca)"
    )
    parser.add_argument(
        "--solver",
        choices=["ccx", "ca"],
        default="ca",
        help="Solver to use: 'ccx' for Calculix, 'ca' for Code Aster",
    )
    parser.add_argument(
        "--geom",
        choices=["shell", "solid", "line"],
        default="shell",
        help="Geometry representation",
    )
    parser.add_argument(
        "--order",
        type=int,
        choices=[1, 2],
        default=1,
        help="Element order (1 or 2)",
    )
    parser.add_argument(
        "--quad",
        action="store_true",
        help="Use quadrilateral (2D) elements",
    )
    parser.add_argument(
        "--hex",
        action="store_true",
        help="Use hexahedral (3D) elements",
    )

    args = parser.parse_args()

    fem = make_fem(args.geom, args.order, args.quad, args.hex)

    case_name = f"Cantilever_{args.solver.upper()}_{args.geom}_o{args.order}"
    solver_engine = "calculix" if args.solver == "ccx" else "code_aster"

    res = fem.to_fem(
        case_name,
        solver_engine,
        overwrite=True,
        execute=True,
    )
    # ensure temp directory for the glTF export
    out_dir = pathlib.Path("temp")
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path = out_dir / "cantilever.glb"
    params = RenderParams(gltf_export_to_file=str(glb_path),purpose=FilePurposeDC.ANALYSIS)
    res.show(params_override=params)


if __name__ == "__main__":
    main()
