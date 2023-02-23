from typing import TYPE_CHECKING

import numpy as np

from ada.config import get_logger
from ada.fem.shapes import definitions as shape_def

from ..common import ada_to_med_type
from .write_sets import _add_cell_sets

if TYPE_CHECKING:
    from ada.concepts.spatial import Part

logger = get_logger()


def elements_str(part: "Part", time_step, profile, families):
    """

    Add the following ['FAM', 'NOD', 'NUM'] to the 'MAI' group

    **NOD** requires 'CGT' and 'NBR' attrs

    :param part:
    :param time_step:
    :param profile:
    :param families:
    :return:
    """

    def get_node_ids_from_element(el_):
        return [int(n.id) for n in el_.nodes]

    elements_group = time_step.create_group("MAI")
    elements_group.attrs.create("CGT", 1)
    for group, elements in part.fem.elements.group_by_type():
        if isinstance(group, (shape_def.MassTypes, shape_def.SpringTypes)):
            logger.warning("NotImplemented: Skipping Mass or Spring Elements")
            continue
        med_type = ada_to_med_type(group)
        elements = list(elements)
        cells = np.array(list(map(get_node_ids_from_element, elements)))

        med_cells = elements_group.create_group(med_type)
        med_cells.attrs.create("CGT", 1)
        med_cells.attrs.create("CGS", 1)
        med_cells.attrs.create("PFL", np.string_(profile))

        nod = med_cells.create_dataset("NOD", data=cells.flatten(order="F"))
        nod.attrs.create("CGT", 1)
        nod.attrs.create("NBR", len(cells))

        # Node Numbering is necessary for proper handling of
        num = med_cells.create_dataset("NUM", data=[int(el.id) for el in elements])
        num.attrs.create("CGT", 1)
        num.attrs.create("NBR", len(cells))

    # Add Element sets
    if len(part.fem.elsets.keys()) > 0:
        _add_cell_sets(elements_group, part, families)
