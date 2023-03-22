from __future__ import annotations

from typing import TYPE_CHECKING

from ada import Plate

if TYPE_CHECKING:
    from ada.sat.factory import SatStore


class PlateFactory:
    # Face row
    name_idx = 2
    loop_idx = 6

    # Loop row
    coedge_ref = 6

    def __init__(self, sat_store: SatStore):
        self.sat_store = sat_store

    def get_plate_from_face(self, face_data_str: str) -> Plate:
        res = face_data_str.strip().split()

        name = self.sat_store.get_name(res[self.name_idx])
        if not name.startswith("FACE"):
            raise NotImplementedError(f"Only face_refs starting with 'FACE' is supported. Found {name}")

        loop = self.sat_store.get(res[self.loop_idx]).split()
        coedge_start_id = loop[self.coedge_ref]
        coedge_first = self.sat_store.get(coedge_start_id)

        str(coedge_first[-3])

        # Coedge row

        return Plate()
