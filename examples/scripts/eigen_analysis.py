import ada
from ada.fem.meshing import GmshOptions
from ada.materials.metals import CarbonSteel


def make_fem(geom_repr) -> ada.Assembly:
    bm = ada.Beam("MyBeam", (0, 0.5, 0.5), (5, 0.5, 0.5), "IPE400", ada.Material("S420", CarbonSteel("S420")))
    assembly = ada.Assembly("MyAssembly") / [ada.Part("MyPart") / bm]
    part = bm.parent
    part.fem = bm.to_fem_obj(0.1, geom_repr, options=GmshOptions(Mesh_ElementOrder=1))
    nodes = bm.bbox().sides.back(return_fem_nodes=True)
    assembly.fem.add_bc(ada.fem.Bc("Fixed", ada.fem.FemSet("bc_nodes", nodes), [1, 2, 3, 4, 5, 6]))
    assembly.fem.add_step(ada.fem.StepEigen("Eigen", num_eigen_modes=10))
    return assembly


def run_ccx():
    a = make_fem("shell")
    res = a.to_fem("Cantilever_CCX_EIG_sh", "calculix", overwrite=True, execute=True)
    res.to_trimesh(1, "DISP")
    for x in res.iter_results_by_field_value():
        print(x)


def run_code_aster():
    a = make_fem("shell")
    res = a.to_fem("Cantilever_CA_EIG_sh", "code_aster", overwrite=True, execute=True)
    for x in res.iter_results_by_field_value():
        print(x)


if __name__ == "__main__":
    # run_ccx()
    run_code_aster()
