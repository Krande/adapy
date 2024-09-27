import pathlib

import ada

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


def run_this():
    a = ada.from_genie_xml(ROOT_DIR / "files/fem_files/sesam/curved_plates.xml")

    a.show(add_ifc_backend=True, unique_viewer_id=1)


if __name__ == "__main__":
    run_this()
