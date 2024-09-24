import os

from ada.config import Config
from ada.occ.tessellating import tessellate_advanced_face

os.environ["ADA_IFC_IMPORT_SHAPE_GEOM"] = "true"
os.environ["ADA_GENERAL_DEBUG"] = "false"


import pathlib

import ada
from ada.cadit.step.read.geom.geom_reader import import_geometry_from_step_geom
from ada.core.tools import attach_vs_debugger_to_this_process

FILES_DIR = [fp for fp in pathlib.Path(__file__).resolve().absolute().parents if fp.name == "examples"][
    0
].parent / "files"


def main(add_vs_debugger=False, import_occ_geom=False):
    Config().reload_config()

    a = ada.from_ifc(FILES_DIR / "ifc_files/bsplinesurfacewithknots.ifc")

    if import_occ_geom:
        a_obj = list(a.get_all_physical_objects())[0]
        a_advanced_face = a_obj.geom.geometry
        occ_geom = a.ifc_store.get_ifc_geom(a.ifc_store.f.by_guid(a_obj.guid), a.ifc_store.settings).geometry
        param_geom = list(import_geometry_from_step_geom(occ_geom))[0]

    if add_vs_debugger:
        attach_vs_debugger_to_this_process()

    a.show()

    # a.to_gltf("temp/bsplinesurfacewithknots.glb")
    # a.to_stp("temp/bsplinesurfacewithknots.stp")


if __name__ == "__main__":
    main()
