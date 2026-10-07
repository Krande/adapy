from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Callable

from ada.base.types import BaseEnum
from ada.config import logger

from .utils import interpret_fem_format_from_path

if TYPE_CHECKING:
    from ada import Assembly


class FEATypes(BaseEnum):
    CODE_ASTER = "code_aster"
    CALCULIX = "calculix"
    ABAQUS = "abaqus"
    SESAM = "sesam"
    USFOS = "usfos"
    OPENCOURANT = "opencourant"
    GMSH = "gmsh"

    # formats only
    XDMF = "xdmf"

    @staticmethod
    def get_solvers_only():
        non_solvers = [FEATypes.XDMF, FEATypes.GMSH]
        return [x for x in FEATypes if x not in non_solvers]


def get_available_software() -> list[str]:
    """Return the list of solver names this machine can actually run.

    Calculix + Code_Aster are always considered available (they ship via
    the docs/test pixi envs in adapy). Abaqus + Sesam require
    `ADA_abaqus_exe` / `ADA_SESTRA_EXE` to point at existing files.

    Used by reporting code (eg the verification report) to narrow the
    solver fanout to what the host can produce results for.
    """
    software = ["calculix", "code_aster"]

    from .abaqus.versions import get_abaqus_exe
    from .sesam.sesam_exe_locator import get_sestra_default_exe_path

    abaqus_exe = get_abaqus_exe()
    if abaqus_exe is not None:
        if not abaqus_exe.exists():
            raise FileNotFoundError(f"ABAQUS executable not found at {abaqus_exe}")
        software.append("abaqus")

    sestra_path_raw = get_sestra_default_exe_path()
    if sestra_path_raw is not None:
        import pathlib as _pathlib

        sestra_path = _pathlib.Path(sestra_path_raw)
        if not sestra_path.exists():
            raise FileNotFoundError(f"SESTRA executable not found at {sestra_path}")
        software.append("sesam")

    return software


def get_fem_imports() -> dict[FEATypes, Callable[..., Assembly]]:
    from . import abaqus, code_aster, sesam

    return {
        FEATypes.ABAQUS: abaqus.read_fem,
        FEATypes.SESAM: sesam.read_fem,
        FEATypes.CODE_ASTER: code_aster.read_fem,
    }


def get_fem_exports() -> dict[FEATypes, Callable[..., Assembly]]:
    from ada.fem.formats.abaqus.config import AbaqusSetup
    from ada.fem.formats.calculix.config import CalculixSetup
    from ada.fem.formats.code_aster.config import CodeAsterSetup
    from ada.fem.formats.opencourant.config import OpenCourantSetup
    from ada.fem.formats.sesam.config import SesamSetup
    from ada.fem.formats.usfos.config import UsfosSetup

    return {
        FEATypes.ABAQUS: AbaqusSetup.default_pre_processor,
        FEATypes.CALCULIX: CalculixSetup.default_pre_processor,
        FEATypes.CODE_ASTER: CodeAsterSetup.default_pre_processor,
        FEATypes.SESAM: SesamSetup.default_pre_processor,
        FEATypes.USFOS: UsfosSetup.default_pre_processor,
        FEATypes.OPENCOURANT: OpenCourantSetup.default_pre_processor,
    }


def get_fem_executable() -> dict[FEATypes, Callable[..., subprocess.CompletedProcess]]:
    from .abaqus.config import AbaqusSetup
    from .calculix.config import CalculixSetup
    from .code_aster.config import CodeAsterSetup
    from .opencourant.config import OpenCourantSetup
    from .sesam.config import SesamSetup

    return {
        FEATypes.ABAQUS: AbaqusSetup.default_executor,
        FEATypes.CALCULIX: CalculixSetup.default_executor,
        FEATypes.CODE_ASTER: CodeAsterSetup.default_executor,
        FEATypes.SESAM: SesamSetup.default_executor,
        FEATypes.OPENCOURANT: OpenCourantSetup.default_executor,
    }


fem_solver_map = {
    FEATypes.SESAM: "sestra",
    FEATypes.CALCULIX: "ccx",
    FEATypes.CODE_ASTER: "run_aster",
    FEATypes.OPENCOURANT: "starter_linux64_gf",
}


class FemConverters(BaseEnum):
    DEFAULT = "default"
    MESHIO = "meshio"


def get_fem_converters(fem_file, fem_format: str | FEATypes, fem_converter: str | FemConverters):
    from ada.fem.formats.mesh_io import meshio_read_fem, meshio_to_fem

    if isinstance(fem_format, str):
        fem_format = FEATypes.from_str(fem_format)
    if isinstance(fem_converter, str):
        fem_converter = FemConverters.from_str(fem_converter)

    if fem_format is None:
        fem_format = interpret_fem_format_from_path(fem_file)

    if fem_converter == FemConverters.DEFAULT:
        fem_importer = get_fem_imports().get(fem_format, None)
        fem_exporter = get_fem_exports().get(fem_format, None)
    elif fem_converter == FemConverters.MESHIO:
        fem_importer = meshio_read_fem
        fem_exporter = meshio_to_fem
    else:
        raise ValueError(f'Unrecognized fem_converter "{fem_converter}". Only "meshio" and "default" are supported')

    return fem_importer, fem_exporter


def export_fem(assembly, name, analysis_dir, fem_format, fem_converter, metadata):
    _, fem_exporter = get_fem_converters("", fem_format, fem_converter)
    metadata = dict() if metadata is None else metadata
    metadata["fem_format"] = fem_format
    try:
        fem_exporter(assembly, name, analysis_dir, metadata)
        return True
    except IOError as e:
        logger.error(e)
        return False


#: Formats whose writer writes a multi-part assembly as one (parts, instances, assembly-level
#: data), so the model is handed over as it is rather than merged into one part first.
_WRITES_ASSEMBLIES = frozenset({FEATypes.ABAQUS})

#: Formats whose writer writes a part FEM's own steps as well as the assembly's -- where ``Part.to_fem_obj`` puts the
#: step its concept load cases become. The other (Usfos: no step) leaves
#: it out, which is reported.
_WRITES_PART_STEPS = frozenset({FEATypes.ABAQUS, FEATypes.SESAM, FEATypes.CALCULIX, FEATypes.CODE_ASTER})


def write_to_fem(
    assembly: Assembly,
    name: str,
    fem_format: FEATypes,
    overwrite: bool,
    fem_converter: str,
    scratch_dir,
    metadata: dict,
    make_zip_file,
    model_data_only=False,
):
    from ada.fem.formats.utils import default_fem_res_path, folder_prep, should_convert

    fem_res_files = default_fem_res_path(name, scratch_dir=scratch_dir)

    res_path = fem_res_files.get(fem_format, None)
    metadata = dict() if metadata is None else metadata
    metadata["fem_format"] = fem_format.value

    out = None
    if should_convert(res_path, overwrite):
        analysis_dir = folder_prep(scratch_dir, name, overwrite)
        _, fem_exporter = get_fem_converters("", fem_format, fem_converter)

        if fem_exporter is None:
            raise ValueError(f'FEM export for "{fem_format}" using "{fem_converter}" is currently not supported')

        # Multi-instance models: the single-part writers (Sesam/MED/Genie) need one merged FEM.
        # Build a TEMPORARY single-part assembly from a non-destructive merge and export that,
        # so the caller's assembly tree (and its per-part FEMs) is never mutated.
        #
        # Not for a writer that writes assemblies itself: Abaqus has parts, instances and
        # assembly-level data, and merging first renamed every part and dropped what the merge
        # does not carry (assembly-level amplitudes, interactions, reference points) -- so a
        # deck read and written back came out as a different model.
        #
        # The merge carries, re-keyed into the merged ids and set names, what the model's steps,
        # supports and the assembly's own FEM name; it refuses by name what it cannot re-key.
        from ada.fem.concept.loads_to_fem import report_unconverted_concept_loads

        report_unconverted_concept_loads(assembly)
        write_assembly = assembly
        fem_parts = [p for p in assembly.get_all_parts_in_assembly(include_self=True) if len(p.fem.nodes) > 0]
        if len(fem_parts) > 1 and fem_format not in _WRITES_ASSEMBLIES:
            from ada.fem.concat import single_part_assembly

            # The merge keeps the parts' steps, on the merged part's FEM (single_part_assembly).
            write_assembly = single_part_assembly(assembly)
        if fem_format not in _WRITES_PART_STEPS:
            # ... which only some writers write: the others leave a part's step out, merged or not.
            parts = write_assembly.get_all_parts_in_assembly(include_self=False)
            _report_part_steps_not_written([p for p in parts if p.fem.steps], fem_format)

        fem_exporter(write_assembly, name, analysis_dir, metadata, model_data_only)

        if make_zip_file is True:
            import shutil

            shutil.make_archive(name, "zip", str(analysis_dir))
    else:
        logger.warning(f'Result file "{res_path}" already exists.\nUse "overwrite=True" if you wish to overwrite')

    if out is None and res_path is None:
        logger.info("No Result file is created")
        return None


def _report_part_steps_not_merged(fem_parts, fem_format) -> None:
    """The merge into one part carries the assembly's steps only: a part FEM's step -- the step its concept load
    cases became in ``Part.to_fem_obj`` -- and its loads do not reach the writer."""
    from ada.fem.formats import conversion_report

    for p in fem_parts:
        for step in p.fem.steps:
            conversion_report.current().omitted(
                f"{fem_format.value} writer",
                "Step",
                step.name,
                "a step of one of several meshed parts; merging the parts into one keeps the assembly's steps only, "
                "so this step and its loads are not written",
                part=p.name,
                n_loads=len(step.loads),
            )


def _report_part_steps_not_written(fem_parts, fem_format) -> None:
    """A writer that writes the assembly's steps only (or none) leaves a part FEM's step out -- the step its concept
    load cases became in ``Part.to_fem_obj`` -- and its loads with it."""
    from ada.fem.formats import conversion_report

    for p in fem_parts:
        for step in p.fem.steps:
            conversion_report.current().omitted(
                f"{fem_format.value} writer",
                "Step",
                step.name,
                "a step of a part's FEM; this writer writes no part's steps (at most the assembly's), so this step and "
                "its loads are not written",
                part=p.name,
                n_loads=len(step.loads),
            )
