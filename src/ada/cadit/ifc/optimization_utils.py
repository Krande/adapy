from collections import deque

import ifcopenshell
import ifcopenshell.util.element
from toposort import toposort_flatten as toposort


def recycle_non_rooted(ifc_file: ifcopenshell.file) -> ifcopenshell.file:
    deleted = []
    hashes = {}
    for element in ifc_file:
        if element.is_a("IfcRoot"):
            continue
        h = hash(tuple(element))
        if h in hashes:
            for inverse in ifc_file.get_inverse(element):
                ifcopenshell.util.element.replace_attribute(inverse, element, hashes[h])
            deleted.append(element.id())
        else:
            hashes[h] = element
    deleted.sort()
    deleted_q = deque(deleted)
    new = ""
    for line in ifc_file.wrapped_data.to_string().split("\n"):
        try:
            if int(line.split("=")[0][1:]) != deleted_q[0]:
                new += line + "\n"
            else:
                deleted_q.popleft()
        except (ValueError, IndexError):
            new += line + "\n"
    return ifc_file.from_string(new)


def general_optimization(ifc_file: ifcopenshell.file) -> ifcopenshell.file:
    def generate_instances_and_references():
        """
        Generator which yields an entity id and
        the set of all of its references contained in its attributes.
        """
        for prod in ifc_file:
            yield prod.id(), set(i.id() for i in ifc_file.traverse(prod)[1:] if i.id())

    instance_mapping = {}
    optimized_file = ifcopenshell.file(schema=ifc_file.schema)

    def map_value(v):
        """
        Recursive function which replicates an entity instance, with
        its attributes, mapping references to already registered
        instances. Indeed, because of the toposort we know that
        forward attribute value instances are mapped before the instances
        that reference them.
        """
        if isinstance(v, (list, tuple)):
            # lists are recursively traversed
            return type(v)(map(map_value, v))
        elif isinstance(v, ifcopenshell.entity_instance):
            if v.id() == 0:
                # express simple types are not part of the toposort and just copied
                return optimized_file.create_entity(v.is_a(), v[0])

            return instance_mapping[v]
        else:
            # a plain python value can just be returned
            return v

    info_to_id = {}

    for ifc_id in toposort(dict(generate_instances_and_references())):
        inst = ifc_file[ifc_id]
        info = inst.get_info(include_identifier=False, recursive=True, return_type=frozenset)
        if info in info_to_id:
            _ = instance_mapping[inst] = instance_mapping[ifc_file[info_to_id[info]]]

        else:
            info_to_id[info] = ifc_id
            instance_mapping[inst] = optimized_file.create_entity(inst.is_a(), *map(map_value, inst))

    start_entities = len(list(ifc_file))
    end_entities = len(list(optimized_file))
    print(f"Optimized number of IFC entities from {start_entities} to {end_entities}")
    return optimized_file
