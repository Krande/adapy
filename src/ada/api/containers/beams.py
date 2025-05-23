from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ada.api.beams import Beam
from ada.api.containers.base import IndexedCollection
from ada.core.utils import roundoff

if TYPE_CHECKING:
    pass


class Beams(IndexedCollection[Beam, str, int]):
    def __init__(self, beams: Iterable[Beam] = (), parent=None):
        super().__init__(
            items=beams,
            sort_key=lambda b: b.name,
            id_key=lambda b: b.guid,
            name_key=lambda b: b.name,
        )
        self._parent = parent

    def get_beams_within_volume(self, vol_, margins) -> Iterable[Beam]:
        """
        :param vol_: List or tuple of tuples [(xmin, xmax), (ymin, ymax), (zmin, zmax)]
        :param margins: Add margins to the volume box (equal in all directions). Input is in meters. Can be negative.
        :return: List of beam ids
        """
        from bisect import bisect_left, bisect_right

        if margins is not None:
            vol_new = []
            for p in vol_:
                vol_new.append((roundoff(p[0] - margins), roundoff(p[1] + margins)))
        else:
            vol_new = vol_
        vol = vol_new

        def sort_beams(bms):
            xkeys = [key[1] for key in bms]
            xmin = bisect_left(xkeys, vol[0][0])
            xmax = bisect_right(xkeys, vol[0][1])

            within_x_list = sorted(bms[xmin:xmax], key=lambda elem: elem[2])

            ykeys = [key[2] for key in within_x_list]
            ymin = bisect_left(ykeys, vol[1][0])
            ymax = bisect_right(ykeys, vol[1][1])

            within_y_list = sorted(within_x_list[ymin:ymax], key=lambda elem: elem[3])

            zkeys = [key[3] for key in within_y_list]
            zmin = bisect_left(zkeys, vol[2][0])
            zmax = bisect_right(zkeys, vol[2][1])

            within_vol_list = within_y_list[zmin:zmax]
            return [bm[0] for bm in within_vol_list]

        bm_list1 = [(bm.name, bm.n1.x, bm.n1.y, bm.n1.z) for bm in sorted(self._items, key=lambda bm: bm.n1.x)]
        bm_list2 = [(bm.name, bm.n2.x, bm.n2.y, bm.n2.z) for bm in sorted(self._items, key=lambda bm: bm.n2.x)]

        return set([self.from_name(bm_id) for bms_ in (bm_list1, bm_list2) for bm_id in sort_beams(bms_)])

    def add(self, beam: Beam) -> Beam:
        if beam.name is None:
            raise ValueError("Name may not be None")
        if beam.name in self._nmap:
            return self._nmap[beam.name]

        # any Beam-specific wiring…
        super().add(beam)
        return beam
