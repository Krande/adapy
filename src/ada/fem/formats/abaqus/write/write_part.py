from typing import TYPE_CHECKING

from .write_constraints import constraints_str
from .write_elements import elements_str
from .write_masses import masses_str
from .write_nodes import nodes_str
from .write_sections import sections_str
from .write_sets import elsets_str, nsets_str
from .write_springs import springs_str
from .write_surfaces import surfaces_str

if TYPE_CHECKING:
    from ada import Part


def write_abaqus_part_str(part: "Part") -> str:
    fem = part.fem
    return f"""** Abaqus Part {part.name}
** Exported using ADA OpenSim
*NODE
{nodes_str(fem)}
{elements_str(fem, False)}
{elsets_str(fem)}
{nsets_str(fem)}
{sections_str(fem)}
{masses_str(fem)}
{surfaces_str(fem)}
{constraints_str(fem)}
{springs_str(fem)}""".rstrip()


@property
def instance_move_str(self):
    if self.part.fem.metadata["move"] is not None:
        move = self.part.fem.metadata["move"]
        mo_str = "\n " + ", ".join([str(x) for x in move])
    else:
        mo_str = "\n 0.,        0.,           0."

    if self.part.fem.metadata["rotate"] is not None:
        rotate = self.part.fem.metadata["rotate"]
        vecs = ", ".join([str(x) for x in rotate[0]])
        vece = ", ".join([str(x) for x in rotate[1]])
        angle = rotate[2]
        move_str = """{move_str}\n {vecs}, {vece}, {angle}""".format(move_str=mo_str, vecs=vecs, vece=vece, angle=angle)
    else:
        move_str = "" if mo_str == "0.,        0.,           0." else mo_str
    return move_str
