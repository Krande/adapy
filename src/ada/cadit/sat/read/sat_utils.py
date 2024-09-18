from __future__ import annotations

from ada.cadit.sat.exceptions import ACISReferenceDataError
from ada.cadit.sat.read.sat_entities import AcisSubType


def get_ref_type(sub_type: AcisSubType) -> AcisSubType:
    max_recursion = 5
    i = 0
    while True:
        try:
            ref_sub_type = sub_type.parent_record.sat_store.get_ref(sub_type.chunks[1])
        except KeyError:
            raise ACISReferenceDataError(f"Reference data for {sub_type.chunks[1]} not found")
        if ref_sub_type.type != "ref":
            break
        else:
            sub_type = ref_sub_type
        i += 1
        if i > max_recursion:
            raise ACISReferenceDataError("Max recursion limit reached")
    sub_type = ref_sub_type
    return sub_type
