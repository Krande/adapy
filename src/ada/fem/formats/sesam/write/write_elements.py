from typing import List, Tuple

from ada import FEM
from ada.config import logger
from ada.fem import Elem
from ada.fem.shapes.definitions import ConnectorTypes

from ..common import sesam_el_map
from .write_utils import write_ff


def eltype_2_sesam(eltyp) -> int:
    for ses, gen in sesam_el_map.items():
        if eltyp == gen:
            return ses

    raise Exception("Currently unsupported eltype", eltyp)


def _is_writable_to_sesam(el: Elem) -> bool:
    """Skip-list gate for elements the Sesam writer can't faithfully
    emit yet. Two reasons land an element here:

    * **CONNECTOR**: ada represents it as a topology-only 2-noded
      element with no stiffness matrix. Sesam's nearest match
      (GLSH, element type 40) requires a 12×12 stiffness via an
      MSHGLSP record that we don't have data for. Emitting a
      GELMNT1 with eltyp=40 without the matching MSHGLSP would
      produce a Sestra-incomplete deck — skipping is the honest
      choice. Cross-format roundtrip uses an MPC / kinematic-
      coupling representation instead (see the internal notes).

    * **Unsectioned elements** (``fem_sec is None``): the Sesam
      writer's GELREF1 emitter needs a section / material binding
      to produce a valid record. Cross-format sources (Abaqus
      ``.inp`` with mesh-only sections, Code_Aster MED mesh
      without analysis spec) sometimes ship elements that haven't
      been bound to a FemSection at all. Emit nothing for those —
      the user gets a clear warning and a partial deck rather
      than a writer crash.
    """
    if isinstance(el.type, ConnectorTypes):
        return False
    if el.fem_sec is None:
        return False
    return True


def elem_str(fem: FEM, thick_map) -> str:
    """
    'GELREF1',  ('elno', 'matno', 'addno', 'intno'), ('mintno', 'strano', 'streno', 'strepono'), ('geono', 'fixno',
            'eccno', 'transno'), 'members|'

    'GELMNT1', 'elnox', 'elno', 'eltyp', 'eltyad', 'nids'
    """

    writable: list[Elem] = []
    skipped_connector = 0
    skipped_unsectioned = 0
    for el in fem.elements.stru_elements:
        if isinstance(el.type, ConnectorTypes):
            skipped_connector += 1
            continue
        if el.fem_sec is None:
            skipped_unsectioned += 1
            continue
        writable.append(el)
    if skipped_connector > 0:
        logger.warning(
            "sesam writer: skipping %d CONNECTOR element(s) — Sesam GLSH "
            "needs a stiffness matrix that ada doesn't carry on the "
            "topology-only Elem instance. Output deck will be missing "
            "these elements.",
            skipped_connector,
        )
    if skipped_unsectioned > 0:
        logger.warning(
            "sesam writer: skipping %d unsectioned element(s) — "
            "GELREF1 needs a FemSection binding (material / section "
            "id) to write a valid record. Bind the elements to a "
            "FemSection on the ada side, or accept a partial deck.",
            skipped_unsectioned,
        )

    out_str = "".join(
        [
            write_ff(
                "GELMNT1",
                [(el.id, el.id, eltype_2_sesam(el.type), 0)] + write_nodal_data(el),
            )
            for el in writable
        ]
    )

    for el in writable:
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
        sec_id = fem_sec.section.id
    elif fem_sec.type == ElemType.SHELL:
        sec_id = thick_map[fem_sec.thickness]
    elif fem_sec.type == ElemType.SOLID:
        # 3-D solid elements (IHEX / LHEX / ITET / TETR / IPRI /
        # TPRI) don't carry a geometric cross-section, so GELREF1's
        # ``geono`` is set to 0. The element still references its
        # material via the standard ``matno`` field a few lines
        # below — that's emitted unconditionally. Sesam's MORSSOL /
        # MTRSOL records define the solid material's orientation;
        # those are absent today and the analysis program assumes a
        # default isotropic alignment. That's a real coverage gap
        # for anisotropic solids; flagged in the internal notes.
        sec_id = 0
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
