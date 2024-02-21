import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, Iterable, List, Union

import numpy as np

from ada import Beam, Part, Section
from ada.api.connections import JointBase
from ada.api.containers import Connections
from ada.core.vector_utils import vector_length


@dataclass
class GenieJoint(JointBase):
    def __init__(self, name, chords, braces, parent: Connections):
        self.chords = chords
        self.braces = braces

        members = chords + braces
        self.eval_members(members)
        center = self.centre
        super(GenieJoint, self).__init__(name, members, center, parent)

    def _init_check(self, members):
        return None

    def contains_stiffener(self):
        chord_sec_types = [bm.section.type for bm in self.beams]
        return True if Section.TYPES.ANGULAR in chord_sec_types else False

    def eval_members(self, members) -> None:
        node_map = dict()
        len_map = dict()
        center = np.array([0, 0, 0], dtype=float)
        for mem in members:
            n1 = mem.n1
            n2 = mem.n2
            center += n1.p
            center += n2.p
            vlen = vector_length(n2.p - n1.p)
            if vlen > 10:
                continue
            len_map[mem] = vlen
            if n1 not in node_map.keys():
                node_map[n1] = []
            if n2 not in node_map.keys():
                node_map[n2] = []
            node_map[n1].append(mem)
            node_map[n2].append(mem)
        center_val = center / (2 * len(members))
        nodes = [(n, items) for n, items in node_map.items() if len(items) > 2]
        if len(nodes) == 0 or len(nodes) > 1:
            # TODO: Should consider multiple segments of same beam and find closest segment to center_val
            raise NotImplementedError(f'Joint @"{center_val}" error. Multiple or zero central nodes found')
        self._centre, self._stubs = nodes[0]

    @property
    def stubs(self) -> List[Beam]:
        return self._stubs

    @property
    def chord_stubs(self) -> List[Beam]:
        return list(filter(lambda x: x in self.stubs, self.chords))

    @property
    def brace_stubs(self) -> List[Beam]:
        return list(filter(lambda x: x in self.stubs, self.braces))

    @property
    def stub_weight(self) -> float:
        weight_sum = 0
        for bm in self.stubs:
            weight_sum += bm.length * bm.section.properties.Ax * bm.material.model.rho
        return weight_sum


def get_bm(el: ET.Element, part):
    ref = el.attrib["beam_ref"]
    if ref not in part.beams.idmap.keys():
        return None
    i = 2
    potential_beams = [part.beams.from_name(ref)]
    suffix = "_E{}"
    eval_name = ref + suffix.format(i)
    while eval_name in part.beams.idmap.keys():
        potential_beams.append(part.beams.from_name(eval_name))
        i += 1
        eval_name = ref + suffix.format(i)

    return potential_beams


def get_joint(type_tag: ET.Element, con: Connections, part: Part) -> Union[None, GenieJoint]:
    from itertools import chain

    def not_none(x):
        return x is not None

    name = type_tag.attrib["name"]
    chords = list(filter(not_none, chain.from_iterable(get_bm(chord, part) for chord in type_tag.findall("chords/"))))
    braces = list(filter(not_none, chain.from_iterable(get_bm(elem, part) for elem in type_tag.findall("braces/"))))
    if str in [type(x) for x in braces] or str in [type(x) for x in chords]:
        raise ValueError()
    if len(chords) == 0 and len(braces) == 0:
        return None
    return GenieJoint(name, chords, braces, con)


def get_joints(xml_root, part: Part) -> Connections:
    con = Connections(parent=part)
    joints = (get_joint(type_tag, con, part) for type_tag in xml_root.findall(".//frame_joint"))
    con.connections = filter_joints(joints)
    return con


def filter_joints(connections: Iterable[Union[None, GenieJoint]]) -> List[GenieJoint]:
    centers: Dict[tuple, GenieJoint] = dict()
    new_connections: List[GenieJoint] = []
    for con in connections:
        if con is None:
            continue
        con: GenieJoint
        c = tuple(con.centre)
        if c in centers.keys():
            raise NotImplementedError("How to merge coincident joints is not yet implemented")
        centers[c] = con
        new_connections.append(con)
    return new_connections
