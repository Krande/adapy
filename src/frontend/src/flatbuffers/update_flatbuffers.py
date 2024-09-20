import pathlib
import subprocess
from update_imports import update_py_imports, update_ts_imports
from dataclass_gen import parse_fbs_file

ROOT_DIR = pathlib.Path(__file__).parent.parent.parent.parent.parent

_COMMS_DIR = ROOT_DIR / "src/ada/comms/"
_SCHEMA_DIR = ROOT_DIR / "src/frontend/src/flatbuffers/schemas/"
_GEN_DIR = ROOT_DIR / "src/frontend/src/flatbuffers/generated/"
CMD_FILE = _SCHEMA_DIR / "commands.fbs"
_WSOCK_DIR = ROOT_DIR / "src/ada/comms/wsock/"

def main():
    args = [
        "flatc", "--python", "-o", _COMMS_DIR.as_posix(), CMD_FILE.as_posix(), "&&",
        "flatc", "--ts", "-o", _GEN_DIR.as_posix(), CMD_FILE.as_posix(), "&&",
        "git", "add", _WSOCK_DIR.as_posix(), _GEN_DIR.as_posix()]

    result = subprocess.run(" ".join(args), shell=True, check=True, cwd=ROOT_DIR)
    if result.returncode == 0:
        print("FlatBuffers generated successfully!")
    else:
        raise Exception("Error generating FlatBuffers!")

    python_code = parse_fbs_file(CMD_FILE)
    with open(_COMMS_DIR / 'fb_model_gen.py', 'w') as output_file:
        output_file.write(python_code)

    update_py_imports(_WSOCK_DIR)
    update_ts_imports(_GEN_DIR)



if __name__ == '__main__':
    main()
