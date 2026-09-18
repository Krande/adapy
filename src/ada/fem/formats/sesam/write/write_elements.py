from typing import Iterator, List, Tuple

from ada import FEM
from ada.config import logger
from ada.fem import Elem
from ada.fem.shapes.definitions import ConnectorTypes

from ..common import sesam_el_map
from ..node_order import SESAM_ORDER
from .write_utils import write_ff

# Reverse of ``sesam_el_map``, built once rather than re-scanned per element. Several
# Sesam codes map to the same general shape (15 and 2 are both LINE); reversing the
# iteration keeps the first-listed code winning.
_gen_2_sesam: dict = {gen: ses for ses, gen in reversed(list(sesam_el_map.items()))}


def eltype_2_sesam(eltyp) -> int:
    ses = _gen_2_sesam.get(eltyp)
    if ses is None:
        raise Exception("Currently unsupported eltype", eltyp)
    return ses


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


def elem_gen(fem: FEM, thick_map) -> Iterator[str]:
    """
    'GELREF1',  ('elno', 'matno', 'addno', 'intno'), ('mintno', 'strano', 'streno', 'strepono'), ('geono', 'fixno',
            'eccno', 'transno'), 'members|'

    'GELMNT1', 'elnox', 'elno', 'eltyp', 'eltyad', 'nids'
    """

    writable: list[Elem] = []
    skipped_connector = 0
    skipped_unsectioned = 0
    # stru_elements already holds springs back, so nothing below would ever mention
    # them. Say so rather than let them vanish: the reader builds Spring objects off
    # GELMNT1 eltyp 18/40 (see sesam_el_map), so a Sesam -> Sesam round trip of a deck
    # with springs loses them here, and silence makes that look like the deck never
    # had any.
    n_springs = sum(1 for _ in fem.elements.springs)
    if n_springs > 0:
        logger.warning(
            "sesam writer: skipping %d spring element(s) — writing them back needs a "
            "GELMNT1 + MGSPRNG pair the writer does not emit yet. Output deck will be "
            "missing these elements.",
            n_springs,
        )
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

    # Yield record by record so the caller can stream: accumulating into one string
    # re-grew a deck-sized buffer per element, and held the whole element block in
    # memory on top of the mesh.
    by_block = _sesam_ordered_ids(fem)
    for el in writable:
        ids = by_block.get(el.type)
        row = getattr(el, "_row", None)
        if ids is not None and row is not None:
            nids = ids[row].tolist()
        else:
            nids = [n.id for n in SESAM_ORDER.nodes_to_format(el.type, list(el.nodes))]
        yield write_ff("GELMNT1", [(el.id, el.id, eltype_2_sesam(el.type), 0)] + _chunk_nodal_data(nids))
    for el in writable:
        yield write_elem(el, thick_map)


def _sesam_ordered_ids(fem: FEM) -> dict:
    """Node ids in Sesam's ordering, one ``(m, k)`` array per element block.

    On the array-backed mesh the reorder is a single gather per block --
    ``node_ids[conn[:, perm]]`` -- rather than a permutation per element. One array
    per block also keeps this proportional to the mesh rather than to the deck, so
    it doesn't undo the writer's streaming. Empty on the object mesh path, where
    the caller reorders each element's node list instead.
    """
    store = getattr(fem.elements, "store", None)
    if store is None:
        return {}
    return {ctype: store.node_ids[SESAM_ORDER.conn_to_format(ctype, blk.conn)] for ctype, blk in store.blocks.items()}


def elem_str(fem: FEM, thick_map) -> str:
    return "".join(elem_gen(fem, thick_map))


def write_nodal_data(el: Elem) -> List[Tuple[int]]:
    """GELMNT1's node ids for one element, in Sesam ordering, four per record line."""
    nodes = SESAM_ORDER.nodes_to_format(el.type, list(el.nodes))
    return _chunk_nodal_data([n.id for n in nodes])


def _chunk_nodal_data(node_ids) -> List[Tuple[int]]:
    """Group node ids into the 4-per-line tuples a GELMNT1 record is written in."""
    return [tuple(node_ids[i : i + 4]) for i in range(0, len(node_ids), 4)]


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
