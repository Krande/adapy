from typing import TYPE_CHECKING, List

from ada.fem import Load, LoadGravity
from ada.fem.exceptions.model_definition import UnsupportedLoadType

if TYPE_CHECKING:
    from ada import FEM


def load_str(load: Load):
    if isinstance(load, LoadGravity):
        return write_gravity_load_str(load)
    elif load.type == Load.TYPES.PRESSURE:
        return pressure_load_str(load)
    else:
        raise ValueError("Calculix does not accept Loads without reference to a fem_set")


def write_gravity_load_str(load: LoadGravity):
    dof = [0, 0, 1] if load.dof is None else load.dof
    fem_set = load.fem_set.name
    return f"""** Name: gravity   Type: Gravity
*Dload
{fem_set}, GRAV, {load.magnitude}, {', '.join([str(x) for x in dof[:3]])}"""


#: CalculiX's ``*DLOAD`` label for a pressure on a shell element.
#:
#: The label carries no face information here, and that is measured rather than assumed: on one S4
#: whose nodes run counter-clockwise in the x-y plane, ``EALL, P, 1000.``, ``EALL, P1, 1000.`` and
#: ``EALL, P2, 1000.`` returned the **bit-identical** displacement field from ccx 2.23. CalculiX
#: expands a shell into a solid before solving, so the face number does not reach the shell's own
#: two sides; the sign of the magnitude is the only thing that can say which way a pressure pushes.
#: See :data:`PRESSURE_SIGN`.
PRESSURE_LABEL = "P"

#: The factor adapy's pressure magnitude is written with, per surface side.
#:
#: adapy's convention is Abaqus': a positive pressure pushes **into** the face it names. Measured on
#: one S4R with its normal along +z, Abaqus 2025 puts ``*Dsload P, 1000.`` at U3 = -3.2004021e-03 on
#: ``SPOS`` and +3.2004021e-03 on ``SNEG`` (the same measurement the Sesam BEUSLO writer's sign flip
#: rests on -- ``ada.fem.formats.sesam.write.write_loads``).
#:
#: CalculiX is the other way round. Measured on one S4 with its normal along +z, three corners
#: clamped, ``EALL, P, 1000.`` from ccx 2.23 moves the free corner to U3 = **+6.02369e-06** -- along
#: the positive normal, i.e. *away* from the positive face. So a positive-face pressure is written
#: negated and a negative-face one as it stands, and a model solved through Calculix then deflects
#: the same way it does through Abaqus, Code_Aster and Sestra instead of the opposite way.
PRESSURE_SIGN = {"positive": -1.0, "negative": +1.0}

#: Surface sides naming the face on an element's negative normal, and on its positive normal. The
#: same two sets the Sesam writer resolves a pressure with, so the two decks read one model
#: identically: the Abaqus reader normalises a single-elset shell surface to ``-1``, and a plain
#: element set names no side at all, which is Abaqus' own default of SPOS.
_NEGATIVE_SIDES = frozenset({-1, "SNEG"})
_POSITIVE_SIDES = frozenset({None, "", 1, "SPOS"})


def _pressure_region(load: Load):
    """The region a pressure acts on: its ``Surface`` if it has one, else its ``FemSet``.

    ``LoadPressure`` (what the Abaqus reader builds from ``*Dsload``) carries a ``Surface`` and no
    ``fem_set``; a bare ``Load`` of type ``pressure`` carries an element set. Both reach this writer.
    """
    surface = getattr(load, "surface", None)
    return load.fem_set if surface is None else surface


def _pressure_sign(side) -> float:
    """The factor for one surface side, or raise -- a side that is not a shell face is not guessed."""
    if side in _POSITIVE_SIDES:
        return PRESSURE_SIGN["positive"]
    if side in _NEGATIVE_SIDES:
        return PRESSURE_SIGN["negative"]
    raise UnsupportedLoadType(
        f"a pressure on surface side {side!r} has no Calculix form; *DLOAD on a shell names one of "
        f"its two faces (SPOS / SNEG), and which way the load pushes is carried by the sign"
    )


def pressure_load_str(load: Load) -> str:
    """A uniform pressure as ``*DLOAD``, one line per element set of the load's region.

    CalculiX takes a pressure on an element set directly, so the set the surface was built over is
    what is named -- a ccx deck carries no ``*SURFACE`` (the writer does not emit one) and the
    element sets are all written out already.

    Verified against ccx 2.23 on the 4.0 x 0.5 m, 10 mm S355 strip in cylindrical bending under
    1000 Pa that the Sesam BEUSLO writer was verified on: three mesh densities converging at second
    order on the closed form ``5 q L^4 / (384 D)``, deflecting the same way Sestra and Abaqus do,
    and a summed reaction of ``q L b``. See ``tests/fem/test_calculix_pressure_load.py``.

    Refused rather than approximated, each by name: a pressure whose magnitude is a total force
    rather than an intensity, a pressure on a node-based surface or a bare element id (``*DLOAD``
    names a set), and a pressure on a non-shell element -- CalculiX numbers a *solid*'s six faces
    individually, and which one a given surface side means is not established here, so writing one
    would be a guess at the face.
    """
    from ada.fem.loads import LoadPressure
    from ada.fem.shapes.definitions import ShellShapes
    from ada.fem.surfaces import pressure_elsets

    distribution = getattr(load, "distribution", None)
    if distribution is not None and distribution != LoadPressure.P_DIST_TYPES.UNIFORM:
        raise UnsupportedLoadType(f'a "{distribution}" pressure is not a pressure intensity; Calculix *DLOAD takes one')

    region = _pressure_region(load)
    if region is None:
        raise UnsupportedLoadType(f"pressure load {load.name!r} names neither a surface nor an element set")
    try:
        groups = pressure_elsets(region)
    except ValueError as e:
        raise UnsupportedLoadType(f"pressure load {load.name!r} has no Calculix *DLOAD form: {e}") from e

    data = []
    for fem_set, side in groups:
        for el in fem_set.members:
            el_type = getattr(el, "type", None)
            if el_type is None or not isinstance(el_type, ShellShapes):
                raise UnsupportedLoadType(
                    f"pressure load {load.name!r} acts on a {el_type} element; Calculix *DLOAD numbers a "
                    f"solid's faces individually and this writer only establishes the shell case"
                )
        data.append(f"{fem_set.name}, {PRESSURE_LABEL}, {_pressure_sign(side) * load.magnitude}")

    return "** Name: {0}   Type: Pressure\n*Dload\n{1}".format(load.name, "\n".join(data))


def check_if_grav_loads(fem: "FEM"):
    if LoadGravity in [type(step) for step in fem.steps]:
        return True
    else:
        return False


def get_all_grav_loads(fem: "FEM") -> List[LoadGravity]:
    return list(filter(lambda x: isinstance(x, LoadGravity), fem.get_all_loads()))
