from ..common import sesam_el_map


def sesam_eltype_2_general(eltyp: int) -> str:
    """Converts the numeric definition of elements in Sesam to a generalized element type form (ie. B31, S4, etc..)"""
    res = sesam_el_map.get(eltyp, None)
    if res is None:
        raise Exception("Currently unsupported eltype", eltyp)
    return res
