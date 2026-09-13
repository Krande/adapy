from itertools import chain

import numpy as np

from ada.config import logger
from ada.core.utils import roundoff
from ada.fem import FEM, Elem, FemSet, Mass, Spring
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.common import sesam_eltype_2_general
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes.lines import SpringTypes

from . import cards


def gelmnt_node_ids(nids: str) -> list[int]:
    """The node ids in a GELMNT1 NODIN field.

    NODIN is an array, and a record may carry more slots than its element type uses:
    a .SIN re-exported through its own input deck writes four values per line, so a
    one-noded MASS or SPRING1 arrives as ``58513 0 0 0``. Zero is Sesam's padding and
    never a node number, so dropping zeros leaves exactly the nodes the element
    references.
    """
    return [n for n in (str_to_int(x) for x in nids.split()) if n != 0]


def gelmnt_point_node_id(gelmnt: dict) -> int:
    """The single node a point element (MASS, SPRING1) sits on.

    Handing the whole NODIN field to ``str_to_int`` — which is ``int(float(s))`` — is
    what raised ``could not convert string to float: '5.85130000E+04 0.00000000E+00
    0.00000000E+00 0.00000000E+00'`` on every .SIN converted to xml or gnx.
    """
    node_ids = gelmnt_node_ids(gelmnt["nids"])
    if not node_ids:
        raise ValueError(f"GELMNT1 element {gelmnt.get('elno')} references no node (NODIN was {gelmnt['nids']!r})")

    return node_ids[0]


def get_elements(bulk_str: str, fem: FEM) -> tuple[FemElements, dict, dict, dict]:
    """Import elements from Sesam Bulk str"""

    mass_elem = dict()
    spring_elem = dict()
    internal_external_element_map = dict()

    def grab_elements(match):
        d = match.groupdict()
        el_no = str_to_int(d["elno"])
        el_nox = str_to_int(d["elnox"])
        internal_external_element_map[el_no] = el_nox
        nodes = [fem.nodes.from_id(x) for x in gelmnt_node_ids(d["nids"])]
        eltyp = d["eltyp"]
        el_type = sesam_eltype_2_general(str_to_int(eltyp))

        if isinstance(el_type, SpringTypes):
            spring_elem[el_no] = dict(gelmnt=d)
            return None

        metadata = dict(eltyad=str_to_int(d["eltyad"]), eltyp=eltyp)
        elem = Elem(el_no, nodes, el_type, None, parent=fem, metadata=metadata)

        if el_type == Elem.EL_TYPES.MASS_SHAPES.MASS:
            logger.warning("Mass element interpretation in sesam is undergoing changes. Results should be checked")
            mass_elem[el_no] = dict(gelmnt=d)
            fem.sets.add(FemSet(f"m{el_no}", [elem], FemSet.TYPES.ELSET, parent=fem))

        return elem

    elements = FemElements(
        filter(lambda x: x is not None, map(grab_elements, cards.GELMNT1.to_ff_re().finditer(bulk_str))), fem_obj=fem
    )
    return elements, mass_elem, spring_elem, internal_external_element_map


def get_elements_arrays(bulk_str: str):
    """Parse GELMNT1 into per-type connectivity (node ids) without building Elem
    objects. Mass/spring elements are split out for separate handling.

    Returns ``(by_type, mass_elem, spring_elem, ext_map)`` where ``by_type`` maps a
    canonical element type to ``(el_ids: list[int], conn: list[list[int]])`` of node
    ids."""
    from collections import defaultdict

    by_type: dict = defaultdict(lambda: ([], []))
    mass_elem: dict = {}
    spring_elem: dict = {}
    ext_map: dict = {}

    for match in cards.GELMNT1.to_ff_re().finditer(bulk_str):
        d = match.groupdict()
        el_no = str_to_int(d["elno"])
        ext_map[el_no] = str_to_int(d["elnox"])
        nids = gelmnt_node_ids(d["nids"])
        el_type = sesam_eltype_2_general(str_to_int(d["eltyp"]))
        if isinstance(el_type, SpringTypes):
            spring_elem[el_no] = dict(gelmnt=d)
            continue
        if el_type == Elem.EL_TYPES.MASS_SHAPES.MASS:
            mass_elem[el_no] = dict(gelmnt=d)
            continue
        el_ids, conns = by_type[el_type]
        el_ids.append(el_no)
        conns.append(nids)

    return by_type, mass_elem, spring_elem, ext_map


def get_mass(bulk_str: str, fem: FEM, mass_elem: dict, renumber_map: dict | None = None) -> FemElements:
    # Generated BNMASS element ids must clear BOTH the current (internal) element
    # numbering AND the external ids structural elements will be renumbered into
    # (renumber_map values) later — otherwise a generated mass id collides with a
    # renumbered structural element. (Pre-existing: this bit both the object and array
    # paths on decks whose external numbering reuses the internal-max+1 range.)
    max_external = max(renumber_map.values()) if renumber_map else 0

    def checkEqual2(iterator):
        return len(set(iterator)) <= 1

    def find_bnmass(match) -> Mass:
        d = match.groupdict()

        nodeno = str_to_int(d["nodeno"])
        mass_in = [
            roundoff(d["m1"]),
            roundoff(d["m2"]),
            roundoff(d["m3"]),
            roundoff(d["m4"]),
            roundoff(d["m5"]),
            roundoff(d["m6"]),
        ]
        masses = [m for m in mass_in if m != 0.0]
        if checkEqual2(masses):
            mass_type = Mass.PTYPES.ISOTROPIC
            masses = [masses[0]] if len(masses) > 0 else [0.0]
        else:
            mass_type = Mass.PTYPES.ANISOTROPIC

        no = fem.nodes.from_id(nodeno)
        fem_set = fem.sets.add(FemSet(f"m{nodeno}", [no], FemSet.TYPES.NSET, parent=fem))
        el_id = max(fem.elements.max_el_id, max_external) + 1
        elem = fem.elements.add(Elem(el_id, [no], Elem.EL_TYPES.MASS_SHAPES.MASS, None, parent=fem), skip_grouping=True)
        mass = Mass(f"m{nodeno}", fem_set, masses, Mass.TYPES.MASS, ptype=mass_type, parent=fem, mass_id=el_id)

        elset = fem.sets.add(FemSet(f"m{nodeno}", [elem], FemSet.TYPES.ELSET, parent=fem))
        elem.mass_props = mass
        elem.elset = elset
        return mass

    def find_mgmass(match) -> Mass:
        d = match.groupdict()
        matno = str_to_int(d["matno"])
        mat_mass_map = {str_to_int(val["section_data"]["matno"]): val for key, val in mass_elem.items()}
        mass_el: dict = mat_mass_map.get(matno, None)
        if mass_el is None:
            raise ValueError()
        ndof = str_to_int(d["ndof"])
        if ndof != 6:
            raise NotImplementedError("Only mass matrices with 6 DOF are currently supported for reading")

        r = [float(x) for x in d["bulk"].split()]
        A = np.matrix(
            [
                [r[0], 0.0, 0.0, 0.0, 0.0, 0.0],
                [r[1], r[6], 0.0, 0.0, 0.0, 0.0],
                [r[2], r[7], r[11], 0.0, 0.0, 0.0],
                [r[3], r[8], r[12], r[15], 0.0, 0.0],
                [r[4], r[9], r[13], r[16], r[18], 0.0],
                [r[5], r[10], r[14], r[17], r[19], r[20]],
            ]
        )
        # use symmetry to complete the 6x6 matrix
        mass_matrix_6x6 = np.tril(A) + np.triu(A.T, 1)
        nodeno = gelmnt_point_node_id(mass_el["gelmnt"])
        elno = str_to_int(mass_el["gelmnt"].get("elno"))
        no = fem.nodes.from_id(nodeno)
        fem_set = fem.sets.add(FemSet(f"m{nodeno}", [no], FemSet.TYPES.NSET, parent=fem))

        mass_type = Mass.PTYPES.ANISOTROPIC
        mass = Mass(f"m{nodeno}", fem_set, mass_matrix_6x6, Mass.TYPES.MASS, ptype=mass_type, parent=fem, mass_id=elno)
        mass_el["el"] = mass
        return mass

    bn_masses = map(find_bnmass, cards.re_bnmass.finditer(bulk_str))
    mg_masses = map(find_mgmass, cards.re_mgmass.finditer(bulk_str))
    return FemElements(chain(bn_masses, mg_masses), fem_obj=fem)


def get_springs(bulk_str, fem: FEM, spring_elem: dict):
    matno_map = {str_to_int(sp["section_data"]["matno"]): sp for sp in spring_elem.values()}

    def find_mgspring(m):
        nonlocal matno_map
        d = m.groupdict()
        matno = str_to_int(d["matno"])
        ndof = str_to_int(d["ndof"])
        res: dict = matno_map.get(matno, None)
        if res is None:
            raise ValueError()

        elid = str_to_int(res["section_data"]["elno"])
        # MGSPRNG carries the lower triangle of an ndof x ndof stiffness matrix, so how
        # many values belong to it follows from ndof alone. Sesam pads a record out to a
        # whole number of slots, and re-exporting a .SIN through its input deck carries
        # that padding into the deck: a 6-DOF spring arrives with 22 values, not 21.
        # Consuming the extra one opened a seventh row, and the symmetric assembly below
        # then failed on `operands could not be broadcast together with shapes (7,6) (6,7)`.
        bulk = d["bulk"].split()[: ndof * (ndof + 1) // 2]

        spr_name = f"spr{elid}"
        n1 = fem.nodes.from_id(gelmnt_point_node_id(res["gelmnt"]))
        a = 1
        row = 0
        spring = []
        subspring = []
        for dof in bulk:
            subspring.append(float(dof.strip()))
            a += 1
            if a > ndof - row:
                spring.append(subspring)
                subspring = []
                a = 1
                row += 1
        # Left-pad each triangle row back out to the full ndof width. Was hardcoded to
        # 6, which only ever agreed with the rows for a 6-DOF spring.
        new_s = [[0.0] * (ndof - len(row)) + row for row in spring]
        spring_matrix = np.array(new_s)
        spring_matrix = spring_matrix + spring_matrix.T - np.diag(np.diag(spring_matrix))
        fs = FemSet(f"{spr_name}_set", [n1], FemSet.TYPES.NSET, parent=fem)
        return Spring(spr_name, elid, "SPRING1", fem_set=fs, stiff=spring_matrix, parent=fem)

    return {c.name: c for c in map(find_mgspring, cards.re_mgsprng.finditer(bulk_str))}
