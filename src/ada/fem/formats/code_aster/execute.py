import os
import pathlib
import time
from functools import wraps

from ada.config import logger

from ..utils import LocalExecute


def write_to_log(res_str, fname):
    with open(f'timeit_{fname.split(".")[0]}.log', "a") as d:
        d.write("\n" + res_str)


def run_code_aster(
    inp_path,
    cpus=2,
    gpus=None,
    run_ext=False,
    metadata=None,
    execute=True,
    return_bat_str=False,
    exit_on_complete=True,
    run_in_shell=False,
):
    """
    :param inp_path: Path to input file folder(s)
    :param cpus: Number of CPUs to run the analysis on. Default is 2.
    :param gpus: Number of GPUs to run the analysis on. Default is none.
    :param run_ext: If False the process will wait for the abaqus analysis to finish. Default is False
    :param metadata: Dictionary containing various metadata relevant for the analysis
    :param execute: Automatically starts Abaqus analysis. Default is True
    :param return_bat_str:
    :param exit_on_complete:
    :param run_in_shell:
    """

    name = pathlib.Path(inp_path).stem
    ca = CodeAsterExecute(
        inp_path,
        cpus=cpus,
        run_ext=run_ext,
        metadata=metadata,
        auto_execute=execute,
    )
    with open(inp_path, "w") as f:
        f.write(write_export_file(name, cpus))

    return ca.run(exit_on_complete=exit_on_complete)


class CodeAsterExecute(LocalExecute):
    def run(self, exit_on_complete=True):
        from ada.fem.formats.general import FEATypes

        exe_path = self.get_exe(FEATypes.CODE_ASTER)
        args = f"{exe_path} {self.analysis_name}.export"
        if "run_aster" in exe_path.name:
            args += " --wrkdir=temp"

        out = self._run_local(args, exit_on_complete=exit_on_complete)
        return out


def write_export_file(name: str, cpus: int):
    export_str = f"""P actions make_etude
P memory_limit 1274
P time_limit 900
P version stable
P mpi_nbcpu 1
P mode interactif
P ncpus {cpus}
F comm {name}.comm D 1
F mmed {name}.med D 20
F mess {name}.mess R 6
F rmed {name}.rmed R 80"""

    return export_str


def clear_temp_files(this_dir):
    patterns = ["fort*", "glob*", "vola*", "pick.code_aster*"]

    for pattern in patterns:
        for f in this_dir.glob(pattern):
            if f.is_file():
                os.remove(f)


def init_close_code_aster(func_=None, *, info_level=1, temp_dir=None):
    def actual_decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            print("Starting code_aster")
            start = time.time()
            conda_dir = pathlib.Path(os.getenv("CONDA_PREFIX"))
            lib_dir = conda_dir / "lib"
            lib_aster_dir = lib_dir / "aster"
            os.environ["LD_LIBRARY_PATH"] = lib_aster_dir.as_posix() + ":" + os.getenv("LD_LIBRARY_PATH", "")
            os.environ["PYTHONPATH"] = lib_aster_dir.as_posix() + ":" + os.getenv("PYTHONPATH", "")
            os.environ["ASTER_LIBDIR"] = lib_dir.as_posix()
            os.environ["ASTER_DATADIR"] = (conda_dir / "share/aster").as_posix()
            os.environ["ASTER_LOCALEDIR"] = (conda_dir / "share/locale/aster").as_posix()
            os.environ["ASTER_ELEMENTSDIR"] = lib_aster_dir.as_posix()

            import code_aster

            this_dir = pathlib.Path(".").resolve().absolute()

            nonlocal temp_dir
            if temp_dir is None:
                clear_temp_files(this_dir)  # Assuming you have this function defined elsewhere
            else:
                if isinstance(temp_dir, str):
                    temp_dir = pathlib.Path(temp_dir)
                    temp_dir = temp_dir.resolve().absolute()

                if temp_dir.exists():
                    clear_temp_files(temp_dir)

                temp_dir.mkdir(exist_ok=True, parents=True)
                logger.info("Changing current directory to keep Code_Aster files away from the code directory")
                os.chdir(temp_dir)

            print(f"{info_level=}")
            code_aster.init(INFO=info_level)

            result = None
            run_issue = None
            try:
                result = func(*args, **kwargs)
            except BaseException as e:
                # Assuming you have a logger
                logger.error(e)
                run_issue = e
                raise
            finally:
                code_aster.close()
                if temp_dir is not None:
                    # Change back
                    os.chdir(this_dir)
                end = time.time()
                print(f"Simulation time: {end - start:.2f}s")

            if result is not None:
                return result

            raise Exception(run_issue)

        return wrapper

    if func_ is None:
        return actual_decorator
    else:
        return actual_decorator(func_)
