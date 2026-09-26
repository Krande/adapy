from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger
from ada.fem.formats.sesam.write.write_utils import write_ff
from ada.fem.shapes.definitions import ConnectorTypes

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import FemSet

#: The TDSETNAM comment line naming the section assigned to a set. A section (``FemSection``)
#: has no Sesam card of its own: GELREF1 hands each element its material and geometry, and
#: TDSECT names a GEONO -- a cross-section or thickness that any number of property
#: assignments share, and that solids do not have at all (GELREF1: "not employed for
#: 3-dimensional elements"). What a section *is* in adapy is the assignment of those to an
#: element set, so its name goes on that set's TDSETNAM, as a comment line (CODTXT, manual
#: 4.2.6) -- the same place Abaqus keeps it (``** Section: <name>``). The reader
#: (``read_sections``) rebuilds the section on that set.
SECTION_TAG = "SECTION: "

#: The TDSETNAM comment line naming the constraint a set belongs to: the coupling (or rigid
#: body) whose dependent nodes it holds, or an equation it is a term of. BLDEP, which carries
#: them, is per dependent node and has no name; see ``read_constraints``.
CONSTRAINT_TAG = "CONSTRAINT: "

#: The TDNODE comment line (manual 4.2.4) naming the equation that makes one dof of the node
#: dependent: ``EQUATION: <dof> <name>``. An equation on node terms has no set to carry its
#: name, and one node may be the eliminated term of an equation per dof.
EQUATION_TAG = "EQUATION: "

#: NCTXT's legal range is [0, 64], NLTXT's [0, 5] (manual 4.2.6).
MAX_TEXT = 64
MAX_LINES = 5


def sets_str(fem: FEM, *others: FEM, unwritten_elements: set | frozenset = frozenset()) -> str:
    """TDSETNAM + GSETMEMB for every set of ``fem`` and then of ``others``.

    ``fem`` (and each of ``others``) is anything with ``.sets.sets``, and the ``sections`` and
    ``constraints`` whose names go on their sets' comment lines: the writer passes the part's
    sets together with the assembly's that stand on the part's mesh (``writer._SetsOf``).

    ``unwritten_elements`` are the ids of elements the deck leaves out
    (``write_elements.unwritten_element_ids``). An element set never names one: a GSETMEMB
    member with no GELMNT1 is an id a reader cannot resolve. A set left with no members by
    that is not written at all -- all it held is gone, and the writer reports each element.
    """
    fems = [fem] + [f for f in others if f is not None and f is not fem]
    # Keyed by what the set is called, not by the object: a constraint may reach its set
    # through a surface, which a multi-part merge carries over without re-pointing it at the
    # merged copy of the set.
    comments: dict[tuple, list[str]] = {}
    for f in fems:
        for sec in getattr(f, "sections", ()):
            comments.setdefault((sec.elset.type, sec.elset.name), []).append(f"{SECTION_TAG}{sec.name}")
        for con in getattr(f, "constraints", {}).values():
            for fs in _constraint_sets(con):
                comments.setdefault((fs.type, fs.name), []).append(f"{CONSTRAINT_TAG}{con.name}")
    out_str = ""
    i = 0
    for f in fems:
        for fs in f.sets.sets:
            members = fs.members
            if fs.type == "elset":
                members = [
                    m
                    for m in members
                    if m.id not in unwritten_elements and not isinstance(getattr(m, "type", None), ConnectorTypes)
                ]
                if fs.members and not members:
                    continue
            i += 1
            out_str += _set_str(fs, members, i, comments.get((fs.type, fs.name), []))
    return out_str


def _constraint_sets(con) -> list[FemSet]:
    """The sets whose TDSETNAM names ``con``: a coupling or rigid body's dependent-node set and
    reference-node set, each if it is one set; each node set an equation names as a term."""
    from ada.fem import FemSet

    if con.type == con.TYPES.EQUATION:
        return [ref for ref, _, _ in con.equation_terms or () if isinstance(ref, FemSet)]
    if con.type not in (con.TYPES.COUPLING, con.TYPES.RIGID_BODY):
        return []
    out = []
    for op in (con.s_set, con.m_set):
        fs = op if isinstance(op, FemSet) else getattr(op, "fem_set", None)
        if isinstance(fs, FemSet):
            out.append(fs)
    return out


def equation_names_str(*fems) -> str:
    """TDNODE records naming, on each eliminated node, the equations that eliminate its dofs
    (:data:`EQUATION_TAG`). A node set term stands for each of its nodes in turn
    (``write_constraints.equation_records``), so each of those nodes carries the name."""
    from ada.fem import FemSet

    names: dict[int, list[str]] = {}
    for f in fems:
        if f is None:
            continue
        for con in getattr(f, "constraints", {}).values():
            if con.type != con.TYPES.EQUATION or not con.equation_terms:
                continue
            ref, dof, _ = con.equation_terms[0]
            for node in ref.members if isinstance(ref, FemSet) else [ref]:
                names.setdefault(node.id, []).append(f"{EQUATION_TAG}{int(dof)} {con.name}")
    out = ""
    for nid, lines in names.items():
        kept = [c for c in lines if len(c) <= MAX_TEXT][:MAX_LINES]
        if len(kept) < len(lines):
            logger.warning(
                "sesam writer: node %s is eliminated by %s, of which only %s fit TDNODE's comment lines; "
                "the rest read back under generated names.",
                nid,
                lines,
                kept,
            )
        if not kept:
            continue
        width = max(len(c) for c in kept)
        # No name (NLNAM = 0), only the comment lines.
        out += write_ff("TDNODE", [(4, nid, 0, 100 * len(kept) + width)] + [(c.ljust(width),) for c in kept])
    return out


def _set_str(fs: FemSet, members: list, i: int, comments: list[str]) -> str:
    out_str = ""
    kept = [c for c in comments if len(c) <= MAX_TEXT][:MAX_LINES]
    if len(kept) < len(comments):
        logger.warning(
            "sesam writer: set %s carries %s, of which only %s fit TDSETNAM's comment lines "
            "(at most %s lines of %s characters); the rest read back under generated names.",
            fs.name,
            comments,
            kept,
            MAX_LINES,
            MAX_TEXT,
        )
    rows = [(4, i, 100 + len(fs.name), 0), (fs.name,)]
    if kept:
        # Every comment line of a record has the same length (NCTXT), so the shorter are padded.
        width = max(len(c) for c in kept)
        rows = [(4, i, 100 + len(fs.name), 100 * len(kept) + width), (fs.name,)]
        rows += [(c.ljust(width),) for c in kept]
    out_str += write_ff("TDSETNAM", rows)
    nfield = len(members) + 5
    mem_ids = [mem.id for mem in members]
    if fs.type == "elset":
        istype = 2
    else:
        istype = 1

    start = 0
    card_idx = 0
    if nfield > 1024:
        num_cards = int(nfield / 1019)
        for card_idx in range(1, num_cards + 1):
            mem_ids_local = mem_ids[start : card_idx * 1019]
            remainder_mem_ids = []
            for k in range(3, len(mem_ids_local), 4):
                remainder_mem_ids.append(mem_ids_local[k : k + 4])
            out_str += write_ff("GSETMEMB", [(1024, i, card_idx, istype), (0, *mem_ids_local[:3]), *remainder_mem_ids])
            start = card_idx * 1019

    card_idx += 1
    length = nfield - start
    if length < 4:
        out_str += write_ff("GSETMEMB", [(length, i, card_idx, istype), (0, *mem_ids[start : start + length])])
    else:
        mem_ids_local = mem_ids[start : start + length - 5]
        remainder_mem_ids = []
        for k in range(3, len(mem_ids_local), 4):
            remainder_mem_ids.append(mem_ids_local[k : k + 4])
        out_str += write_ff(
            "GSETMEMB", [(len(mem_ids_local) + 5, i, card_idx, istype), (0, *mem_ids_local[:3]), *remainder_mem_ids]
        )

    return out_str
