# Optimization strategies copied from the IfcPatch subdir of IfcOpenShell repo

import pathlib

import ifcopenshell
import ifcopenshell.util.element
from ifcopenshell.util.element import remove_deep2

THIS_DIR = pathlib.Path(__file__).parent
TEMP_DIR = THIS_DIR / "temp"
# /* should remove #104,#115, #147,#163,#179 in order to be left with only 1 surface */
# #29= IFCCLOSEDSHELL((#131));
URL = "https://raw.githubusercontent.com/IfcOpenShell/files/master/advanced_brep.ifc"


def read_txt_from_url(url):
    import requests

    response = requests.get(url)
    response.raise_for_status()
    return response.text


def main_cleanup_of_unused_elements():
    temp_ifc = TEMP_DIR / "ifc_file.ifc"
    temp_ifc.parent.mkdir(exist_ok=True)
    if not temp_ifc.exists():
        with open(temp_ifc, "w") as f:
            f.write(read_txt_from_url(URL))

    delete_and_follow_usage = [104, 115, 147, 163, 179]

    f_old = ifcopenshell.open(temp_ifc)
    ifc_closed_shell = f_old.by_id(29)
    ifc_closed_shell.CfsFaces = tuple([i for i in ifc_closed_shell.CfsFaces if i.id() not in delete_and_follow_usage])
    start_entities = len(list(f_old))
    for elem_id in delete_and_follow_usage:
        elem = f_old.by_id(elem_id)

        remove_deep2(f_old, elem)

    end_entities = len(list(f_old))
    print(f"Optimized number of IFC entities from {start_entities} to {end_entities}")
    f_old.write(TEMP_DIR / "optimized.ifc")


if __name__ == "__main__":
    main_cleanup_of_unused_elements()
