import pathlib

import ifcopenshell
from ifcopenshell.util.unit import get_unit_assignment
# from convert_length_unit_patch import Patcher
from convert_length_unit_patch_v2 import Patcher
# from convert_length_unit_patch_v3 import Patcher
parents = list(pathlib.Path(__file__).resolve().absolute().parents)
print(parents)
FILES_DIR = [fp for fp in parents if fp.name == "examples"][0].parent / "files"


def main():
    ifc_file = FILES_DIR / "ifc_files/beams/beam-standard-case.ifc"
    ifc_file_out = pathlib.Path("temp") / "beam-standard-case-re-exported.ifc"
    ifc_file_out.parent.mkdir(exist_ok=True, parents=True)

    # Convert the units of the IFC file
    file = ifcopenshell.open(ifc_file)

    # Convert the units of the IFC file
    task = Patcher(src=ifc_file, file=file, logger=None, unit="METERS")
    task.patch()

    # Export the IFC file
    task.file_patched.write(str(ifc_file_out))

    for inverse in task.file_patched.by_type("IFCCARTESIANPOINT"):
        print(tuple(inverse))


if __name__ == '__main__':
    main()
