import os

os.environ["ADA_IFC_IMPORT_SHAPE_GEOM"] = "true"
os.environ["ADA_GENERAL_DEBUG"] = "true"
import ada
import pathlib

FILES_DIR = [fp for fp in pathlib.Path(__file__).resolve().absolute().parents if fp.name == "examples"][
                0
            ].parent / "files"


def main():

    a = ada.from_ifc(FILES_DIR / "ifc_files/bsplinesurfacewithknots.ifc")
    # a.show()
    a.to_stp("temp/bsplinesurfacewithknots.stp")


if __name__ == "__main__":
    main()
