from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger
from ada.fem.formats.sesam.write.write_utils import write_ff

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

#: NCTXT's legal range is [0, 64] (manual 4.2.6).
MAX_TEXT = 64


def section_comment(name: str) -> str | None:
    text = f"{SECTION_TAG}{name}"
    return text if len(text) <= MAX_TEXT else None


def sets_str(fem: FEM, *others: FEM) -> str:
    """TDSETNAM + GSETMEMB for every set of ``fem`` and then of ``others``.

    A Sesam file is one superelement, so the sets of the assembly (passed in ``others``) are
    written into it too: they name the same nodes and elements. They used to be left out, which
    also left out the node sets assembly-level boundary conditions stand on.
    """
    fems = [fem] + [f for f in others if f is not None and f is not fem]
    section_names = {id(sec.elset): sec.name for f in fems for sec in f.sections}
    out_str = ""
    i = 0
    for f in fems:
        for fs in f.sets.sets:
            i += 1
            out_str += _set_str(fs, i, section_names.get(id(fs)))
    return out_str


def _set_str(fs: FemSet, i: int, section_name: str | None) -> str:
    out_str = ""
    rows = [(4, i, 100 + len(fs.name), 0), (fs.name,)]
    if section_name is not None:
        text = section_comment(section_name)
        if text is None:
            logger.warning(
                "sesam writer: the name of section %s is too long for a TDSETNAM comment (%s characters); "
                "it reads back under a generated name.",
                section_name,
                MAX_TEXT,
            )
        else:
            rows = [(4, i, 100 + len(fs.name), 100 + len(text)), (fs.name,), (text,)]
    out_str += write_ff("TDSETNAM", rows)
    nfield = len(fs.members) + 5
    mem_ids = [mem.id for mem in fs.members]
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
