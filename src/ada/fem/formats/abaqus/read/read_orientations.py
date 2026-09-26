from ada.config import logger
from ada.fem import FEM, Csys

from .helper_utils import get_set_from_assembly
from .keywords import validate
from .lexer import KeywordBlock, iter_keywords


def _points(block: KeywordBlock) -> list[str]:
    """The a/b/c point values of an ``*Orientation``'s first data line.

    Six values give points a and b; nine add point c. Abaqus permits either, so the reader
    takes what is there rather than requiring a fixed count.
    """
    if not block.data_lines:
        return []
    return [x.strip() for x in block.data_lines[0].split(",") if x.strip() != ""]


def get_lcsys_from_bulk(bulk_str: str, parent: FEM) -> dict[str, Csys]:
    """
    https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-orientation.htm#simakey-r-orientation

    """
    lcsysd = dict()
    for block in iter_keywords(bulk_str, "ORIENTATION"):
        validate(block)
        name = (block.params.get("NAME") or "").replace('"', "")
        defi = block.params.get("DEFINITION") or "COORDINATES"
        system = block.params.get("SYSTEM") or "RECTANGULAR"
        values = _points(block)
        # COORDINATES gives points a and b as x,y,z each (optionally c as well); NODES gives
        # the three node references themselves. So the two spellings need different counts.
        required = 3 if defi.upper() == "NODES" else 6
        if len(values) < required:
            logger.warning(
                "abaqus read: *Orientation %r (line %d) needs at least %d values on its data line — skipping",
                name,
                block.lineno,
                required,
            )
            continue
        if defi.upper() == "COORDINATES":
            coords = [tuple(float(x) for x in values[0:3]), tuple(float(x) for x in values[3:6])]
            if len(values) >= 9:
                coords += [tuple(float(x) for x in values[6:9])]
            lcsysd[name] = Csys(name, system=system, coords=coords, parent=parent)
        elif defi.upper() == "NODES":
            nodes = [get_set_from_assembly(v, parent, "nset") for v in values[0:3]]
            lcsysd[name] = Csys(name, system=system, definition=defi, nodes=nodes, parent=parent)
        else:
            raise NotImplementedError(f'Orientation definition "{defi}" is not yet supported')

    return lcsysd
