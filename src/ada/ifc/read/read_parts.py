from ada import Assembly, Part

from .reader_utils import get_name, get_parent, getIfcPropertySets


def read_hierarchy(f, a: Assembly):
    for product in f.by_type("IfcProduct"):
        res, new_part = import_ifc_hierarchy(a, product)
        if new_part is None:
            continue
        if res is None:
            a.add_part(new_part)
        elif type(res) is not Part:
            raise NotImplementedError()
        else:
            res.add_part(new_part)


def import_ifc_hierarchy(assembly, product):
    pr_type = product.is_a()
    pp = get_parent(product)
    if pp is None:
        return None, None
    name = get_name(product)
    if pr_type not in [
        "IfcBuilding",
        "IfcSpace",
        "IfcBuildingStorey",
        "IfcSpatialZone",
    ]:
        return None, None
    props = getIfcPropertySets(product)
    new_part = Part(name, ifc_elem=product, metadata=dict(original_name=name, props=props))
    res = assembly.get_by_name(pp.Name)
    return res, new_part