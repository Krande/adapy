from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.formats.sesam.write.write_utils import write_ff

if TYPE_CHECKING:
    from ada import FEM


def sets_str(fem: FEM, unwritten_elements: set | frozenset = frozenset()) -> str:
    """The TDSETNAM + GSETMEMB records of ``fem``'s sets.

    ``unwritten_elements`` are the ids of elements the deck leaves out
    (``write_elements.unwritten_element_ids``). An element set never names one: a GSETMEMB
    member with no GELMNT1 is an id a reader cannot resolve. A set left with no members by
    that is not written at all -- all it held is gone, and the writer reports each element.
    """
    out_str = ""

    i = 0
    for fs in fem.sets.sets:
        members = fs.members
        if fs.type == "elset" and unwritten_elements:
            members = [m for m in members if m.id not in unwritten_elements]
            if fs.members and not members:
                continue
        i += 1
        out_str += write_ff("TDSETNAM", [(4, i, 100 + len(fs.name), 0), (fs.name,)])
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
                out_str += write_ff(
                    "GSETMEMB", [(1024, i, card_idx, istype), (0, *mem_ids_local[:3]), *remainder_mem_ids]
                )
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
