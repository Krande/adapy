import pathlib
import shutil
import subprocess
import sys

from topological_sorting import topological_sort
from schema_includes import find_schema_inclusions

ROOT_DIR = pathlib.Path(__file__).parent.parent.parent

_COMMS_DIR = ROOT_DIR / "src/ada/comms/fb"
_SCHEMA_DIR = ROOT_DIR / "src/flatbuffers/schemas/"
_GEN_DIR = ROOT_DIR / "src/frontend/src/flatbuffers"
CMD_FILE = _SCHEMA_DIR / "message.fbs"


def call_ts_flatbuffers(flatc_exe: pathlib.Path, schema_files: list[str]):
    # Generate FlatBuffers code
    args = [
        flatc_exe.as_posix(),
        "--ts",
        "--gen-object-api",
        "-o",
        _GEN_DIR.as_posix(),
        # CMD_FILE.as_posix(),
        # "--gen-all",
        *schema_files,

    ]

    print("Running command:", " ".join(args))
    result = subprocess.run(" ".join(args), shell=True, check=True, cwd=ROOT_DIR)
    if result.returncode == 0:
        print("FlatBuffers generated successfully!")
        if result.stdout:
            print(result.stdout.decode())
        if result.stderr:
            print(result.stderr.decode())
    else:
        raise Exception("Error generating FlatBuffers!")

def call_py_flatbuffers(flatc_exe: pathlib.Path, schema_files: list[str]):
    # Generate FlatBuffers code
    args = [
        flatc_exe.as_posix(),
        "--python",
        "-o",
        _COMMS_DIR.as_posix(),
        *schema_files,
    ]

    print("Running command:", " ".join(args))
    result = subprocess.run(" ".join(args), shell=True, check=True, cwd=ROOT_DIR)
    if result.returncode == 0:
        print("FlatBuffers generated successfully!")
        if result.stdout:
            print(result.stdout.decode())
        if result.stderr:
            print(result.stderr.decode())
    else:
        raise Exception("Error generating FlatBuffers!")

def run_flatc():
    # Clean wsock directory and generated directory
    shutil.rmtree(_GEN_DIR, ignore_errors=True)
    shutil.rmtree(_COMMS_DIR, ignore_errors=True)
    flatc_exe = shutil.which("flatc.exe")
    if flatc_exe is None:  #
        flatc_exe = pathlib.Path(sys.prefix) / "Library/bin/flatc.exe"
        flatc_exe = shutil.which(flatc_exe)

    if flatc_exe is None:
        raise Exception("FlatBuffers compiler not found in PATH!")

    if isinstance(flatc_exe, str):
        flatc_exe = pathlib.Path(flatc_exe)

    schema_files_fp = [fbs for fbs in _SCHEMA_DIR.rglob("*.fbs")]
    schema_files = [fbs.as_posix() for fbs in schema_files_fp]

    _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    call_ts_flatbuffers(flatc_exe, schema_files)

    inclusions = find_schema_inclusions(schema_files_fp, _SCHEMA_DIR)
    sorted_inclusions = topological_sort(inclusions)
    for schema in sorted_inclusions:
        all_includes = [schema, *inclusions[schema]]

        call_py_flatbuffers(flatc_exe, [(_SCHEMA_DIR / x).as_posix() for x in all_includes])

    finalize_args = [
        "git",
        "add",
        _COMMS_DIR.as_posix(),
        _GEN_DIR.as_posix(),
    ]

    result = subprocess.run(finalize_args, shell=True, check=True)

    if result.returncode != 0:
        raise Exception("Error generating FlatBuffers!")


if __name__ == "__main__":
    run_flatc()
