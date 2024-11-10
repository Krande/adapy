from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.formats.sesam.write.write_utils import write_ff

if TYPE_CHECKING:
    from ada import FEM


def sets_str(fem: FEM) -> str:
    out_str = ""

    for i, fs in enumerate(fem.sets.sets, start=1):
        out_str += write_ff("TDSETNAM", [(4, i, 100 + len(fs.name), 0), (fs.name,)])
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
