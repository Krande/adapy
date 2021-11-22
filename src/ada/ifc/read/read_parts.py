from ifcopenshell.util.element import get_psets

from ada import Assembly, Part

from ..concepts import IfcRef
from .reader_utils import get_parent


def read_hierarchy(f, a: Assembly, ifc_ref: IfcRef):
    for product in f.by_type("IfcProduct"):
        res, new_part = import_ifc_hierarchy(a, product, ifc_ref)
        if new_part is None:
            continue
        if res is None:
            a.add_part(new_part)
        elif type(res) is not Part:
            raise NotImplementedError()
        else:
            res.add_part(new_part)


def import_ifc_hierarchy(assembly: Assembly, product, ifc_ref: IfcRef):
    pr_type = product.is_a()
    pp = get_parent(product)
    if pp is None:
        return None, None
    props = get_psets(product)
    name = product.Name
    if pr_type not in [
        "IfcBuilding",
        "IfcSpace",
        "IfcBuildingStorey",
        "IfcSpatialZone",
    ]:
        return None, None

    new_part = Part(
        name,
        metadata=dict(original_name=name, props=props),
        guid=product.GlobalId,
        ifc_ref=ifc_ref,
        units=assembly.units,
    )
    res = assembly.get_by_name(pp.Name)
    return res, new_part
