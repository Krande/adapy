import sys
from pathlib import Path

# Run as a flat script from within this dir. ``conftest.beam`` and
# ``test_fem_eig`` still live under ``tests/fem/`` — they're test
# fixtures we reuse for the debug pipeline, so we bootstrap that path
# onto ``sys.path`` rather than packaging this script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "tests" / "fem"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_report_utils as ru  # noqa: E402
from build_verification_report import build_fea_report  # noqa: E402

import ada  # noqa: E402
from ada.config import logger  # noqa: E402
from ada.fem.formats.abaqus.config import AbaqusSetup  # noqa: E402
from ada.fem.meshing import GmshOptions  # noqa: E402

from conftest import beam  # noqa: E402
from test_fem_eig_cantilever import test_fem_eig  # noqa: E402


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
