from typing import Union

from ..grammar import render_keyword


def render_block(*args, **kwargs) -> str:
    """:func:`render_keyword` without its final newline, for writers that join blocks with ``"\\n"``."""
    return render_keyword(*args, **kwargs)[:-1]


def include_str(path: str) -> str:
    """``*INCLUDE,INPUT=<path>``, with no blank after the keyword: the layout the writer has always
    used (``bundle`` reads it back either way)."""
    return render_block("INCLUDE", [("INPUT", path)], sep=",")


def set_name(fem_set) -> str:
    """The name of an element's set, whether the element holds the set or only its name.

    Object elements carry their ``FemSet``; elements on the array-backed mesh (every deck the
    reader reads) carry the set's name, which is all a keyword line needs.
    """
    return fem_set if isinstance(fem_set, str) else fem_set.name


def is_connector_set(obj) -> bool:
    """A set of connector elements only. Connectors are written at assembly level (they may join
    instances), so such a set is too -- a part-level one would name elements the part does not
    define."""
    from ada.fem import Connector, FemSet

    if not isinstance(obj, FemSet):
        return False
    members = obj.members
    return bool(members) and all(isinstance(m, Connector) for m in members)


def get_instance_name(obj, written_on_assembly_level: bool) -> str:
    from ada import FEM, Assembly, Node, Part

    if is_connector_set(obj):
        # Written beside its connectors, at assembly level (writer.py), under its own name.
        return obj.name

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
