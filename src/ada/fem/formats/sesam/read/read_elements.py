from itertools import count

import numpy as np

from ada.fem import FEM, Elem, FemSet, Mass, Spring
from ada.fem.containers import FemElements
from ada.fem.formats.sesam.common import sesam_eltype_2_general
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes.lines import SpringTypes

from ..node_order import SESAM_ORDER
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
        eltyp = d["eltyp"]
        el_type = sesam_eltype_2_general(str_to_int(eltyp))
        nodes = SESAM_ORDER.nodes_from_format(el_type, [fem.nodes.from_id(x) for x in gelmnt_node_ids(d["nids"])])

        if isinstance(el_type, SpringTypes):
            spring_elem[el_no] = dict(gelmnt=d)
            return None

        if el_type == Elem.EL_TYPES.MASS_SHAPES.MASS:
            # Built by get_mass from its MGMASS, as on the array path. A plain Elem here as well
            # put two elements of that id in the container, plus a generated set ``m<id>``.
            mass_elem[el_no] = dict(gelmnt=d)
            return None

        metadata = dict(eltyad=str_to_int(d["eltyad"]), eltyp=eltyp)
        return Elem(el_no, nodes, el_type, None, parent=fem, metadata=metadata)

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


def get_mass(bulk_str: str, fem: FEM, mass_elem: dict, renumber_map: dict | None = None) -> list[Mass]:
    """The deck's masses, added to ``fem.elements`` by id: each mass element (GELMNT1 ELTYP 11)
    with its own id, and each BNMASS on a new one."""
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
        # NDOF components, padded to six: a solid-type node has NDOF=3 and carries only
        # the translational masses (see cards.re_bnmass).
        ndof = str_to_int(d["ndof"])
        vals = [float(x) for x in d["content"].split()][:ndof]
        mass_in = (vals + [0.0] * 6)[:6]
        masses = [m for m in mass_in if m != 0.0]
        if checkEqual2(masses):
            mass_type = Mass.PTYPES.ISOTROPIC
            masses = [masses[0]] if len(masses) > 0 else [0.0]
        else:
            mass_type = Mass.PTYPES.ANISOTROPIC

        no = fem.nodes.from_id(nodeno)
        fem_set = fem.sets.add(FemSet(f"m{nodeno}", [no], FemSet.TYPES.NSET, parent=fem))
        # The Mass is the element: a plain MASS Elem of the same id used to go in beside it,
        # which is why the caller's ``+=`` renumbered every mass -- and with them the mass
        # elements a deck names by id.
        mass = Mass(f"m{nodeno}", fem_set, masses, Mass.TYPES.MASS, ptype=mass_type, parent=fem, mass_id=next(bn_ids))
        fem.elements.add(mass)
        mass.elset = fem.sets.add(FemSet(f"m{nodeno}", [mass], FemSet.TYPES.ELSET, parent=fem))
        return mass

    first_mass_element = max(mass_elem, default=0)
    bn_ids = count(max(fem.elements.max_el_id, max_external, first_mass_element) + 1)
    tdelem = element_names(bulk_str)
    mgmass = {matno: m for matno, m in (lower_matrix(x) for x in cards.re_mgmass.finditer(bulk_str))}

    def mass_from_element(elno: int, mass_el: dict) -> Mass:
        """A mass element (GELMNT1 ELTYP 11) and the MGMASS its GELREF1 MATNO names."""
        matno = str_to_int(mass_el["section_data"]["matno"])
        if matno not in mgmass:
            raise ValueError(f"Mass element {elno} refers to MGMASS {matno}, which the deck does not have")
        matrix = mgmass[matno]
        no = fem.nodes.from_id(gelmnt_point_node_id(mass_el["gelmnt"]))
        name = tdelem.get(elno, f"m{no.id}")
        value, mass_type, ptype = point_mass_from_matrix(matrix)
        mass = Mass(name, [no], value, mass_type, ptype=ptype, parent=fem, mass_id=elno)
        mass_el["el"] = mass
        fem.elements.add(mass)
        return mass

    masses = [
        mass_from_element(elno, mass_el) for elno, mass_el in sorted(mass_elem.items()) if "section_data" in mass_el
    ]
    return list(map(find_bnmass, cards.re_bnmass.finditer(bulk_str))) + masses


def element_names(bulk_str: str) -> dict[int, str]:
    """``{elno: name}`` from TDELEM (manual 4.2.1)."""
    from .read_sets import text_record

    out = {}
    for m in cards.re_tdelem.finditer(bulk_str):
        d = m.groupdict()
        name, _ = text_record(d, "name")
        if name:
            out[str_to_int(d["elno"])] = name
    return out


def lower_matrix(match) -> tuple[int, np.ndarray]:
    """``(matno, full symmetric matrix)`` of an MGMASS / MGSPRNG record.

    The record holds the NDOF x NDOF matrix's terms on and below the diagonal, column by
    column (manual 7.4.7, 7.4.8), so how many values belong to it follows from NDOF alone:
    Sesam pads a record out to whole lines, and a re-exported .SIN can carry a padding slot
    past the matrix."""
    d = match.groupdict()
    ndof = str_to_int(d["ndof"])
    values = [float(x) for x in d["bulk"].split()][: ndof * (ndof + 1) // 2]
    return str_to_int(d["matno"]), _symmetric_from_lower_columns(values, ndof)


def _symmetric_from_lower_columns(values: list[float], n: int) -> np.ndarray:
    k = np.zeros((n, n))
    it = iter(values)
    for j in range(n):
        for i in range(j, n):
            k[i, j] = k[j, i] = next(it)
    return k


def point_mass_from_matrix(matrix: np.ndarray):
    """``(value, mass type, point mass type)`` for adapy's ``Mass`` from a mass matrix.

    A translational diagonal is a point mass -- isotropic when its three terms agree -- and a
    rotational block alone a rotary inertia (``I11, I22, I33, I12, I13, I23``), which is
    what the writer makes of them (``write_point_elements.mass_matrix``). Anything else --
    the two coupled, or translational terms off the diagonal -- stays the full matrix."""
    m = np.zeros((6, 6))
    n = matrix.shape[0]
    m[:n, :n] = matrix
    trans, rot = m[:3, :3], m[3:, 3:]
    uncoupled = not np.any(m[:3, 3:])
    if uncoupled and not np.any(rot) and not np.any(trans - np.diag(np.diag(trans))):
        diag = [float(x) for x in np.diag(trans)]
        if diag[0] == diag[1] == diag[2]:
            return diag[0], Mass.TYPES.MASS, Mass.PTYPES.ISOTROPIC
        return diag, Mass.TYPES.MASS, Mass.PTYPES.ANISOTROPIC
    if uncoupled and not np.any(trans):
        inertia = [rot[0, 0], rot[1, 1], rot[2, 2], rot[0, 1], rot[0, 2], rot[1, 2]]
        return [float(x) for x in inertia], Mass.TYPES.ROT_INERTIA, None
    return m, Mass.TYPES.MASS, Mass.PTYPES.ANISOTROPIC


def attach_named_sets(fem: FEM) -> None:
    """Point a mass element at the deck's own element set for it, the one holding just it,
    once the sets are read."""
    elsets = {}
    for fs in fem.sets.elements.values():
        elsets.setdefault(frozenset(m.id for m in fs.members), fs)
    for mass in fem.elements.masses:
        if mass.elset is None and mass.id is not None:
            mass.elset = elsets.get(frozenset([mass.id]))


def get_springs(bulk_str, fem: FEM, spring_elem: dict) -> list[Spring]:
    """Build the deck's Spring elements.

    Returns a list, not the name-keyed dict it used to: ``FEM.springs`` is now a view
    derived from ``FEM.elements``, so the caller adds these through ``add_spring`` and
    there is no dict for anyone to assign over.
    """

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

    return list(map(find_mgspring, cards.re_mgsprng.finditer(bulk_str)))
