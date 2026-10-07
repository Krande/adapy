from __future__ import annotations

import pathlib
from subprocess import CompletedProcess

from ada.fem.formats.general import get_fem_executable
from ada.fem.formats.utils import default_fem_inp_path


def execute_fem(
    name, fem_format, scratch_dir, cpus, gpus, run_ext, metadata, execute, exit_on_complete, run_in_shell
) -> CompletedProcess | None:
    fem_inp_files = default_fem_inp_path(name, scratch_dir)
    exe_func = get_fem_executable().get(fem_format, None)
    inp_path = fem_inp_files.get(fem_format, None)

    if exe_func is None:
        if execute is False:
            return None
        else:
            raise NotImplementedError(f'The FEM format "{fem_format}" has no execute function')

    if inp_path is None:
        raise ValueError(f"FEM format '{fem_format}' is not supported")

    if isinstance(inp_path, str):
        inp_path = pathlib.Path(inp_path)

    out = exe_func(
        inp_path=inp_path,
        cpus=cpus,
        gpus=gpus,
        run_ext=run_ext,
        metadata=metadata,
        execute=execute,
        exit_on_complete=exit_on_complete,
        run_in_shell=run_in_shell,
    )

    if out is None:
        return None

    out_str = ""
    for out_stream in [out.stdout, out.stderr]:
        if out_stream is not None:
            out_str += out_stream

    with open(inp_path.parent / "run_log.txt", "w", encoding="utf8") as f:
        f.write(out_str)

    return out


def check_previous_run(name, fem_format, scratch_dir) -> None:
    """Raise :class:`~ada.fem.exceptions.fea_execution.FEASolveFailed` unless the run that left the result of
    ``name`` in ``scratch_dir`` says it finished: Code_Aster's verdict in the .mess file (``OK`` or ``<A>_ALARM``),
    CalculiX's saved output (``Job finished``, no ``*ERROR``). Other formats are not checked.

    A failed run can leave a result file: Code_Aster's MED file holds what was printed before the command that
    stopped, CalculiX's .frd its header. ``Assembly.to_fem`` with ``overwrite=False`` read such a file back as the
    answer without looking at the verdict the run was checked by (``check_code_aster_run``, ``check_calculix_run``).
    """
    from ada.fem.formats.general import FEATypes

    inp_path = default_fem_inp_path(name, scratch_dir).get(fem_format)
    if inp_path is None:
        return
    if fem_format == FEATypes.CODE_ASTER:
        from ada.fem.formats.code_aster.execute import check_code_aster_run

        check_code_aster_run(pathlib.Path(inp_path).with_suffix(".mess"))
    elif fem_format == FEATypes.CALCULIX:
        from ada.fem.formats.calculix.execute import check_previous_calculix_run

        check_previous_calculix_run(inp_path)
