from typing import Union


def set_name(fem_set) -> str:
    """The name of an element's set, whether the element holds the set or only its name.

    Object elements carry their ``FemSet``; elements on the array-backed mesh (every deck the
    reader reads) carry the set's name, which is all a keyword line needs.
    """
    return fem_set if isinstance(fem_set, str) else fem_set.name


def get_instance_name(obj, written_on_assembly_level: bool) -> str:
    from ada import FEM, Assembly, Node, Part

    parent: Union[FEM, Part] = obj.parent
    p = parent.parent if type(parent) is FEM else parent
    obj_ref = obj.id if isinstance(obj, Node) else obj.name

    if type(p) is Assembly:
        obj_on_assembly_level = True
    else:
        obj_on_assembly_level = False

    if written_on_assembly_level is True and obj_on_assembly_level is False:
        if obj.parent is None:
            raise AttributeError
        return f"{obj.parent.instance_name}.{obj_ref}"
    else:
        return str(obj_ref)
