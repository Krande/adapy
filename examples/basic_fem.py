import argparse
from typing import Literal

import ada
from ada.base.types import GeomRepr
from ada.comms.fb.fb_base_gen import FilePurposeDC
from ada.config import logger
from ada.fem.meshing import GmshOptions
from ada.materials.metals import CarbonSteel
from ada.visit.renderer_manager import RenderParams

logger.setLevel("INFO")


def _make_cantilever_part(geom_repr: GeomRepr, element_order: int, use_quads: bool, use_hex: bool) -> ada.Part:
    options = GmshOptions(Mesh_ElementOrder=element_order)
    bm = ada.Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (5, 0.5, 0.5),
        "IPE400",
        ada.Material("S420", CarbonSteel("S420")),
    )
    part = ada.Part("MyPart") / bm
    part.fem = part.to_fem_obj(
        0.1,
        geom_repr,
        options=options,
        use_quads=use_quads,
        use_hex=use_hex,
    )
    nodes = bm.bbox().sides.back(return_fem_nodes=True)
    part.fem.add_bc(ada.fem.Bc("Fixed", ada.fem.FemSet("bc_nodes", nodes), [1, 2, 3]))
    return part

def _make_cube_part(geom_repr: GeomRepr, element_order: int, use_quads: bool, use_hex: bool) -> ada.Part:
    options = GmshOptions(Mesh_ElementOrder=element_order)
    cube = ada.PrimBox('box', (0,0,0), (1,1,1), material=ada.Material("S420", CarbonSteel("S420")))
    part = ada.Part("MyPart") / cube
    part.fem = part.to_fem_obj(
        1.5,
        geom_repr,
        options=options,
        use_quads=use_quads,
        use_hex=use_hex,
    )
    nodes = cube.bbox().sides.back(return_fem_nodes=True)
    part.fem.add_bc(ada.fem.Bc("Fixed", ada.fem.FemSet("bc_nodes", nodes), [1, 2, 3]))
    return part

def make_fem_part(geom_repr: GeomRepr, element_order: int, use_quads: bool, use_hex: bool, model: Literal["cantilever", "cube"]) -> ada.Part:
    if model == "cube":
        part = _make_cube_part(geom_repr, element_order, use_quads, use_hex)
    elif model == "cantilever":
        part = _make_cantilever_part(geom_repr, element_order, use_quads, use_hex)
    else:
        raise ValueError(f"Unknown model: {model}")

    return part

def make_assembly(part: ada.Part, simulation: Literal["eigen", "static"]) -> ada.Assembly:
    assembly = ada.Assembly("MyAssembly") / part
    if simulation == "static":
        step = assembly.fem.add_step(ada.fem.StepImplicitStatic("Static", nl_geom=True, init_incr=100.0, total_time=100.0))
        step.add_load(ada.fem.LoadGravity("Gravity", -9.81*800))
    elif simulation == "eigen":
        assembly.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=10))
    else:
        raise ValueError(f"Unknown simulation type: {simulation}")
    return assembly

def main():
    parser = argparse.ArgumentParser(description="Run FEM analysis with Calculix (ccx) or Code Aster (ca)")
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
    parser.add_argument(
        "--model",
        choices=["cantilever", "cube"],
        default="cantilever",
        help="Model to use: 'cantilever' or 'cube'",
    )
    parser.add_argument(
        "--embed-glb",
        action="store_true",
        help="Embed GLB file in the output",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files",
    )
    parser.add_argument(
        "--simulation",
        choices=["eigen", "static"],
        default="eigen",
        help="Overwrite existing files",
    )
    args = parser.parse_args()

    geom_repr = GeomRepr.from_str(args.geom)
    if geom_repr == GeomRepr.LINE and args.model == "cube":
        logger.warning("Cube model cannot use LINE geometry representation. Using SOLID instead.")
        geom_repr = GeomRepr.SOLID

    part = make_fem_part(geom_repr, args.order, args.quad, args.hex, args.model)
    assembly = make_assembly(part, args.simulation)

    if geom_repr == geom_repr.SHELL:
        eltypname = "QUAD" if args.quad else "TRI"
    elif geom_repr == geom_repr.SOLID:
        eltypname = "HEX" if args.hex else "TETRA"
    else:
        eltypname = "LINE"

    case_name = f"{args.model}_{args.simulation}_{args.solver.upper()}_{geom_repr.value}_{eltypname}_o{args.order}"
    solver_engine = "calculix" if args.solver == "ccx" else "code_aster"
    res = assembly.to_fem(
        case_name,
        solver_engine,
        overwrite=args.overwrite,
        execute=True,
    )
    params = RenderParams(purpose=FilePurposeDC.ANALYSIS)
    res.show(params_override=params, force_embed_glb=args.embed_glb)


if __name__ == "__main__":
    main()
