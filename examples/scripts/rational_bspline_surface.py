import os

os.environ["ADA_IFC_IMPORT_SHAPE_GEOM"] = "true"
os.environ["ADA_GENERAL_DEBUG"] = "true"
from ada.cadit.step.read.geom.geom_reader import import_geometry_from_step_geom
from ada.core.tools import attach_vs_debugger_to_this_process


import ada
import pathlib

FILES_DIR = [fp for fp in pathlib.Path(__file__).resolve().absolute().parents if fp.name == "examples"][
                0
            ].parent / "files"


def main():
    a = ada.from_ifc(FILES_DIR / "ifc_files/bsplinesurfacewithknots.ifc")
    obj = list(a.get_all_physical_objects())[0]
    advanced_face = obj.geom.geometry

    occ_geom = a.ifc_store.get_ifc_geom(a.ifc_store.f.by_guid(obj.guid), a.ifc_store.settings).geometry
    param_geom = list(import_geometry_from_step_geom(occ_geom))[0]

    # a.show()
    attach_vs_debugger_to_this_process()
    a.to_stp("temp/bsplinesurfacewithknots.stp")


if __name__ == "__main__":
    main()
