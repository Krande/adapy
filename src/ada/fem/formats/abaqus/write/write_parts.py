from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ada.config import Config
from ada.fem.conversion_utils import convert_ecc_to_mpc, convert_hinges_2_couplings

from .write_constraints import constraints_str
from .write_elements import elements_str
from .write_masses import masses_str
from .write_nodes import nodes_str, rp_str
from .write_sections import sections_str
from .write_sets import elsets_str, nsets_str
from .write_springs import springs_str
from .write_surfaces import surfaces_str

if TYPE_CHECKING:
    from ada import Assembly, Part


def write_all_parts(assembly: Assembly, analysis_dir):
    for part in assembly.get_all_subparts():
        if len(part.fem.elements) == 0 and len(part.fem.nodes) == 0:
            continue

        if Config().fem_convert_options_hinges_to_coupling is True:
            convert_hinges_2_couplings(part.fem)

        if Config().fem_convert_options_ecc_to_mpc is True:
            convert_ecc_to_mpc(part.fem)

        write_part_bulk(part, analysis_dir)


def write_part_bulk(part_in: "Part", analysis_dir):
    bulk_path = analysis_dir / f"bulk_{part_in.name}"
    bulk_file = bulk_path / "aba_bulk.inp"
    os.makedirs(bulk_path, exist_ok=True)

    if part_in.fem.initial_state is not None:
        with open(bulk_file, "w") as d:
            d.write("** This part is replaced by an initial state step")
        return None

    with open(bulk_file, "w") as d:
        d.write(write_abaqus_part_str(part_in))


def write_abaqus_part_str(part: "Part") -> str:
    fem = part.fem
    return f"""** Abaqus Part {part.name}
** Exported using ADA OpenSim
{nodes_str(fem)}
{elements_str(fem, False)}
{rp_str(fem)}
{elsets_str(fem, False)}
{nsets_str(fem, False)}
{sections_str(fem)}
{masses_str(fem, False)}
{surfaces_str(fem, False)}
{constraints_str(fem, False)}
{springs_str(fem)}""".rstrip()


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
