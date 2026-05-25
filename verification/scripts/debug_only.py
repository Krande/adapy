import sys
from pathlib import Path

# Flat script under verification/scripts/. Bootstrap the parent
# verification/ dir onto sys.path so `build_report_utils` /
# `build_verification_report` resolve as siblings of one level up.
_VERIFICATION_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VERIFICATION_DIR))

import build_report_utils as ru  # noqa: E402
from build_verification_report import build_fea_report  # noqa: E402

import ada  # noqa: E402
from ada.api.fem_tasks import (  # noqa: E402
    design_cantilever,
    eig_case_name,
    mesh_cantilever,
    run_eig,
)
from ada.config import logger  # noqa: E402
from ada.fem.formats.abaqus.config import AbaqusSetup  # noqa: E402


def main(overwrite=True, execute=True, build_report=False, show=False):
    if ru.ODB_DUMP_EXE is not None:
        AbaqusSetup.set_default_post_processor(ru.post_processing_abaqus)

    fea_format = "code_aster"
    geom_repr = "shell"
    elem_order = 1
    use_hex_quad = False
    reduced_integration = False
    eigen_modes = 10

    a = design_cantilever()
    a = mesh_cantilever(
        a,
        geom_repr=geom_repr,
        elem_order=elem_order,
        use_hex_quad=use_hex_quad,
        reduced_integration=reduced_integration,
    )

    result = run_eig(
        a,
        fem_format=fea_format,
        scratch_dir=_VERIFICATION_DIR / "temp" / "eigen",
        name=eig_case_name(fea_format, geom_repr, elem_order, use_hex_quad, reduced_integration),
        eigen_modes=eigen_modes,
        overwrite=overwrite,
        execute=execute,
    )

    if show:
        a.show()

    if build_report:
        if result is None:
            logger.error("No result file is located")
            return

        metadata = dict(geo=geom_repr, elo=elem_order, hexquad=use_hex_quad,
                        reduced_integration=reduced_integration)
        fvr = ru.postprocess_result(result, metadata)
        bm = next(b for b in a.get_all_physical_objects() if isinstance(b, ada.Beam))
        build_fea_report(bm, [fvr], eigen_modes)


if __name__ == "__main__":
    logger.setLevel("INFO")
    main(overwrite=True, execute=True, build_report=False)
