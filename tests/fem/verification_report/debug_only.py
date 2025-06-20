from fem.verification_report import build_report_utils as ru
from fem.verification_report.build_verification_report import build_fea_report

import ada
from ada.config import logger
from ada.fem.formats.abaqus.config import AbaqusSetup
from ada.fem.meshing import GmshOptions

from ..conftest import beam
from ..test_fem_eig_cantilever import test_fem_eig


def main(overwrite=True, execute=True, test_gmsh_options=False, build_report=False, show=False):
    if ru.ODB_DUMP_EXE is not None:
        AbaqusSetup.set_default_post_processor(ru.post_processing_abaqus)

    fea_format = "code_aster"
    geom_repr = "shell"
    elem_order = 1
    use_hex_quad = False
    reduced_integration = False
    eigen_modes = 10
    gmsh_options = GmshOptions(Mesh_ElementOrder=elem_order, Mesh_Algorithm=6, Mesh_Algorithm3D=10)
    bm = beam()
    a = ada.Assembly("MyAssembly") / [bm]

    result = test_fem_eig(
        beam_fixture=bm,
        fem_format=fea_format,
        geom_repr=geom_repr,
        elem_order=elem_order,
        use_hex_quad=use_hex_quad,
        reduced_integration=reduced_integration,
        short_name_map=None,
        overwrite=overwrite,
        execute=execute,
        eigen_modes=eigen_modes,
        name="debug",
        debug=True,
        interactive=False,
        silent=False,
        options=gmsh_options if test_gmsh_options else None,
        perform_quality_check=False,
    )
    if show:
        a.show()
    # result.show()

    if build_report:
        metadata = dict()
        metadata["geo"] = geom_repr
        metadata["elo"] = elem_order
        metadata["hexquad"] = use_hex_quad
        metadata["reduced_integration"] = reduced_integration

        if result is None:
            logger.error("No result file is located")
            return

        fvr = ru.postprocess_result(result, metadata)

        build_fea_report(bm, [fvr], eigen_modes)


if __name__ == "__main__":
    logger.setLevel("INFO")
    main(overwrite=True, execute=True, test_gmsh_options=False, build_report=False)
