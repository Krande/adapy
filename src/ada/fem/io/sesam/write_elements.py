from typing import List, Tuple

from ada import FEM
from ada.fem import Elem

from .write_utils import write_ff


def elem_str(fem: FEM, thick_map) -> str:
    """
    'GELREF1',  ('elno', 'matno', 'addno', 'intno'), ('mintno', 'strano', 'streno', 'strepono'), ('geono', 'fixno',
            'eccno', 'transno'), 'members|'

    'GELMNT1', 'elnox', 'elno', 'eltyp', 'eltyad', 'nids'
    """
    from .reader import eltype_2_sesam

    out_str = "".join(
        [
            write_ff(
                "GELMNT1",
                [(el.id, el.id, eltype_2_sesam(el.type), 0)] + write_nodal_data(el),
            )
            for el in fem.elements
        ]
    )

    for el in fem.elements:
        out_str += write_elem(el, thick_map)

    return out_str


def write_nodal_data(el: Elem) -> List[Tuple[int]]:
    if len(el.nodes) <= 4:
        return [tuple([e.id for e in el.nodes])]

    nodes = []
    curr_tup = []
    counter = 0
    for n in el.nodes:
        curr_tup.append(n.id)
        counter += 1
        if counter == 4:
            counter = 0
            nodes.append(tuple(curr_tup))
            curr_tup = []

    return nodes + [tuple(curr_tup)]


def write_elem(el: Elem, thick_map) -> str:
    from ada.fem.elements import ElemType

    fem_sec = el.fem_sec
    if fem_sec.type == ElemType.LINE:
        sec_id = fem_sec.section.metadata["numid"]
    elif fem_sec.type == ElemType.SHELL:
        sec_id = thick_map[fem_sec.thickness]
    else:
        raise ValueError(f'Unsupported elem type "{fem_sec.type}"')

    fixno = el.metadata.get("fixno", None)
    transno = el.metadata.get("transno")
    if fixno is None:
        last_tuples = [(sec_id, 0, 0, transno)]
    else:
        h1_fix, h2_fix = fixno
        last_tuples = [(sec_id, -1, 0, transno), (h1_fix, h2_fix)]

    return write_ff(
        "GELREF1",
        [
            (el.id, el.fem_sec.material.id, 0, 0),
            (0, 0, 0, 0),
        ]
        + last_tuples,
    )
