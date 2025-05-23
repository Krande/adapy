import pathlib
import shutil
import subprocess

from gen_dataclasses import generate_dataclasses_from_schema, load_fbs_file
from gen_deserializer import generate_deserialization_code
from gen_serializer import generate_serialization_code
from run_flatbuffer_cli import run_flatc
from update_imports import update_py_imports, update_ts_imports

ROOT_DIR = pathlib.Path(__file__).parent.parent.parent

_COMMS_DIR = ROOT_DIR / "src/ada/comms/fb"
_SCHEMA_DIR = ROOT_DIR / "src/flatbuffers/schemas/"
_GEN_DIR = ROOT_DIR / "src/frontend/src/flatbuffers"
CMD_FILE = _SCHEMA_DIR / "message.fbs"


def add_to_git():
    finalize_args = [
        "git",
        "add",
        _COMMS_DIR.as_posix(),
        _GEN_DIR.as_posix(),
    ]

    result = subprocess.run(finalize_args, shell=True, check=True)

    if result.returncode != 0:
        raise Exception("Error adding FlatBuffers files to git!")


def main():
    # Clean wsock directory and generated directory
    shutil.rmtree(_GEN_DIR, ignore_errors=True)
    shutil.rmtree(_COMMS_DIR, ignore_errors=True)

    main_cmd_file = CMD_FILE

    _SCHEMA_DIR.mkdir(parents=True, exist_ok=True)

    # Generate FlatBuffers code
    run_flatc()

    # Update imports in the generated code

    update_ts_imports(_GEN_DIR)

    # Update datclasses and enums
    fbs_schema = load_fbs_file(main_cmd_file.as_posix(), py_root="ada.comms.fb")
    sequence = fbs_schema.includes + [fbs_schema]
    namespaces = [fbs.namespace for fbs in sequence]
    for included_fbs in sequence:
        namespace = included_fbs.namespace
        for ns in namespaces:
            update_py_imports(ns, _COMMS_DIR / namespace)

        prefix = f"fb_{namespace}"

        fb_gen_import_root = f"{included_fbs.py_root}.{namespace}"
        dc_imports = f"{included_fbs.py_root}.{prefix}_gen"

        generate_dataclasses_from_schema(included_fbs, _COMMS_DIR / f"{prefix}_gen.py")

        # Update serializer and deserializer
        generate_serialization_code(
            included_fbs,
            _COMMS_DIR / f"{prefix}_serializer.py",
            fb_gen_import_root,
            dc_imports,
            py_root=fbs_schema.py_root,
        )
        generate_deserialization_code(
            included_fbs.file_path.as_posix(),
            _COMMS_DIR / f"{prefix}_deserializer.py",
            fb_gen_import_root,
            dc_imports,
            py_root=fbs_schema.py_root,
        )

    add_to_git()


if __name__ == "__main__":
    main()
