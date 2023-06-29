from ada.fem import FEM, Csys

from . import cards
from .helper_utils import get_set_from_assembly


def get_lcsys_from_bulk(bulk_str: str, parent: FEM) -> dict[str, Csys]:
    """
    https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-orientation.htm#simakey-r-orientation

    """
    lcsysd = dict()
    for m in cards.orientation.regex.finditer(bulk_str):
        d = m.groupdict()
        name = d["name"].replace('"', "")
        defi = d.get("definition", "COORDINATES")
        system = d.get("system", "RECTANGULAR")
        if defi is None or defi.upper() == "COORDINATES":
            coords = [tuple(float(d[x]) for x in ["ax", "ay", "az"]), tuple(float(d[x]) for x in ["bx", "by", "bz"])]
            if d["cx"] is not None:
                coords += [(float(d["cx"]), float(d["cy"]), float(d["cz"]))]
            lcsysd[name] = Csys(name, system=system, coords=coords, parent=parent)
        elif defi.upper() == "NODES":
            nodes = []
            for n in ["ax", "ay", "az"]:
                nodes += [get_set_from_assembly(d[n], parent, "nset")]
            lcsysd[name] = Csys(name, system=system, definition=defi, nodes=nodes, parent=parent)
        else:
            raise NotImplementedError(f'Orientation definition "{defi}" is not yet supported')

    return lcsysd
