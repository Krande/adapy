from __future__ import annotations

import xml.etree.ElementTree as ET


def add_local_system(xdir, ydir, zdir) -> ET.Element:
    local_system_elem = ET.Element("local_system")
    d = ["x", "y", "z"]
    for j, vec in enumerate([xdir, ydir, zdir]):
        props = {d[i]: str(k) for i, k in enumerate(vec)}
        props.update(dict(dir=d[j]))
        vec_elem = ET.Element("vector", props)
        local_system_elem.append(vec_elem)

    return local_system_elem
