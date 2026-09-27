from ada.fem import Load, LoadPressure
from ada.fem.exceptions.model_definition import UnsupportedLoadType


def write_load(load: Load) -> str:
    load_str_map = {
        Load.TYPES.GRAVITY: gravity_load_str,
        Load.TYPES.ACC: acc_load_str,
        Load.TYPES.PRESSURE: pressure_load_str,
    }

    load_str_writer = load_str_map.get(load.type, None)

    if load_str_writer is None:
        raise NotImplementedError(f'Load type "{load.type}"')

    return load_str_writer(load)


def gravity_load_str(load: Load) -> str:
    return f"""{load.name} = AFFE_CHAR_MECA(
    MODELE=model, PESANTEUR=_F(DIRECTION=(0.0, 0.0, 1.0), GRAVITE={load.magnitude})
)"""


def acc_load_str(load: Load) -> str:
    acc_dir_str = f"({','.join(load.acc_vector)})"
    return f"""{load.name} = AFFE_CHAR_MECA(
    MODELE=model, PESANTEUR=_F(DIRECTION={acc_dir_str}, GRAVITE={load.magnitude})
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

#: Surface sides naming an element's negative-normal face, and its positive-normal face. Shared
#: reading with the Sesam and Calculix writers: the Abaqus reader normalises a single-elset shell
#: surface to ``-1``, and a plain element set names no side, which is Abaqus' own default of SPOS.
_NEGATIVE_SIDES = frozenset({-1, "SNEG"})
_POSITIVE_SIDES = frozenset({None, "", 1, "SPOS"})


def _pressure_sign(side) -> float:
    if side in _POSITIVE_SIDES:
        return PRESSURE_SIGN["positive"]
    if side in _NEGATIVE_SIDES:
        return PRESSURE_SIGN["negative"]
    raise UnsupportedLoadType(
        f"a pressure on surface side {side!r} has no Code_Aster FORCE_COQUE form; a shell pressure names "
        f"one of the element's two faces (SPOS / SNEG) and carries its direction in the sign"
    )


def pressure_load_str(load: LoadPressure) -> str:
    """A uniform pressure on shell elements as ``FORCE_COQUE``, over the element sets it acts on.

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
    on anything but a shell -- ``PRES_REP`` is the 3D counterpart and which face of a solid a given
    surface side means is not established here.
    """
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
        for el in fem_set.members:
            el_type = getattr(el, "type", None)
            if el_type is None or not isinstance(el_type, ShellShapes):
                raise UnsupportedLoadType(
                    f"pressure load {load.name!r} acts on a {el_type} element; FORCE_COQUE is a shell load "
                    f"and the solid form (PRES_REP) is not established here"
                )
        pres = _pressure_sign(side) * load.magnitude
        rows.append(f"        _F(GROUP_MA='{fem_set.name}', PRES={pres}),")

    # VERI_NORM='NON' because the group loaded here is the shell model itself, not a skin. The
    # default 'OUI' runs Code_Aster's boundary-orientation check, which wants the named cells to be
    # the connected boundary of something and stops with <MESH3_99> ("les groupes de mailles de bord
    # ne forment pas un ensemble connexe") on a plain shell patch -- measured on the strip. Switching
    # it off also keeps the load's direction tied to the element normals as meshed, rather than to
    # whatever outward orientation the check would have imposed, which is what makes the sign above
    # reproducible.
    return (
        "{0} = AFFE_CHAR_MECA(\n    MODELE=model,\n    VERI_NORM='NON',\n    FORCE_COQUE=(\n{1}\n    ),\n)".format(
            load.name, "\n".join(rows)
        )
    )
