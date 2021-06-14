import logging
import reprlib
from bisect import bisect_left, bisect_right
from itertools import chain
from operator import attrgetter

import numpy as np
import toolz
from pyquaternion import Quaternion

from ada.config import Settings

from .utils import Counter, points_in_cylinder, vector_length

__all__ = [
    "Nodes",
    "Beams",
    "Plates",
    "Connections",
    "Materials",
    "Sections",
]


class BaseCollections:
    """
    The Base class for all collections

    :param parent:
    """

    def __init__(self, parent):
        self._parent = parent


class Beams(BaseCollections):
    """
    A collections of Beam objects

    :param beams:
    :param unique_ids:
    :param parent:
    """

    def __init__(self, beams=None, unique_ids=True, parent=None):

        super().__init__(parent)
        beams = [] if beams is None else beams
        if unique_ids:
            beams = toolz.unique(beams, key=attrgetter("name"))
        self._beams = sorted(beams, key=attrgetter("name"))
        self._dmap = {n.name: n for n in self._beams}

    def __contains__(self, item):
        return item.guid in self._dmap.keys()

    def __len__(self):
        return len(self._beams)

    def __iter__(self):
        return iter(self._beams)

    def __getitem__(self, index):
        result = self._beams[index]
        return Beams(result) if isinstance(index, slice) else result

    def __eq__(self, other):
        if not isinstance(other, Beams):
            return NotImplemented
        return self._beams == other._beams

    def __ne__(self, other):
        if not isinstance(other, Beams):
            return NotImplemented
        return self._beams != other._beams

    def __add__(self, other):
        return Beams(chain(self._beams, other._beams))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Beams({rpr.repr(self._beams) if self._beams else ''})"

    def index(self, item):
        index = bisect_left(self._beams, item)
        if (index != len(self._beams)) and (self._beams[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def count(self, item):
        return int(item in self)

    def from_name(self, name):
        """
        Get beam from its name

        :param name: Beam name
        :return: Beam object
        """
        if name not in self._dmap.keys():
            raise ValueError(f'The beam "{name}" is not found')
        else:
            return self._dmap[name]

    def add(self, beam):
        """

        :param beam:
        :type beam: ada.Beam
        """
        if beam.name is None:
            raise Exception("Name is not allowed to be None.")

        if beam.name in self._dmap.keys():
            return self._dmap[beam.name]
        self._dmap[beam.name] = beam
        self._beams.append(beam)

    def get_beams_within_volume(self, vol_, margins=None):
        """
        :param vol_: List or tuple of tuples [(xmin, xmax), (ymin, ymax), (zmin, zmax)]
        :param margins: Add margins to the volume box (equal in all directions). Input is in meters. Can be negative.
        :return: List of beam ids
        """
        from bisect import bisect_left, bisect_right

        from .utils import roundoff

        if margins is not None:
            vol_new = []
            for p in vol_:
                vol_new.append((roundoff(p[0] - margins), roundoff(p[1] + margins)))
        else:
            vol_new = vol_
        vol = vol_new

        def sort_bms(bms):
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

        bm_list1 = [(bm.name, bm.n1.x, bm.n1.y, bm.n1.z) for bm in sorted(self._beams, key=lambda bm: bm.n1.x)]
        bm_list2 = [(bm.name, bm.n2.x, bm.n2.y, bm.n2.z) for bm in sorted(self._beams, key=lambda bm: bm.n2.x)]

        return list(set([self._dmap[bm_id] for bms_ in (bm_list1, bm_list2) for bm_id in sort_bms(bms_)]))

    @property
    def dmap(self):
        """

        :return: A dictionary of all beams {int(id1):node1, ..}
        :rtype: dict
        """
        return self._dmap


class Plates(BaseCollections):
    """
    Plate object collection

    :param plates:
    :param unique_ids:
    :param parent:
    """

    def __init__(self, plates=None, unique_ids=True, parent=None):

        plates = [] if plates is None else plates
        super().__init__(parent)

        if unique_ids:
            plates = toolz.unique(plates, key=attrgetter("name"))
        self._plates = sorted(plates, key=attrgetter("name"))
        self._dmap = {n.name: n for n in self._plates}

    def __contains__(self, item):
        return item.id in self._dmap.keys()

    def __len__(self):
        return len(self._plates)

    def __iter__(self):
        return iter(self._plates)

    def __getitem__(self, index):
        result = self._plates[index]
        return Materials(result) if isinstance(index, slice) else result

    def __eq__(self, other):
        if not isinstance(other, Plates):
            return NotImplemented
        return self._plates == other._plates

    def __ne__(self, other):
        if not isinstance(other, Plates):
            return NotImplemented
        return self._plates != other._plates

    def __add__(self, other):
        return Plates(chain(self._plates, other._plates))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Plates({rpr.repr(self._plates) if self._plates else ''})"

    def index(self, item):
        index = bisect_left(self._plates, item)
        if (index != len(self._plates)) and (self._plates[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def count(self, item):
        return int(item in self)

    def get_by_id(self, name):
        """
        Get plate by its name

        :param name: Name of plate
        :return: Plate object
        """
        if name not in self._dmap.keys():
            raise ValueError(f'The node id "{name}" is not found')
        else:
            return self._dmap[name]

    @property
    def dmap(self):
        """

        :return: A dictionary of all nodes {int(id1):node1, ..}
        :rtype: dict
        """
        return self._dmap

    def _add_node_to_global(self, pnt):
        """

        :param pnt:
        :type pnt: ada.Node
        """
        old_node = self._parent.nodes.add(pnt)
        if old_node is not None:
            pnt = old_node
        return pnt

    def add(self, plate):
        """

        :param plate:
        :type plate: ada.Plate
        """
        if plate.name is None:
            raise Exception("Name is not allowed to be None.")

        if plate.name in self._dmap.keys():
            return self._dmap[plate.name]
        mat = self._parent.materials.add(plate.material)
        if mat is not None:
            plate.material = mat

        self._plates.append(plate)


class Connections(BaseCollections):
    counter = Counter(1, "C")
    """
    Connections Collection.

    :param connections: List of connections
    :param parent: Parent object
    """

    def __init__(self, connections=None, parent=None):

        connections = [] if connections is None else connections
        super().__init__(parent)
        self._connections = connections
        self._dmap = {j.id: j for j in self._connections}

    def __contains__(self, item):
        return item.id in self._dmap.keys()

    def __len__(self):
        return len(self._connections)

    def __iter__(self):
        return iter(self._connections)

    def __getitem__(self, index):
        result = self._connections[index]
        return Materials(result) if isinstance(index, slice) else result

    def __eq__(self, other):
        if not isinstance(other, Beams):
            return NotImplemented
        return self._connections == other._beams

    def __ne__(self, other):
        if not isinstance(other, Beams):
            return NotImplemented
        return self._connections != other._beams

    def __add__(self, other):
        return Beams(chain(self._connections, other._beams))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Connections({rpr.repr(self._connections) if self._connections else ''})"

    def add(self, joint):
        """
        Add a joint

        :param joint:
        :type joint: ada.Connection
        """
        if joint.name is None:
            raise Exception("Name is not allowed to be None.")

        if joint.name in self._dmap.keys():
            return self._dmap[joint.name]

        self._connections.append(joint)

    def find(self, out_of_plane_tol=0.1, joint_func=None):
        """
        Find all connections between beams in all parts using a simple clash check.

        :param out_of_plane_tol:
        :param joint_func: Pass a function for mapping the generic Connection classes to a specific reinforced Joints
        """
        from ada import Beam, Connection
        from ada.core.utils import beam_cross_check

        ass = self._parent.get_assembly()
        bm_res = ass.beam_clash_check()

        point_tuples = []

        def are_beams_connected(beams):
            """

            :param beams: Tuple containing beam and list of beams found using clash check
            :return:
            """
            bm1 = beams[0]
            assert isinstance(bm1, Beam)
            cross_beams = dict()
            for bm2 in beams[1]:
                if bm1 == bm2:
                    continue
                assert isinstance(bm2, Beam)
                res = beam_cross_check(bm1, bm2, out_of_plane_tol)
                if res is None:
                    continue
                point, s, t = res
                self._eval_joint_ends(bm1, bm2, t, point)

                if point is not None:
                    tp = tuple(point)
                    if tp not in cross_beams.keys():
                        cross_beams[tp] = []
                    cross_beams[tp].append(bm2)

            for p, mem in cross_beams.items():
                if p in point_tuples:
                    continue
                point_tuples.append(p)
                if joint_func is not None:
                    joint = joint_func(next(self.counter), [bm1] + mem)
                    if joint is None:
                        continue
                else:
                    joint = Connection(next(self.counter), [bm1] + mem)

                bm1.connected_from.append(joint)
                for m in mem:
                    m.connected_to.append(joint)
                self.add(joint)

        list(map(are_beams_connected, bm_res))

    def _eval_joint_ends(self, bm1, bm2, t_, intersect_point):
        """
        Evaluate the  use AB_ to ensure that the node lands on the beam

        :param bm1:
        :param bm2:
        :param t_:
        :param intersect_point:
        :return:
        """

        from ada import Node

        def eval_node(ab, n1=True):
            n = Node(ab)
            n_old = self._parent.nodes.add(n)

            # TODO: Evaluate if the following is necessary!
            if n_old is not None:
                n = n_old
            if n.id not in [bm2.n1.id, bm2.n2.id]:
                if n1 is True:
                    bm2.n1 = n
                    bm2.n1.Free = False
                else:
                    bm2.n2 = n
                    bm2.n2.Free = False
            else:
                logging.debug("Midnode on n1")

        if t_ <= 0:
            eval_node(intersect_point, n1=True)
        elif t_ >= 1:
            eval_node(intersect_point, n1=False)
        else:
            logging.error('bm1 "{}", bm2 "{}", t: "{}"'.format(bm1.name, bm2.name, t_))


class Materials(BaseCollections):
    """
    Collection of materials

    :param materials:
    :param unique_ids:
    :param parent:
    """

    def __init__(self, materials=None, unique_ids=True, parent=None, units="m"):
        super().__init__(parent)
        self._materials = sorted(materials, key=attrgetter("name")) if materials is not None else []
        self._unique_ids = unique_ids
        self._dmap = {n.name: n for n in self._materials}
        self._idmap = {n.id: n for n in self._materials}
        self._units = units

    def __contains__(self, item):
        return item.name in self._dmap.keys()

    def __len__(self):
        return len(self._materials)

    def __iter__(self):
        return iter(self._materials)

    def __getitem__(self, index):
        # if index not in self._dmap.keys():
        #     raise ValueError(f'Material name "{index}" not found')
        result = self._materials[index]
        return Materials(result) if isinstance(index, slice) else result

    def __eq__(self, other):
        if not isinstance(other, Materials):
            return NotImplemented
        return self._materials == other._materials

    def __ne__(self, other):
        if not isinstance(other, Materials):
            return NotImplemented
        return self._materials != other._materials

    def __add__(self, other):
        return Materials(chain(self._materials, other._materials))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Materials({rpr.repr(self._materials) if self._materials else ''})"

    def index(self, item):
        """

        :param item:
        :type item: ada.Material
        :return:
        """
        return self._materials.index(item)

    def count(self, item):
        return int(item in self)

    def get_by_name(self, name):
        """

        :param name:
        :return:
        """
        if name not in self._dmap.keys():
            raise ValueError(f'The material name "{name}" is not found')
        else:
            return self._dmap[name]

    def get_by_id(self, mat_id):
        """

        :param mat_id:
        :return:
        """
        if mat_id not in self._idmap.keys():
            raise ValueError(f'The material id "{mat_id}" is not found')
        else:
            return self._idmap[mat_id]

    @property
    def dmap(self):
        """

        :return: A dictionary of all nodes {int(id1):node1, ..}
        :rtype: dict
        """
        return self._dmap

    @property
    def parent(self):
        """

        :return:
        :rtype: ada.Part
        """
        return self._parent

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if value != self._units:
            for m in self._materials:
                m.units = value
            self._units = value

    def add(self, material):
        """

        :param material:
        :type material: ada.Material
        """
        if material in self:
            return self._dmap[material.name]

        if material.id is None or material.id in self._idmap.keys():
            material.id = len(self._materials) + 1
        self._idmap[material.id] = material
        self._dmap[material.name] = material
        self._materials.append(material)


class Sections:
    """

    :param sections:
    :param unique_ids:
    :param parent:
    """

    def __init__(self, sections=None, unique_ids=True, parent=None):

        sections = [] if sections is None else sections
        self._parent = parent

        if unique_ids:
            sections = list(toolz.unique(sections, key=attrgetter("name")))
        self._sections = sorted(sections, key=attrgetter("name"))
        self._nmap = {n.name: n for n in self._sections}
        self._idmap = {n.id: n for n in self._sections}
        if len(self._nmap.keys()) != len(self._idmap.keys()):
            raise ValueError("Non-unique ids or name are observed..")

    def __contains__(self, item):
        return item.id in self._nmap.keys()

    def __len__(self):
        return len(self._sections)

    def __iter__(self):
        return iter(self._sections)

    def __getitem__(self, index):
        result = self._sections[index]
        return Sections(result) if isinstance(index, slice) else result

    def __add__(self, other):
        return Sections(chain(self._sections, other._sections))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Sections({rpr.repr(self._sections) if self._sections else ''})"

    def _change_units(self, scale_factor):
        for s in self._sections:
            s._change_units(scale_factor)

    def index(self, item):
        index = bisect_left(self._sections, item)
        if (index != len(self._sections)) and (self._sections[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def count(self, item):
        return int(item in self)

    def get_by_name(self, name):
        """

        :param name: Name of id
        :rtype: ada.Section
        """
        if name not in self._nmap.keys():
            raise ValueError(f'The section id "{name}" is not found')
        else:
            return self._nmap[name]

    def get_by_id(self, sec_id):
        """

        :param sec_id: Section ID
        :rtype: ada.Section
        """
        if sec_id not in self._idmap.keys():
            raise ValueError(f'The node id "{sec_id}" is not found')
        else:
            return self._idmap[sec_id]

    @property
    def nmap(self):
        """

        :return: A dictionary of all nodes {int(id1):node1, ..}
        :rtype: dict
        """
        return self._nmap

    @property
    def idmap(self):
        """

        :return: A dictionary of all nodes {name:node1, ..}
        :rtype: dict
        """
        return self._idmap

    def add(self, section):
        """

        :param section:
        :type section: ada.Section
        """
        if section.name is None:
            raise Exception("Name is not allowed to be None.")

        # Note: Evaluate if parent should be "Sections" not Part object?
        if section.parent is None:
            section.parent = self._parent

        if section.name in self._nmap.keys():
            return self._nmap[section.name]

        if section.id is None or section.id in self._idmap.keys():
            new_sec_id = len(self._sections) + 1
            section.edit(sec_id=new_sec_id)

        self._sections.append(section)
        self._idmap[section.id] = section
        self._nmap[section.name] = section


class Nodes:
    """

    :param nodes:
    :param unique_ids:
    :param parent:
    :param from_np_array:
    """

    def __init__(self, nodes=None, unique_ids=True, parent=None, from_np_array=None):
        self._parent = parent
        if from_np_array is not None:
            self._array = from_np_array
            nodes = self._np_array_to_nlist(from_np_array)
        else:
            nodes = [] if nodes is None else nodes

        if unique_ids is True:
            nodes = toolz.unique(nodes, key=attrgetter("id"))

        self._nodes = sorted(nodes, key=attrgetter("x", "y", "z"))
        self._idmap = {n.id: n for n in sorted(self._nodes, key=attrgetter("id"))}
        self._maxid = max(self._idmap.keys()) if len(self._nodes) > 0 else 0
        self._bbox = self._get_bbox() if len(self._nodes) > 0 else None

    def renumber(self):
        """
        Ensures that the node numberings starts at 1 and has no holes in its numbering.

        """
        for i, n in enumerate(sorted(self._nodes, key=attrgetter("id")), start=1):
            if i != n.id:
                n.id = i

        self._idmap = {n.id: n for n in sorted(self._nodes, key=attrgetter("id"))}
        self._maxid = max(self._idmap.keys()) if len(self._nodes) > 0 else 0
        self._bbox = self._get_bbox() if len(self._nodes) > 0 else None

    def _np_array_to_nlist(self, np_array):
        from ada import Node

        return [Node(row[1:], int(row[0]), parent=self._parent) for row in np_array]

    def nlist_to_np_array(self, nlist):
        return np.array([(n.id, *n) for n in nlist])

    def __contains__(self, item):
        return item.id in self._idmap.keys()

    def __len__(self):
        return len(self._nodes)

    def __iter__(self):
        return iter(self._nodes)

    def __getitem__(self, index):
        result = self._nodes[index]
        return Nodes(result) if isinstance(index, slice) else result

    def __eq__(self, other):
        if not isinstance(other, Nodes):
            return NotImplemented
        return self._nodes == other._nodes

    def __ne__(self, other):
        if not isinstance(other, Nodes):
            return NotImplemented
        return self._nodes != other._nodes

    def __add__(self, other):
        return Nodes(chain(self._nodes, other._nodes))

    def __repr__(self):
        return f"Nodes({len(self._nodes)}, max_id: {self.max_nid}, min_id: {self.min_nid})"

    def index(self, item):
        index = bisect_left(self._nodes, item)
        if (index != len(self._nodes)) and (self._nodes[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def count(self, item):
        return int(item in self)

    def move(self, move=None, rotate=None):
        """
        A method for translating and/or rotating your model.

        :param move: Translate Nodes by parsing a Numpy array containing dX,dY,dZ vector np.array([dx,dy,dz])
        :param rotate: Rotate Nodes around a specified axis before translation.
                       Input is a tuple([p1X, p1Y, p1Z], [p2X, p2Y, p2Z], degrees)
        """

        def moving(no):
            no.p = no.p + move

        def map_rotations(no, p):
            no.p = p

        if rotate is not None:
            p1 = np.array(rotate[0])
            p2 = np.array(rotate[1])
            deg = rotate[2]
            my_quaternion = Quaternion(axis=p2 - p1, degrees=deg)
            rot_mat = my_quaternion.rotation_matrix
            vectors = np.array([n.p - p1 for n in self._nodes])
            res = np.matmul(vectors, np.transpose(rot_mat))
            [map_rotations(n, p + p1) for n, p in zip(self._nodes, res)]

        if move is not None:
            move = np.array(move)
            list(map(moving, self._nodes))

        self._nodes = sorted(self._nodes, key=attrgetter("x", "y", "z"))

    def from_id(self, nid):
        """

        :param nid:
        :return:
        """
        if nid not in self._idmap.keys():
            raise ValueError(f'The node id "{nid}" is not found')
        else:
            return self._idmap[nid]

    def _get_bbox(self):
        if len(self._nodes) == 0:
            raise ValueError("No Nodes are found")
        nodes_yids = sorted(self._nodes, key=attrgetter("y"))
        nodes_zids = sorted(self._nodes, key=attrgetter("z"))
        xmin, xmax = self._nodes[0], self._nodes[-1]
        ymin, ymax = nodes_yids[0], nodes_yids[-1]
        zmin, zmax = nodes_zids[0], nodes_zids[-1]
        return (xmin, xmax), (ymin, ymax), (zmin, zmax)

    @property
    def dmap(self):
        """

        :return: A dictionary of all nodes {int(id1):node1, ..}
        :rtype: dict
        """
        return self._idmap

    @property
    def bbox(self):
        """

        :return:
        """
        if self._bbox is None:
            self._bbox = self._get_bbox()
        return self._bbox

    @property
    def vol_cog(self):
        return tuple([(self.bbox[i][0][i] + self.bbox[i][1][i]) / 2 for i in range(3)])

    @property
    def max_nid(self):
        return max(self.dmap.keys()) if len(self.dmap.keys()) > 0 else 0

    @property
    def min_nid(self):
        return min(self.dmap.keys()) if len(self.dmap.keys()) > 0 else 0

    def get_by_volume(self, p=None, vol_box=None, vol_cyl=None, tol=1e-4):
        """

        :param p: Point
        :param vol_box: Additional point to find nodes inside a rectangular box
        :param vol_cyl: (radius, height, cylinder thickness). Note! Radius is measured to outside of cylinder wall
        :param tol: Point tolerance
        :return:
        """
        from ada import Node

        p = np.array(p) if type(p) is (list, tuple) else p
        if p is not None and vol_cyl is None and vol_box is None:
            vol = [(coord - tol, coord + tol) for coord in p]
        elif vol_box is not None:
            vol = list(zip(p, vol_box))
        elif vol_cyl is not None and p is not None:
            r, h, t = vol_cyl
            vol = [
                (p[0] - r - tol, p[0] + r + tol),
                (p[1] - r - tol, p[1] + r + tol),
                (p[2] - tol, p[2] + tol + h),
            ]
        else:
            raise Exception("No valid search input provided. None is returned")

        vol_min, vol_max = zip(*vol)
        xmin = bisect_left(self._nodes, Node(vol_min))
        xmax = bisect_right(self._nodes, Node(vol_max))

        xlist = sorted(self._nodes[xmin:xmax], key=attrgetter("y"))
        ysorted = [n.y for n in xlist]
        ymin = bisect_left(ysorted, vol_min[1])
        ymax = bisect_right(ysorted, vol_max[1])

        ylist = sorted(xlist[ymin:ymax], key=attrgetter("z"))
        zsorted = [n.z for n in ylist]
        zmin = bisect_left(zsorted, vol_min[2])
        zmax = bisect_right(zsorted, vol_max[2])

        simplesearch = ylist[zmin:zmax]

        if vol_cyl is not None:
            r, h, t = vol_cyl
            pt1_ = p + np.array([0, 0, -h])
            pt2_ = p + np.array([0, 0, +h])

            def eval_p_in_cyl(no):
                if t == r:
                    if points_in_cylinder(pt1_, pt2_, r, no.p) is True:
                        return no
                else:
                    eval1 = points_in_cylinder(pt1_, pt2_, r + t, no.p)
                    eval2 = points_in_cylinder(pt1_, pt2_, r - t, no.p)
                    if eval1 is True and eval2 is False:
                        return no
                return None

            return list(filter(None, [eval_p_in_cyl(q) for q in simplesearch]))
        else:
            return list(simplesearch)

    def add(self, node, point_tol=Settings.point_tol, allow_coincident=False):
        """
        Insert node into sorted list.

        :param node: Node object
        :param point_tol: Point Tolerance
        :param allow_coincident: Disable check for coincident nodes
        :type node: ada.Node
        """

        def insert_node(n, i):
            new_id = self._maxid + 1 if len(self._nodes) > 0 else 1
            if n.id in self._idmap.keys() or n.id is None:
                n.id = new_id

            self._nodes.insert(i, n)
            self._idmap[n.id] = n
            self._bbox = None
            self._maxid = n.id if n.id > self._maxid else self._maxid
            # self._bbox = self._get_bbox()

        index = bisect_left(self._nodes, node)

        if (index != len(self._nodes)) and (self._nodes[index] == node) and allow_coincident is False:
            old_node = self._nodes[index]
            vlen = vector_length(old_node.p - node.p)
            if vlen < point_tol:
                logging.debug(f'Replaced new node with node id "{self._nodes[index].id}" found within point tolerances')
                return self._nodes[index]
            else:
                insert_node(node, index)
        else:
            insert_node(node, index)

        return None
