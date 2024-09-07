import pathlib

import ifcopenshell.geom
import ifcopenshell.validate

import ada
from ada.cadit.step.write.writer import StepWriter

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


def ifc_to_step_file(
        ifc_file_path: str | pathlib.Path,
        step_file: pathlib.Path | None = None,
) -> None:
    if isinstance(ifc_file_path, str):
        ifc_file_path = pathlib.Path(ifc_file_path)

    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_PYTHON_OPENCASCADE, True)

    a = ada.from_ifc(ifc_file_path)
    object = list(a.get_all_physical_objects())[0]
    occ_geo = a.ifc_store.get_ifc_geom(a.ifc_store.f.by_guid(object.guid), settings)
    stp_writer = StepWriter()
    stp_writer.add_shape(occ_geo.geometry, "occ_geo")
    stp_writer.export(step_file)

if __name__ == '__main__':
    ifc_to_step_file(ROOT_DIR / "files/ifc_files/bsplinewknots.ifc", THIS_DIR / "temp" / "bsplinewknots.stp")
