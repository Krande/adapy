"""Drive a single eig case from the design->mesh->run_eig pipeline.

Useful for quick iteration on the FEM-tasks helpers without spinning
up the full @task DAG or building the report. Drops the
``build_report`` branch the legacy version had — for full-bundle
output, use `pixi run -e docs fea-doc` (or
`paradoc build verification`).
"""

import sys
from pathlib import Path

# Flat script under verification/scripts/. Bootstrap the parent
# verification/ dir onto sys.path so `build_report_utils` resolves
# as a sibling of one level up.
_VERIFICATION_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_VERIFICATION_DIR))

import build_report_utils as ru  # noqa: E402

import ada  # noqa: E402
from ada.api.fem_tasks import (  # noqa: E402
    design_cantilever,
    eig_case_name,
    mesh_cantilever,
    run_eig,
)
from ada.config import logger  # noqa: E402
from ada.fem.formats.abaqus.config import AbaqusSetup  # noqa: E402


def main(overwrite: bool = True, execute: bool = True, show: bool = False):
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

    run_eig(
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


if __name__ == "__main__":
    logger.setLevel("INFO")
    main(overwrite=True, execute=True)
