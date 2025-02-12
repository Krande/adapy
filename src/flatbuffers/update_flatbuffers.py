import pathlib
import shutil
import subprocess
import sys

from dataclass_gen import generate_dataclasses_from_schema, parse_fbs_file
from gen_deserializer import generate_deserialization_code
from gen_serializer import generate_serialization_code
from update_imports import update_py_imports, update_ts_imports

ROOT_DIR = pathlib.Path(__file__).parent.parent.parent

_COMMS_DIR = ROOT_DIR / "src/ada/comms/"
_SCHEMA_DIR = ROOT_DIR / "src/flatbuffers/schemas/"
_GEN_DIR = ROOT_DIR / "src/frontend/src/flatbuffers"
CMD_FILE = _SCHEMA_DIR / "commands.fbs"
_WSOCK_DIR = ROOT_DIR / "src/ada/comms/wsock/"


def main():
    # Clean wsock directory and generated directory
    shutil.rmtree(_WSOCK_DIR, ignore_errors=True)
    shutil.rmtree(_GEN_DIR, ignore_errors=True)
    flatc_exe = shutil.which("flatc.exe")
    if flatc_exe is None: #
        flatc_exe = pathlib.Path(sys.prefix) / "Library/bin/flatc.exe"
        flatc_exe = shutil.which(flatc_exe)

    if flatc_exe is None:
        raise Exception("FlatBuffers compiler not found in PATH!")

    if isinstance(flatc_exe, str):
        flatc_exe = pathlib.Path(flatc_exe)

    main_cmd_file = _SCHEMA_DIR / "commands.fbs"
    schema_files = [fbs.as_posix() for fbs in _SCHEMA_DIR.rglob("*.fbs")]

    _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    # Generate FlatBuffers code
    args = [
        flatc_exe.as_posix(),
        "--python",
        "-o",
        _COMMS_DIR.as_posix(),
        *schema_files,
        "&&",
        flatc_exe.as_posix(),
        "--ts",
        "--gen-object-api",
        "-o",
        _GEN_DIR.as_posix(),
        *schema_files,
    ]

    result = subprocess.run(" ".join(args), shell=True, check=True, cwd=ROOT_DIR)
    if result.returncode == 0:
        print("FlatBuffers generated successfully!")
    else:
        raise Exception("Error generating FlatBuffers!")



    finalize_args = [
        "git",
        "add",
        _COMMS_DIR.as_posix(),
        _GEN_DIR.as_posix(),
    ]

    result = subprocess.run(finalize_args, shell=True, check=True)

    if result.returncode != 0:
        raise Exception("Error generating FlatBuffers!")

    # Update imports in the generated code
    update_py_imports(_WSOCK_DIR)
    update_ts_imports(_GEN_DIR)

    # Update datclasses and enums
    fbs_schema = parse_fbs_file(main_cmd_file.as_posix())
    sequence = fbs_schema.includes + [fbs_schema]

    for included_fbs in sequence:
        namespace = included_fbs.namespace
        prefix = f"fb_{namespace}"

        fb_gen_import_root = f"ada.comms.{namespace}"
        dc_imports = f"ada.comms.{prefix}_gen"

        generate_dataclasses_from_schema(included_fbs, _COMMS_DIR / f"{prefix}_gen.py", "ada.comms")

        # Update serializer and deserializer
        generate_serialization_code(
            included_fbs, _COMMS_DIR / f"{prefix}_serializer.py", fb_gen_import_root, dc_imports
        )
        generate_deserialization_code(
            included_fbs.file_path.as_posix(), _COMMS_DIR / f"{prefix}_deserializer.py", fb_gen_import_root, dc_imports
        )


if __name__ == "__main__":
    main()
