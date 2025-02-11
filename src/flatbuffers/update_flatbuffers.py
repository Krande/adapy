import pathlib
import shutil
import subprocess

from dataclass_gen import generate_dataclasses_from_schema, parse_fbs_file
from deserializer_gen import generate_deserialization_code
from serializer_gen import generate_serialization_code
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
    for cmd_file in _SCHEMA_DIR.glob("*.fbs"):
        # Generate FlatBuffers code
        args = [
            "flatc",
            "--python",
            "-o",
            _COMMS_DIR.as_posix(),
            cmd_file.as_posix(),
            "&&",
            "flatc",
            "--ts",
            "--gen-object-api",
            "-o",
            _GEN_DIR.as_posix(),
            cmd_file.as_posix(),
            "&&",
            "git",
            "add",
            _WSOCK_DIR.as_posix(),
            _GEN_DIR.as_posix(),
        ]

        result = subprocess.run(" ".join(args), shell=True, check=True, cwd=ROOT_DIR)
        if result.returncode == 0:
            print("FlatBuffers generated successfully!")
        else:
            raise Exception("Error generating FlatBuffers!")

    # Update imports in the generated code
    update_py_imports(_WSOCK_DIR)
    update_ts_imports(_GEN_DIR)

    # Update datclasses and enums
    generate_dataclasses_from_schema(parse_fbs_file(CMD_FILE.as_posix()), _COMMS_DIR / "fb_model_gen.py")

    # Update serializer and deserializer
    generate_serialization_code(
        CMD_FILE.as_posix(), _COMMS_DIR / "fb_serializer.py", "ada.comms.wsock", "ada.comms.fb_model_gen"
    )
    generate_deserialization_code(
        CMD_FILE.as_posix(), _COMMS_DIR / "fb_deserializer.py", "ada.comms.wsock", "ada.comms.fb_model_gen"
    )


if __name__ == "__main__":
    main()
