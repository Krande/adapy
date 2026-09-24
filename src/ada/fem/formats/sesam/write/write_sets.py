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

#: The TDSETNAM comment line naming the coupling (or rigid body) whose dependent nodes the set
#: holds. BLDEP, which carries it, is per dependent node and has no name; see
#: ``read_constraints``.
CONSTRAINT_TAG = "CONSTRAINT: "

#: NCTXT's legal range is [0, 64], NLTXT's [0, 5] (manual 4.2.6).
MAX_TEXT = 64
MAX_LINES = 5


def sets_str(fem: FEM, *others: FEM) -> str:
    """TDSETNAM + GSETMEMB for every set of ``fem`` and then of ``others``.

    A Sesam file is one superelement, so the sets of the assembly (passed in ``others``) are
    written into it too: they name the same nodes and elements. They used to be left out, which
    also left out the node sets assembly-level boundary conditions stand on.

    A connector is not written (``write_elements._is_writable_to_sesam``), so it is left out of
    the sets as well, and a set of connectors only is not written: a member that names an
    element the file does not have is one no reader can resolve.
    """
    fems = [fem] + [f for f in others if f is not None and f is not fem]
    # Keyed by what the set is called, not by the object: a constraint may reach its set
    # through a surface, which a multi-part merge carries over without re-pointing it at the
    # merged copy of the set.
    comments: dict[tuple, list[str]] = {}
    for f in fems:
        for sec in f.sections:
            comments.setdefault((sec.elset.type, sec.elset.name), []).append(f"{SECTION_TAG}{sec.name}")
        for con in f.constraints.values():
            fs = _bldep_slave_set(con)
            if fs is not None:
                comments.setdefault((fs.type, fs.name), []).append(f"{CONSTRAINT_TAG}{con.name}")
    out_str = ""
    i = 0
    for f in fems:
        for fs in f.sets.sets:
            members = [m for m in fs.members if not isinstance(getattr(m, "type", None), ConnectorTypes)]
            if fs.members and not members:
                continue
            i += 1
            out_str += _set_str(fs, members, i, comments.get((fs.type, fs.name), []))
    return out_str


def _bldep_slave_set(con) -> FemSet | None:
    """The set a coupling or rigid body's dependent nodes are written from, if it is one set."""
    from ada.fem import FemSet

    if con.type not in (con.TYPES.COUPLING, con.TYPES.RIGID_BODY):
        return None
    s_set = con.s_set
    if not isinstance(s_set, FemSet):
        s_set = getattr(s_set, "fem_set", None)
    return s_set if isinstance(s_set, FemSet) else None


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
