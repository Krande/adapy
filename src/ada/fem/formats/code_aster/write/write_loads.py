from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import numpy as np

from ada.fem import Load, LoadLine, LoadPressure
from ada.fem.exceptions.model_definition import UnsupportedLoadType
from ada.fem.shell_sides import (
    SHELL_SIDE_NEGATIVE,
    SHELL_SIDE_POSITIVE,
    UnsupportedShellSide,
    normalise_shell_side,
)

from .names import concept_name

if TYPE_CHECKING:
    from ada.fem import FEM, FemSet
    from ada.fem.steps import Step


def write_load(load: Load, amplitude_written: bool = False) -> str:
    """One load's ``AFFE_CHAR_MECA``. ``amplitude_written``: the caller applies the load's amplitude itself (a dynamic
    step's FONC_MULT), so it is not reported as left out."""
    load_str_map = {
        Load.TYPES.GRAVITY: gravity_load_str,
        Load.TYPES.ACC: gravity_load_str,
        Load.TYPES.PRESSURE: pressure_load_str,
        Load.TYPES.LINE: line_load_str,
        Load.TYPES.FORCE: point_load_str,
        Load.TYPES.ACC_ROT: rotation_load_str,
        Load.TYPES.TRACTION: traction_load_str,
    }

    load_str_writer = load_str_map.get(load.type, None)

    if load_str_writer is None:
        raise UnsupportedLoadType(f'code_aster writer: load {load.name!r} of type "{load.type}" has no Code_Aster form')

    if getattr(load, "amplitude", None) is not None and not amplitude_written:
        from ada.fem.formats import conversion_report

        conversion_report.current().omitted(
            STAGE, "Load", load.name, "an amplitude; this writer applies the load in full"
        )
    return load_str_writer(load)


def gravity_load_str(load: Load) -> str:
    """A gravity or acceleration field as ``PESANTEUR``: the magnitude and the direction of the load's acceleration.

    The direction used to be ``(0, 0, 1)`` whatever the load said, so an acceleration along x or y -- a concept
    acceleration field becomes ``|a|`` along a unit ``dof`` -- was applied along z.
    """
    from ada.fem.loads import acceleration_vector

    acc = np.asarray(acceleration_vector(load), dtype=float)
    magnitude = float(np.linalg.norm(acc))
    if magnitude == 0.0:
        raise UnsupportedLoadType(f"code_aster writer: gravity load {load.name!r} has no acceleration")
    direction = ", ".join(repr(float(d)) for d in acc / magnitude)
    group = ""
    if load.fem_set is not None:
        group = f", GROUP_MA='{load.fem_set.name}'"
    return f"""{concept_name(load, "load")} = AFFE_CHAR_MECA(
    MODELE=model, PESANTEUR=_F(DIRECTION=({direction}), GRAVITE={magnitude!r}{group})
)"""


def rotation_load_str(load: Load) -> str:
    """A rotating frame (:class:`~ada.fem.LoadRotation`) as ``ROTATION``: its angular velocity ``VITESSE`` (rad/s)
    about ``AXE`` through ``CENTRE``, the centrifugal force (U4.44.01). Code_Aster has no keyword for an angular
    acceleration's force, so a load with one is refused by name."""
    from ada.fem.loads import LoadRotation

    if not isinstance(load, LoadRotation):
        raise UnsupportedLoadType(
            f"code_aster writer: load {load.name!r} of type acc_rot is not a LoadRotation; no axis given"
        )
    if float(getattr(load, "angular_acceleration", 0.0)) != 0.0:
        raise UnsupportedLoadType(
            f"code_aster writer: rotation load {load.name!r} has an angular acceleration; ROTATION takes an angular "
            "velocity only"
        )
    if load.angular_velocity == 0.0:
        raise UnsupportedLoadType(f"code_aster writer: rotation load {load.name!r} has no angular velocity")
    axis = ", ".join(repr(float(c)) for c in load.axis)
    centre = ", ".join(repr(float(c)) for c in load.origin)
    group = "" if load.fem_set is None else f", GROUP_MA='{load.fem_set.name}'"
    return f"""{concept_name(load, "load")} = AFFE_CHAR_MECA(
    MODELE=model, ROTATION=_F(VITESSE={load.angular_velocity!r}, AXE=({axis}), CENTRE=({centre}){group})
)"""


def traction_load_str(load: Load) -> str:
    """A surface traction (:class:`~ada.fem.LoadSurfaceTraction`) as ``FORCE_COQUE`` FX/FY/FZ, a force per unit area in
    global components (U4.44.01), on each shell element set of its surface; on solid faces, ``FORCE_FACE`` on the face
    cells the writer makes for it (:mod:`.write_skins`). A set mixing the two, or a solid side naming no face, is
    refused by name."""
    from ada.fem.shapes.definitions import ShellShapes
    from ada.fem.surfaces import pressure_elsets

    from .write_skins import current as current_skins

    try:
        groups = pressure_elsets(load.surface)
    except ValueError as e:
        raise UnsupportedLoadType(f"traction {load.name!r}: {e}") from e
    rows = []
    comps = ", ".join(f"{c}={float(v)!r}" for c, v in zip(("FX", "FY", "FZ"), load.traction))
    skins = current_skins()
    skin = skins.group(load)
    for fem_set, side in groups:
        if skin is not None and skins.covers(load, fem_set, side):
            continue
        if not all(isinstance(getattr(el, "type", None), ShellShapes) for el in fem_set.members):
            raise UnsupportedLoadType(
                f"code_aster writer: traction {load.name!r} on {fem_set.name!r}, which is neither all shells "
                "(FORCE_COQUE) nor solid faces (FORCE_FACE)"
            )
        rows.append(f"        _F(GROUP_MA='{fem_set.name}', {comps}),")
    keywords = ""
    if rows:
        keywords += "\n    FORCE_COQUE=(\n{0}\n    ),".format("\n".join(rows))
    if skin is not None:
        keywords += f"\n    FORCE_FACE=(\n        _F(GROUP_MA='{skin.name}', {comps}),\n    ),"
    return f"""{concept_name(load, "load")} = AFFE_CHAR_MECA(
    MODELE=model,{keywords}
)"""


#: Code_Aster's FORCE_NODALE components, dof 1 to 6.
_NODAL_COMPONENTS = ("FX", "FY", "FZ", "MX", "MY", "MZ")


def point_load_str(load: Load) -> str:
    """A point load as ``FORCE_NODALE`` on its node group, global components, moments as ``MX``/``MY``/``MZ``.

    The writer raised on every point load (``NotImplementedError: Load type "force"``). ``FORCE_NODALE`` takes a node
    group (U4.44.01); a load given in a local system is written in global components (``Load.forces_global``).
    """
    from ada.fem import FemSet

    fem_set = load.fem_set
    if fem_set is None or fem_set.type != FemSet.TYPES.NSET:
        raise UnsupportedLoadType(f"code_aster writer: point load {load.name!r} is not on a node set")
    forces = load.forces_global
    comps = ", ".join(f"{c}={float(f)!r}" for c, f in zip(_NODAL_COMPONENTS, forces) if f not in (None, 0, 0.0))
    if not comps:
        raise UnsupportedLoadType(f"code_aster writer: point load {load.name!r} has no non-zero component")
    return f"""{concept_name(load, "load")} = AFFE_CHAR_MECA(
    MODELE=model,
    FORCE_NODALE=_F(GROUP_NO='{fem_set.name}', {comps}),
)"""


#: The factor a pressure magnitude is written with, per surface side.
#:
#: adapy's convention is Abaqus': a positive pressure pushes **into** the face it names, which is
#: along the element's *negative* normal for the positive (SPOS) face.
#:
#: ``FORCE_COQUE``'s ``PRES`` is the same way round. Measured on the 4.0 x 0.5 m, 10 mm strip in
#: cylindrical bending under 1000 Pa (``tests/fem/test_pressure_load_cross_format.py``), whose mesh
#: gmsh winds with every element normal along -z: a positive ``PRES`` moved mid-span to +z, i.e.
#: along the negative normal. So a positive-face pressure is written through unchanged and only a
#: negative-face one is flipped -- the same rule the Sesam BEUSLO writer applies, arrived at from the
#: same Abaqus measurement.
PRESSURE_SIGN = {"positive": +1.0, "negative": -1.0}


def _pressure_sign(side) -> float:
    # Sides are read by ``ada.fem.shell_sides``, shared with every other writer.
    try:
        normalised = normalise_shell_side(side)
    except UnsupportedShellSide:
        normalised = None
    if normalised == SHELL_SIDE_POSITIVE:
        return PRESSURE_SIGN["positive"]
    if normalised == SHELL_SIDE_NEGATIVE:
        return PRESSURE_SIGN["negative"]
    raise UnsupportedLoadType(
        f"a pressure on surface side {side!r} has no Code_Aster FORCE_COQUE form; a shell pressure names "
        f"one of the element's two faces (SPOS / SNEG) and carries its direction in the sign"
    )


#: The ``AFFE_CHAR_MECA`` keyword of a pressure on a shell, and on a membrane (which has no ``FORCE_COQUE``).
SHELL_PRESSURE, MEMBRANE_PRESSURE = "FORCE_COQUE", "PRES_REP"


def pressure_rows(load: LoadPressure) -> list[tuple[str, FemSet, float]]:
    """``(keyword, element set, PRES)`` for each element set a uniform shell pressure acts on: what
    :func:`pressure_load_str` writes, one ``_F(GROUP_MA=..., PRES=...)`` each. Raises what that refuses."""
    from ada.fem.shapes.definitions import ShellShapes
    from ada.fem.surfaces import pressure_elsets

    distribution = getattr(load, "distribution", None)
    if distribution is not None and distribution != LoadPressure.P_DIST_TYPES.UNIFORM:
        raise UnsupportedLoadType(
            f'a "{distribution}" pressure is not a pressure intensity; Code_Aster FORCE_COQUE takes one'
        )

    surface = getattr(load, "surface", None)
    region = load.fem_set if surface is None else surface
    if region is None:
        raise UnsupportedLoadType(f"pressure load {load.name!r} names neither a surface nor an element set")
    try:
        groups = pressure_elsets(region)
    except ValueError as e:
        raise UnsupportedLoadType(f"pressure load {load.name!r} has no Code_Aster FORCE_COQUE form: {e}") from e

    rows = []
    for fem_set, side in groups:
        membranes = 0
        for el in fem_set.members:
            el_type = getattr(el, "type", None)
            if el_type is None or not isinstance(el_type, ShellShapes):
                raise UnsupportedLoadType(
                    f"pressure load {load.name!r} acts on a {el_type} element; FORCE_COQUE is a shell load "
                    "(a solid's PRES_REP is on face cells of its own, write_skins)"
                )
            fem_sec = getattr(el, "fem_sec", None)
            membranes += fem_sec is not None and fem_sec.is_membrane
        pres = _pressure_sign(side) * load.magnitude
        if membranes == 0:
            rows.append((SHELL_PRESSURE, fem_set, pres))
        elif membranes == len(fem_set.members):
            # MEMBRANE has no FORCE_COQUE; its pressure is PRES_REP, the face pressure, the same way round.
            rows.append((MEMBRANE_PRESSURE, fem_set, pres))
        else:
            raise UnsupportedLoadType(
                f"pressure load {load.name!r} acts on element set {fem_set.name!r} of both membranes and shells, "
                "which Code_Aster loads with different keywords (PRES_REP, FORCE_COQUE); split the set"
            )
    return rows


def nettable_pressure(load) -> bool:
    """Whether ``load`` is a uniform pressure on shells or membranes only (:func:`pressure_rows` takes it): a pressure
    whose items one charge can net with others' (:func:`pressure_items`). A varying one (``gradient``) is a FORMULE of
    ``AFFE_CHAR_MECA_F``, and a solid one ``PRES_REP`` on face cells of its own (:mod:`.write_skins`): neither shares a
    cell with a shell's ``FORCE_COQUE`` item, and each keeps its own items."""
    if load.type != Load.TYPES.PRESSURE or getattr(load, "gradient", None) is not None:
        return False
    try:
        pressure_rows(load)
    except UnsupportedLoadType:
        return False
    return True


def pressures_overlap(loads) -> bool:
    """Whether an element is loaded by two rows of the pressures ``loads`` (:func:`pressure_rows`): two loads on it,
    or one load naming it twice (two of its sets, or both of its faces).

    One charge does not add two ``FORCE_COQUE`` (or ``PRES_REP``) items on one cell: the later replaces the earlier
    (U4.44.01, "règle de surcharge"), where two charges add. Measured, Code_Aster 18.1.7: two -50 kPa pressures on one
    plate written into one charge loaded it with -50 kPa, half of what the two charges apply.
    """
    seen: set[tuple[str, int]] = set()
    for load in loads:
        for keyword, fem_set, _ in pressure_rows(load):
            ids = {(keyword, int(el.id)) for el in fem_set.members}
            if ids & seen or len(ids) < len(fem_set.members):
                return True
            seen |= ids
    return False


def net_shell_pressures(loads) -> dict[tuple[str, int], tuple[object, float]]:
    """``{(keyword, element id): (element, PRES)}``: the sum of every row of the pressures ``loads`` on each element,
    as ``PRES`` on its positive face (:data:`PRESSURE_SIGN`: a pressure ``p`` on the negative face is ``-p`` on the
    positive one, the same load). What the loads apply as separate charges, which add."""
    net: dict[tuple[str, int], tuple[object, float]] = {}
    for load in loads:
        for keyword, fem_set, pres in pressure_rows(load):
            for el in fem_set.members:
                key = (keyword, int(el.id))
                net[key] = (el, net.get(key, (el, 0.0))[1] + float(pres))
    return net


def net_pressure_groups(loads) -> list[tuple[str, str, list, float]]:
    """``(keyword, group name, elements, PRES)``, one per keyword and distinct net pressure of the pressures ``loads``
    (:func:`net_shell_pressures`), elements whose pressures cancel left out: the pressures as one charge loads them,
    where their own element sets overlap (:func:`pressures_overlap`).

    The groups are mesh groups of their own (:func:`add_pressure_groups`), named as a line load's are
    (:func:`_line_load_groups`): ``P`` and a hash of the loads' names and a counter, deterministic and at most 24
    characters.
    """
    net = net_shell_pressures(loads)
    scale = max((abs(p) for _, p in net.values()), default=0.0)
    by_value: dict[tuple[str, float], list] = {}
    for key in sorted(net):
        el, pres = net[key]
        if abs(pres) <= 1e-12 * scale:
            continue
        by_value.setdefault((key[0], pres), []).append(el)
    prefix = "P" + hashlib.sha1("\0".join(ld.name for ld in loads).encode("utf-8")).hexdigest()[:8]
    return [
        (keyword, f"{prefix}_{i}", members, pres) for i, ((keyword, pres), members) in enumerate(by_value.items(), 1)
    ]


def pressure_groups_of_steps(steps) -> list[list[LoadPressure]]:
    """The lists of pressures the deck may write into one charge: each pressure of ``steps`` on its own
    (:func:`pressure_load_str`), each load case's (``steps.static_lin.merged_charge_str``), and each general static
    step's own and carried ones."""
    from ada.fem.formats import conversion_report
    from ada.fem.formats.carried_loads import general_step_loads

    def pressures(loads) -> list:
        out, seen = [], set()
        for ld in loads or []:
            if nettable_pressure(ld) and id(ld) not in seen:
                seen.add(id(ld))
                out.append(ld)
        return out

    with conversion_report.collect():  # the step writers report what they find; this only looks ahead
        carried = general_step_loads(steps, STAGE)
    out = []
    for step in steps:
        cases = getattr(step, "load_cases", None) or {}
        lists = [step.loads] + [lc.loads for lc in cases.values()]
        if id(step) in carried:
            lists.append(carried[id(step)])
        for loads in lists:
            group = pressures(loads)
            out.extend([ld] for ld in group)
            if len(group) > 1:
                out.append(group)
    return out


def add_pressure_groups(steps: list[Step], fem: FEM) -> list[FemSet]:
    """Add to ``fem`` the mesh groups of the net pressures (:func:`net_pressure_groups`) of every list of pressures the
    deck may write into one charge (:func:`pressure_groups_of_steps`) whose element sets overlap, so they reach the
    MED file; returns them, for the caller to remove once the deck is written.

    All or nothing: every group is worked out, and a name the mesh already has refused, before any is added; a
    failure while adding takes the ones added off again. (The caller removes them only once this returns.)
    The deck names the same groups (:func:`pressure_items`), from the same loads by the same function.
    """
    from ada.fem import FemSet

    wanted: dict[str, list] = {}
    for loads in pressure_groups_of_steps(steps):
        try:
            if not pressures_overlap(loads):
                continue
            groups = net_pressure_groups(loads)
        except UnsupportedLoadType:
            continue  # refused where the load is written
        for _, name, members, _ in groups:
            if name in wanted:
                continue
            if name in fem.elsets:
                raise UnsupportedLoadType(
                    f"pressure loads {[ld.name for ld in loads]}: the mesh already has a group named {name!r}"
                )
            wanted[name] = members
    added: list[FemSet] = []
    try:
        for name, members in wanted.items():
            added.append(fem.sets.add(FemSet(name, members, FemSet.TYPES.ELSET, parent=fem)))
    except BaseException:
        for fs in added:
            fem.sets.remove(fs)
        raise
    return added


def _pressure_items(rows) -> list[tuple[str, str]]:
    """``(keyword, _F(...))`` of ``(keyword, group name, PRES)`` rows."""
    return [(keyword, f"_F(GROUP_MA='{name}', PRES={pres})") for keyword, name, pres in rows]


def pressure_items(loads) -> list[tuple[str, str]]:
    """``(keyword, item)`` of every ``FORCE_COQUE`` / ``PRES_REP`` item the pressures ``loads`` are written with in
    one charge: their own element sets, or where those overlap (:func:`pressures_overlap`) the net pressure on each
    element, over the groups :func:`add_pressure_groups` adds -- so that one charge loads the model as the loads
    do, each applied in full."""
    if pressures_overlap(loads):
        rows = [(keyword, name, pres) for keyword, name, _, pres in net_pressure_groups(loads)]
    else:
        rows = [(keyword, fem_set.name, pres) for load in loads for keyword, fem_set, pres in pressure_rows(load)]
    return _pressure_items(rows)


def pressure_load_str(load: LoadPressure) -> str:
    """A uniform pressure on shell elements as ``FORCE_COQUE``, over the element sets it acts on; a varying one (a
    ``gradient``) as the same keyword of ``AFFE_CHAR_MECA_F`` with a ``FORMULE`` of the geometry.

    Two things here were wrong before and are worth naming, because a deck carrying either analysed:

    ``FORCE_FACE=_F(FY=...)`` is a surface traction along global **Y**, not a pressure. On this
    strip -- loaded normal to its own plane, which is the x-y plane -- it asked Code_Aster for an
    in-plane load, so even had the group resolved, the answer would have been a membrane problem
    rather than plate bending. ``FORCE_COQUE``'s ``PRES`` is the normal pressure on a shell.

    ``GROUP_MA`` was the **surface's** name. A surface is an adapy concept with no counterpart in a
    MED file, which carries mesh groups: the groups written are the element sets, so a deck naming
    the surface stopped at ``<EXCEPTION> <MODELISA7_77>`` -- no such group -- and no pressure had
    ever reached Code_Aster. The element sets behind the surface are named instead, resolved by
    :func:`ada.fem.surfaces.pressure_elsets`, which the Calculix writer resolves its own with too so
    the two decks load the same elements.

    Refused by name rather than approximated: a total-force magnitude (``PRES`` is an intensity), a
    node-based surface or a bare element id (a mesh group is named, not an element), and a pressure
    on anything but a shell or a solid face.

    A pressure on solid elements is ``PRES_REP`` on face cells the writer adds to the mesh for it
    (:mod:`.write_skins`), one per loaded face, oriented out of the solid.

    A uniform shell pressure whose element sets overlap (two of its sets, or both faces of one) is written as the net
    pressure on each element (:func:`pressure_items`): in one charge an element's later item replaces its earlier one.
    """
    if nettable_pressure(load) and pressures_overlap([load]):
        items = pressure_items([load])
        rows = [f"        {item}," for keyword, item in items if keyword == SHELL_PRESSURE]
        membrane_rows = [f"        {item}," for keyword, item in items if keyword == MEMBRANE_PRESSURE]
        return _pressure_charge_str(concept_name(load, "load"), rows, membrane_rows, None, {})

    from ada.fem.shapes.definitions import ShellShapes
    from ada.fem.surfaces import pressure_elsets

    distribution = getattr(load, "distribution", None)
    if distribution is not None and distribution != LoadPressure.P_DIST_TYPES.UNIFORM:
        raise UnsupportedLoadType(
            f'a "{distribution}" pressure is not a pressure intensity; Code_Aster FORCE_COQUE takes one'
        )

    surface = getattr(load, "surface", None)
    region = load.fem_set if surface is None else surface
    if region is None:
        raise UnsupportedLoadType(f"pressure load {load.name!r} names neither a surface nor an element set")
    try:
        groups = pressure_elsets(region)
    except ValueError as e:
        raise UnsupportedLoadType(f"pressure load {load.name!r} has no Code_Aster FORCE_COQUE form: {e}") from e

    # A varying pressure (``LoadPressure.gradient``) is a function of the geometry: AFFE_CHAR_MECA_F with a FORMULE
    # of X, Y, Z per sign, evaluated by Code_Aster at the element's own points, as Sestra integrates BEUSLO's nodal
    # values. Its PRES is the same way round as the uniform one's.
    gradient = getattr(load, "gradient", None)
    name = concept_name(load, "load")
    formulas: dict[float, str] = {}
    rows, membrane_rows = [], []
    from .write_skins import current as current_skins

    solid_rows = []
    skin = current_skins().group(load)
    if skin is not None:
        # A pressure on solid faces: PRES_REP on the face cells made for it, whose normals point out of the solid --
        # PRES_REP pushes against the cell normal, so a positive pressure pushes into the solid.
        pres = load.magnitude if gradient is None else _pressure_formula(name, 1.0, formulas)
        solid_rows.append(f"        _F(GROUP_MA='{skin.name}', PRES={pres}),")
    for fem_set, side in groups:
        if current_skins().covers(load, fem_set, side):
            continue
        membranes = 0
        for el in fem_set.members:
            el_type = getattr(el, "type", None)
            if el_type is None or not isinstance(el_type, ShellShapes):
                raise UnsupportedLoadType(
                    f"pressure load {load.name!r} acts on a {el_type} element of set {fem_set.name!r} that has no "
                    "face cells for PRES_REP: the set mixes solids with other elements, or its side names no face"
                )
            fem_sec = getattr(el, "fem_sec", None)
            membranes += fem_sec is not None and fem_sec.is_membrane
        sign = _pressure_sign(side)
        pres = sign * load.magnitude if gradient is None else _pressure_formula(name, sign, formulas)
        if membranes == 0:
            rows.append(f"        _F(GROUP_MA='{fem_set.name}', PRES={pres}),")
        elif membranes == len(fem_set.members):
            # MEMBRANE has no FORCE_COQUE; its pressure is PRES_REP, the face pressure, the same way round.
            membrane_rows.append(f"        _F(GROUP_MA='{fem_set.name}', PRES={pres}),")
        else:
            raise UnsupportedLoadType(
                f"pressure load {load.name!r} acts on element set {fem_set.name!r} of both membranes and shells, "
                "which Code_Aster loads with different keywords (PRES_REP, FORCE_COQUE); split the set"
            )
    return _pressure_charge_str(name, rows, membrane_rows + solid_rows, gradient, formulas, load)


def _pressure_charge_str(name: str, rows, pres_rep_rows, gradient, formulas, load=None) -> str:
    """The ``AFFE_CHAR_MECA`` (``AFFE_CHAR_MECA_F`` for a ``gradient``) of a pressure's ``FORCE_COQUE`` and
    ``PRES_REP`` rows."""
    # VERI_NORM='NON' because the group loaded here is the shell model itself, not a skin. The
    # default 'OUI' runs Code_Aster's boundary-orientation check, which wants the named cells to be
    # the connected boundary of something and stops with <MESH3_99> ("les groupes de mailles de bord
    # ne forment pas un ensemble connexe") on a plain shell patch -- measured on the strip. Switching
    # it off also keeps the load's direction tied to the element normals as meshed, rather than to
    # whatever outward orientation the check would have imposed, which is what makes the sign above
    # reproducible.
    keywords = ""
    if rows:
        keywords += "\n    FORCE_COQUE=(\n{0}\n    ),".format("\n".join(rows))
    if pres_rep_rows:
        keywords += "\n    PRES_REP=(\n{0}\n    ),".format("\n".join(pres_rep_rows))
    if gradient is None:
        return "{0} = AFFE_CHAR_MECA(\n    MODELE=model,\n    VERI_NORM='NON',{1}\n)".format(name, keywords)
    c, (gx, gy, gz) = float(load.magnitude), gradient
    defs = "".join(
        f"{fname} = FORMULE(VALE='{sign!r}*({c!r} + {gx!r}*X + {gy!r}*Y + {gz!r}*Z)', NOM_PARA=('X', 'Y', 'Z'))\n"
        for sign, fname in formulas.items()
    )
    return "{0}{1} = AFFE_CHAR_MECA_F(\n    MODELE=model,\n    VERI_NORM='NON',{2}\n)".format(defs, name, keywords)


def _pressure_formula(name: str, sign: float, formulas: dict[float, str]) -> str:
    """The name of the FORMULE of the load's pressure times ``sign`` (one per sign, made on first use)."""
    if sign not in formulas:
        formulas[sign] = f"{name}_{'p' if sign > 0 else 'n'}"
    return formulas[sign]


#: The conversion report's stage for this writer.
STAGE = "code_aster writer"


def _is_beam(elem) -> bool:
    from ada.fem.shapes.definitions import LineShapes

    return elem.type == LineShapes.LINE


def _line_load_groups(load: LoadLine):
    """The mesh groups a line load is written over: ``(elsets, nsets)``.

    ``elsets`` are ``(name, elements, q)``, one per distinct intensity of the beam segments that load an element
    uniformly from end to end, for ``FORCE_POUTRE``; ``nsets`` are ``(name, nodes, load)``, one per distinct summed
    six-component nodal load of the other segments (:meth:`LoadLine.summed_beam_nodal_loads`: forces and moments on a
    two-node beam, forces on a shell edge), for ``FORCE_NODALE``. Code_Aster names a group, never an element or node
    (``FORCE_NODALE`` takes ``GROUP_NO`` only, U4.44.01), so each distinct value needs one. The names are a hash of the
    load's name and a counter: deterministic, so the writer that adds the groups to the mesh and the one that writes
    the ``.comm`` agree, and at most 24 characters, so the name map (:mod:`.name_map`) leaves them alone.
    """
    prefix = "L" + hashlib.sha1(load.name.encode("utf-8")).hexdigest()[:8]
    by_q: dict[tuple, list] = {}
    rest = []
    for seg in sorted(load.segments, key=lambda s: s.elem.id):
        if seg.uniform_over_beam_element():
            by_q.setdefault(tuple(float(q) for q in seg.q1), []).append(seg.elem)
        else:
            rest.append(seg)
    by_f: dict[tuple, list] = {}
    for node, f in LoadLine.summed_beam_nodal_loads(rest, _is_beam):
        by_f.setdefault(tuple(float(x) for x in f), []).append(node)
    elsets = [(f"{prefix}_e{i}", els, q) for i, (q, els) in enumerate(by_q.items(), start=1)]
    nsets = [(f"{prefix}_n{i}", nodes, f) for i, (f, nodes) in enumerate(by_f.items(), start=1)]
    return elsets, nsets


def add_line_load_groups(steps: list[Step], fem: FEM) -> list[FemSet]:
    """Add the mesh groups of every line load in ``steps`` to ``fem``, so they reach the MED file; returns them, for
    the caller to remove once the deck is written."""
    from ada.fem import FemSet

    added = []
    for step in steps:
        for load in step.loads:
            if not isinstance(load, LoadLine):
                continue
            elsets, nsets = _line_load_groups(load)
            for name, members, set_type in [(n, m, "elset") for n, m, _ in elsets] + [
                (n, m, "nset") for n, m, _ in nsets
            ]:
                container = fem.elsets if set_type == "elset" else fem.nsets
                if name in container:
                    raise UnsupportedLoadType(f"line load {load.name!r}: the mesh already has a group named {name!r}")
                added.append(fem.sets.add(FemSet(name, members, set_type, parent=fem)))
    return added


def line_load_str(load: LoadLine) -> str:
    """A distributed line load as one ``AFFE_CHAR_MECA``: ``FORCE_POUTRE`` where it is exact, nodal loads elsewhere.

    ``FORCE_POUTRE`` with ``FX``/``FY``/``FZ`` is a force per unit length in global components on ``POU_D_E``
    beams (U4.44.01, v14), constant over the group: exact for a beam element loaded uniformly end to end. A varying
    or partial stretch on a beam is written as ``FORCE_NODALE`` with the consistent nodal forces *and moments* of the
    Euler-Bernoulli element (:meth:`LoadLine.hermite_nodal_loads`), which is what ``POU_D_E`` is, so its nodal
    displacements are the exact ones; a shell edge load as the consistent nodal forces of the (linear or quadratic) edge
    (:meth:`LoadLine.nodal_loads`) -- ``FORCE_ARETE``, Code_Aster's shell edge load, acts on segment cells along the
    edge, which an adapy shell mesh does not have -- exact in the resultant and its moment, reported as a note.

    One ``AFFE_CHAR_MECA`` for the whole load because the static step's ``EXCIT`` names a load by its name.
    """
    from ada.fem.formats import conversion_report

    elsets, nsets = _line_load_groups(load)
    blocks = []
    if elsets:
        rows = "\n".join(f"        _F(GROUP_MA='{n}', FX={q[0]!r}, FY={q[1]!r}, FZ={q[2]!r})," for n, _, q in elsets)
        blocks.append(f"    FORCE_POUTRE=(\n{rows}\n    ),")
    if nsets:
        rows = []
        for n, _, f in nsets:
            comps = ", ".join(f"{c}={v!r}" for c, v in zip(_NODAL_COMPONENTS, f) if v != 0.0 or c == "FX")
            rows.append(f"        _F(GROUP_NO='{n}', {comps}),")
        blocks.append("    FORCE_NODALE=(\n{0}\n    ),".format("\n".join(rows)))
        if any(seg.edge is not None or not _is_beam(seg.elem) for seg in load.segments):
            conversion_report.current().note(
                STAGE,
                "LoadLine",
                load.name,
                "a shell edge load is written as the consistent nodal forces of the edges it acts on, linear or quadratic "
                "(FORCE_NODALE): FORCE_ARETE needs edge cells the mesh does not have",
                n_nodes=sum(len(nodes) for _, nodes, _ in nsets),
            )
    return "{0} = AFFE_CHAR_MECA(\n    MODELE=model,\n{1}\n)".format(concept_name(load, "load"), "\n".join(blocks))
