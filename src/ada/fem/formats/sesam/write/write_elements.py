from typing import Dict, Iterator, List, Tuple

from ada import FEM
from ada.config import logger
from ada.fem import Elem
from ada.fem.shapes.definitions import (
    ConnectorTypes,
    ElemShapeTypes,
    MassTypes,
    SpringTypes,
)

from ..common import sesam_el_map
from ..node_order import SESAM_ORDER
from .not_held import STAGE, report
from .write_springs import element_records
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


#: How many of the missing internal element numbers the contiguity warning names before it
#: stops; enough to locate the first hole in the deck without printing a second mesh.
_N_GAPS_REPORTED = 5


def _missing_ids(sorted_ids: List[int], limit: int) -> Tuple[List[int], int]:
    """The first ``limit`` internal element numbers missing from ``sorted_ids``, and how
    many are missing in total.

    ``sorted_ids`` is sorted and duplicate-free (``elem_gen`` has just proved both), so
    the total is ``max(ids) - len(ids)`` given the numbering is meant to run 1..N -- no
    need to materialise the full expected range, which on a model whose ids start high
    would be far larger than the mesh. The listed examples come from a single walk that
    stops as soon as ``limit`` of them are known.
    """
    if not sorted_ids:
        return [], 0
    n_missing = sorted_ids[-1] - len(sorted_ids)
    if n_missing <= 0:
        return [], 0

    examples: List[int] = []
    expected = 1
    for eid in sorted_ids:
        while expected < eid and len(examples) < limit:
            examples.append(expected)
            expected += 1
        if len(examples) >= limit:
            break
        expected = eid + 1
    return examples, n_missing


def _warn_if_not_contiguous(el_ids: List[int], n_skipped: int) -> None:
    """Warn once when the emitted GELMNT1/GELREF1 ids are not contiguous from 1.

    The writer uses the model's own element ids as Sesam internal element numbers so the
    correspondence survives the conversion, and it must not renumber. But Sesam's internal
    element numbering is expected to run 1..N without holes, and a reader that assumes it
    -- associating the n-th result with the n-th element -- mis-associates results across a
    gap. The skip warnings above each say what was dropped; this says what dropping it did
    to the deck, in the same message, so the consequence can't be missed by someone who
    only reads the last warning.
    """
    examples, n_missing = _missing_ids(el_ids, _N_GAPS_REPORTED)
    if n_missing == 0:
        return

    shown = ", ".join(str(i) for i in examples)
    if n_missing > len(examples):
        shown += ", ..."
    logger.warning(
        "sesam writer: the deck's internal element numbering is not contiguous from 1 -- "
        "%d element number(s) are missing (first: %s) because %d element(s) were skipped "
        "(see the warnings above). The element ids are kept as the model's own rather than "
        "renumbered, so a reader that assumes contiguous internal numbering will "
        "mis-associate results.",
        n_missing,
        shown or "n/a",
        n_skipped,
    )


def is_spring(el) -> bool:
    return el.type in ElemShapeTypes.springs


def partition_elements(fem: FEM) -> Tuple[List[Elem], Dict[str, List[Elem]]]:
    """Split ``fem``'s structural elements into the ones the deck writes and the ones it
    cannot, the latter keyed by why.

    Shared by :func:`elem_gen`, which writes the first and reports the second, and by
    ``writer.to_fem``, which keeps the set block from naming an element that is not in
    the deck (a GSETMEMB member with no GELMNT1 is an id the reader cannot resolve).

    * ``"connector"``: ada represents a connector as a topology-only 2-noded element with
      no stiffness matrix. Sesam's nearest match (GLSH, element type 40) requires a 12×12
      stiffness via an MSHGLSP record that we don't have data for, and a GELMNT1 without
      it would be a Sestra-incomplete deck.
    * ``"unsectioned"``: GELREF1 needs a section / material binding to produce a valid
      record. Cross-format sources (Abaqus mesh-only decks, Code_Aster MED meshes)
      sometimes ship elements that were never bound to a FemSection.
    * ``"unsupported"``: a shape with no Sesam element type (section 5, table 5.1), e.g.
      the 7-node triangle and the 5-node pyramid.
    """
    writable: List[Elem] = []
    # ``stru_elements`` holds ``Connector`` objects back; they are left out all the same,
    # and a connector set must not name them.
    skipped: Dict[str, List[Elem]] = {"connector": list(fem.elements.connectors), "unsectioned": [], "unsupported": []}
    for el in fem.elements.stru_elements:
        if isinstance(el.type, (MassTypes, SpringTypes)):
            # A packed mass or spring row seen through its block rather than as its object
            # (a merged multi-part model has these): BNMASS writes the mass, and the spring
            # is reported with the others.
            continue
        if isinstance(el.type, ConnectorTypes):
            skipped["connector"].append(el)
        elif el.type not in _gen_2_sesam:
            skipped["unsupported"].append(el)
        elif el.fem_sec is None:
            skipped["unsectioned"].append(el)
        else:
            writable.append(el)
    return writable, skipped


def unwritten_element_ids(fem: FEM) -> set:
    """Ids of the elements :func:`elem_gen` leaves out of the deck."""
    _, skipped = partition_elements(fem)
    return {el.id for els in skipped.values() for el in els}


def _report_skipped(fem: FEM, skipped: Dict[str, List[Elem]]) -> int:
    """Name every element the deck leaves out; return how many that is."""
    rep = report()
    for el in skipped["connector"]:
        rep.omitted(STAGE, "Connector", el.name, "a connector has no Sesam element (GLSH needs a stiffness matrix)")
    for el in skipped["unsectioned"]:
        rep.omitted(STAGE, "Element", str(el.id), "no section: GELREF1 needs a material and section binding")
    for el in skipped["unsupported"]:
        rep.omitted(STAGE, "Element", str(el.id), f"{el.type} has no Sesam element type", type=str(el.type))
    return sum(len(els) for els in skipped.values())


def elem_gen(fem: FEM, thick_map, spring_matnos: dict | None = None) -> Iterator[str]:
    """
    'GELREF1',  ('elno', 'matno', 'addno', 'intno'), ('mintno', 'strano', 'streno', 'strepono'), ('geono', 'fixno',
            'eccno', 'transno'), 'members|'

    'GELMNT1', 'elnox', 'elno', 'eltyp', 'eltyad', 'nids'
    """

    writable, skipped = partition_elements(fem)
    n_skipped = _report_skipped(fem, skipped)

    # Presel requires GELMNT1/GELREF1 in internal element order (ELNO, manual printed
    # 6-65 and 6-68); the array-backed container iterates block by block, so a mixed
    # mesh interleaves the ids and Presel aborts with "ELEMENT n INTERNAL ALREADY
    # EXISTS" the first time the sequence steps backwards. Sort rather than renumber —
    # the ids the model carries are the ids the deck has to use — and note that sorting
    # does not fill gaps left by the skipped elements above; see the contiguity check
    # just below, which reports those gaps rather than renumbering around them.
    # Springs take their place in that order too (write_springs): GELMNT1 type 18/40 with a
    # GELREF1 naming their stiffness record.
    springs = list(fem.elements.springs) if spring_matnos else []
    writable = writable + springs
    el_ids = [el.id for el in writable]
    order = sorted(range(len(writable)), key=el_ids.__getitem__)
    writable = [writable[i] for i in order]
    el_ids = [el_ids[i] for i in order]
    dupes = [b for a, b in zip(el_ids, el_ids[1:]) if a == b]
    if dupes:
        raise ValueError(f'Doubly defined element id "{dupes[0]}"')  # mirrors nodes_gen
    spring_records = {sp.id: element_records(sp, spring_matnos[sp.id]) for sp in springs}

    _warn_if_not_contiguous(el_ids, n_skipped)

    # Yield record by record so the caller can stream: accumulating into one string
    # re-grew a deck-sized buffer per element, and held the whole element block in
    # memory on top of the mesh.
    by_block = _sesam_ordered_ids(fem)
    for el in writable:
        if el.id in spring_records and is_spring(el):
            yield spring_records[el.id][0]
            continue
        ids = by_block.get(el.type)
        row = getattr(el, "_row", None)
        if ids is not None and row is not None:
            nids = ids[row].tolist()
        else:
            nids = [n.id for n in SESAM_ORDER.nodes_to_format(el.type, list(el.nodes))]
        yield write_ff("GELMNT1", [(el.id, el.id, eltype_2_sesam(el.type), 0)] + _chunk_nodal_data(nids))
    for el in writable:
        if el.id in spring_records and is_spring(el):
            yield spring_records[el.id][1]
            continue
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
    # GUNIVEC references are handed over by ``univec_str`` for beams only (element
    # types 2, 15 and 23 — manual printed 6-92). Everything else uses default element
    # axes, which GELREF1 expresses as TRANSNO = 0 (manual printed 6-67); writing a
    # GUNIVEC number on a shell produced Presel's "TRANSFORMATION NUMBER 1 DOES NOT
    # EXSIST". A ``transno`` a caller placed here on a non-beam element is a BNTRCOS
    # reference and is written as given; ada does not itself produce BNTRCOS records
    # today, nor does its Abaqus reader carry a section ``orientation=`` through.
    transno = el.metadata.get("transno")
    if transno is None:
        transno = 0
    # ``eccno`` is left here by ``eccen_str`` once it has written the GECCEN records:
    # absent when the element has no offset, an int when both ends share one vector,
    # and a per-node list when they differ.
    eccno = el.metadata.get("eccno", None)

    # GELREF1's OPT convention: a field is either a single value covering every node
    # of the element, or -1 with a per-node list moved into the record's tail. The
    # tail carries those lists in field order — geono, fixno, eccno, transno — so
    # fixno's list has to go in before eccno's, or a reader pairs them up wrong.
    members: list[int] = []
    if fixno is None:
        fixno_field = 0
    else:
        fixno_field = -1
        members += list(fixno)

    if eccno is None:
        eccno_field = 0
    elif isinstance(eccno, (list, tuple)):
        eccno_field = -1
        members += list(eccno)
    else:
        eccno_field = eccno

    last_tuples = [(sec_id, fixno_field, eccno_field, transno)]
    # Four fields per record line, the same chunking GELMNT1's node ids get.
    last_tuples += [tuple(members[i : i + 4]) for i in range(0, len(members), 4)]

    return write_ff(
        "GELREF1",
        [
            (el.id, el.fem_sec.material.id, 0, 0),
            (0, 0, 0, 0),
        ]
        + last_tuples,
    )
