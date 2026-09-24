from itertools import chain

import numpy as np

from ada.config import logger
from ada.core.utils import roundoff
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
        # NDOF components, padded to six: a solid-type node has NDOF=3 and carries only
        # the translational masses (see cards.re_bnmass).
        ndof = str_to_int(d["ndof"])
        vals = [roundoff(x) for x in d["content"].split()][:ndof]
        mass_in = (vals + [0.0] * 6)[:6]
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


def _lower_by_columns(values: list[float], n: int) -> np.ndarray:
    """A symmetric n x n matrix from its lower triangle stored column by column (MGSPRNG,
    MSHGLSP: ``K(1,1), K(2,1) ... K(n,1), K(2,2) ...``)."""
    k = np.zeros((n, n))
    it = iter(values)
    for j in range(n):
        for i in range(j, n):
            k[i, j] = next(it)
    return k + k.T - np.diag(np.diag(k))


def element_names(bulk_str: str) -> dict[int, tuple[str | None, list[str]]]:
    """``{element number: (name, comment lines)}`` from TDELEM records (section 4.2.1)."""
    out = {}
    lines = bulk_str.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("TDELEM"):
            continue
        fields = [float(x) for x in line[8:].split()]
        elno, codnam, codtxt = int(fields[1]), int(fields[2]), int(fields[3])
        n_name, n_text = codnam // 100, codtxt // 100
        text = [ln[8:].strip() for ln in lines[i + 1 : i + 1 + n_name + n_text]]
        out[elno] = (text[0] if n_name else None, text[n_name:])
    return out


def get_springs(bulk_str, fem: FEM, spring_elem: dict) -> list[Spring]:
    """Build the deck's Spring elements: element type 18 (GSPR) with MGSPRNG, and type 40
    (GLSH) with a general-spring MSHGLSP.

    Returns a list, not the name-keyed dict it used to: ``FEM.springs`` is now a view
    derived from ``FEM.elements``, so the caller adds these through ``add_spring`` and
    there is no dict for anyone to assign over.

    A TDELEM record gives a spring its name and, in a ``Nset: <set>`` comment, the node set
    it was defined on (the writer's ``write_springs``). Without one a spring is named
    ``spr<element number>`` on a set of its own. The node set is linked to the deck's set of
    that name by :func:`link_spring_sets` once the sets are read.
    """
    from ada.fem.formats import conversion_report

    from ..write.write_springs import NSET_COMMENT, two_node_matrix

    matno_map = {str_to_int(sp["section_data"]["matno"]): sp for sp in spring_elem.values()}
    names = element_names(bulk_str)

    def build(matno: int, stiff: np.ndarray, two_node: bool) -> Spring | None:
        res: dict = matno_map.get(matno, None)
        if res is None:
            raise ValueError(f"no spring element refers to stiffness record MATNO {matno}")
        elid = str_to_int(res["section_data"]["elno"])
        name, comments = names.get(elid, (None, []))
        name = name or f"spr{elid}"
        nset = next((c[len(NSET_COMMENT) :] for c in comments if c.startswith(NSET_COMMENT)), f"{name}_set")
        node_ids = gelmnt_node_ids(res["gelmnt"]["nids"])
        nodes = [fem.nodes.from_id(n) for n in (node_ids if two_node else node_ids[:1])]
        fs = FemSet(nset, nodes, FemSet.TYPES.NSET, parent=fem)
        return Spring(name, elid, "SPRING2" if two_node else "SPRING1", fem_set=fs, stiff=stiff, parent=fem)

    def find_mgspring(m):
        d = m.groupdict()
        ndof = str_to_int(d["ndof"])
        # MGSPRNG carries the lower triangle of an ndof x ndof stiffness matrix, so how
        # many values belong to it follows from ndof alone. Sesam pads a record out to a
        # whole number of slots, and re-exporting a .SIN through its input deck carries
        # that padding into the deck: a 6-DOF spring arrives with 22 values, not 21.
        values = [float(x) for x in d["bulk"].split()[: ndof * (ndof + 1) // 2]]
        return build(str_to_int(d["matno"]), _lower_by_columns(values, ndof), two_node=False)

    def find_mshglsp(m):
        d = m.groupdict()
        matno = str_to_int(d["matno"])
        n1, n2 = str_to_int(d["ndof1"]), str_to_int(d["ndof2"])
        n = n1 + n2
        values = [float(x) for x in d["bulk"].split()[: n * (n + 1) // 2]]
        k = _lower_by_columns(values, n)
        # adapy's two-node spring is a set of links, ``k`` at (i, j) between DOF i of the
        # first node and DOF j of the second: the coupling block holds them, and the matrix
        # is exactly theirs only if assembling them gives it back.
        stiff = -k[:n1, n1:] if n1 == n2 else None
        if stiff is None or not np.array_equal(two_node_matrix(stiff), k):
            conversion_report.current().omitted(
                "sesam reader",
                "MSHGLSP",
                str(matno),
                "a general two-node spring matrix that is not a set of DOF-to-DOF springs has no adapy form",
            )
            return None
        return build(matno, stiff + 0.0, two_node=True)

    springs = [find_mgspring(m) for m in cards.re_mgsprng.finditer(bulk_str)]
    springs += [find_mshglsp(m) for m in cards.re_mshglsp.finditer(bulk_str)]
    return [sp for sp in springs if sp is not None]


def link_spring_sets(fem: FEM) -> None:
    """Point each spring at the deck's node set of its name, or register the set it was
    built with when the deck has none."""
    for spring in fem.springs.values():
        existing = fem.sets.nodes.get(spring.fem_set.name)
        if existing is None:
            fem.sets.add(spring.fem_set)
        else:
            spring._fem_set = existing
