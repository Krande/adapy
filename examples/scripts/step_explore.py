import pathlib
from OCC.Core.TopAbs import TopAbs_SHELL
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.XCAFDoc import XCAFDoc_DataMapOfShapeLabel

from ada.occ.step.reader import StepStore

MAX_32_BIT_INT = 2**31 - 1

FILES_DIR = pathlib.Path(__file__).parent.parent.parent / "files/step_files"
COLOR_FILE = FILES_DIR / "flat_plate_abaqus_10x10_m_wColors.stp"
if not COLOR_FILE.exists():
    raise FileNotFoundError(f"File {COLOR_FILE} not found")


def main():
    step_store = StepStore(COLOR_FILE)
    std_reader = step_store.create_step_reader()
    caf_reader = step_store.create_caf_step_reader()
    r = caf_reader.NbRootsForTransfer()
    nb = std_reader.NbShapes()
    res = caf_reader.GetShapeLabelMap()

    for step_shape in step_store.iter_all_shapes(True):
        print(f"Shape: {step_shape}")


if __name__ == "__main__":
    main()
