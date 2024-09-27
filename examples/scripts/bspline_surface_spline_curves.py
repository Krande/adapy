import os

os.environ["ADA_IFC_IMPORT_SHAPE_GEOM"] = "true"
os.environ["ADA_GENERAL_DEBUG"] = "false"
os.environ["ADA_GXML_IMPORT_ADVANCED_FACES"] = "true"

import pathlib

import ada
from ada.cadit.step.read.geom.geom_reader import import_geometry_from_step_geom
from ada.config import Config
from ada.core.tools import attach_vs_debugger_to_this_process

_parents = pathlib.Path(__file__).resolve().absolute().parents
FILES_DIR = [fp for fp in _parents if fp.name == "examples"][0].parent / "files"


def main(add_vs_debugger=False, import_occ_geom=False):
    Config().reload_config()

    a = ada.from_genie_xml(FILES_DIR / "fem_files/sesam/curved_plates.xml")

    if import_occ_geom:
        a_obj = list(a.get_all_physical_objects(by_type=ada.PlateCurved))[0]
        a_advanced_face = a_obj.geom.geometry
        occ_geom = a.ifc_store.get_ifc_geom(a.ifc_store.f.by_guid(a_obj.guid), a.ifc_store.settings).geometry
        param_geom = list(import_geometry_from_step_geom(occ_geom))[0]

    if add_vs_debugger:
        attach_vs_debugger_to_this_process()

    a.show()

    # a.to_gltf("temp/bsplinesurfacewithknots_wsplinecurves.glb")
    # a.to_stp("temp/bsplinesurfacewithknots_wsplinecurves.stp")


if __name__ == "__main__":
    main()
