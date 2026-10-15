from typing import Iterable

from ada.fem import FemSection
from ada.fem.containers import FemSections

from .names import concept_name


def create_sections_str(
    fem_sections: FemSections,
    has_ref_points: bool = False,
    ref_points_group: str = None,
    membrane_large_strain: bool = False,
) -> str:
    from .write_constraints import REF_POINTS_GROUP, create_ref_points_discrete_str

    mat_assign_str = ""
    section_materials = ""

    beam_sections_str = "\n        POUTRE=(),"
    shell_sections_str = "\n        COQUE=(),"

    shells = [sh for sh in fem_sections.shells if not sh.is_membrane]
    membranes = [sh for sh in fem_sections.shells if sh.is_membrane]
    beams = [li for li in fem_sections.lines if not li.is_axial_only]
    trusses = [li for li in fem_sections.lines if li.is_axial_only and not li.is_cable]
    cables = [li for li in fem_sections.lines if li.is_cable]

    if len(shells) > 0:
        mat_assign_str_, shell_sections_str = ["".join(x) for x in zip(*[write_shell_section(sh) for sh in shells])]
        mat_assign_str += mat_assign_str_
        shell_sections_str = f"\n        COQUE=(\n{shell_sections_str}\n        ),"

    if len(membranes) > 0:
        mats, mat_assign_str_, membrane_str = [
            "".join(x) for x in zip(*[write_membrane_section(m, membrane_large_strain) for m in membranes])
        ]
        section_materials += mats
        mat_assign_str += mat_assign_str_
        shell_sections_str += f"\n        MEMBRANE=(\n{membrane_str}\n        ),"

    if len(beams) > 0:
        mat_assign_str_, beam_sections_str, orientations_str = [
            "".join(x) for x in zip(*[write_beam_section(bm) for bm in beams])
        ]
        mat_assign_str += mat_assign_str_
        beam_sections_str = f"\n        POUTRE=(\n{beam_sections_str}\n        ),"
        beam_sections_str += f"\n        ORIENTATION=(\n{orientations_str}\n        ),"

    if len(trusses) > 0:
        mat_assign_str_, truss_str = ["".join(x) for x in zip(*[write_truss_section(tr) for tr in trusses])]
        mat_assign_str += mat_assign_str_
        beam_sections_str += f"\n        BARRE=(\n{truss_str}\n        ),"

    if len(cables) > 0:
        mats, mat_assign_str_, cable_str = ["".join(x) for x in zip(*[write_cable_section(cb) for cb in cables])]
        section_materials += mats
        mat_assign_str += mat_assign_str_
        beam_sections_str += f"\n        CABLE=(\n{cable_str}\n        ),"

    if len(fem_sections.solids) > 0:
        mat_assign_str += write_solid_section(fem_sections.solids)

    discrete_str = create_ref_points_discrete_str(ref_points_group or REF_POINTS_GROUP) if has_ref_points else ""

    sec_str = ""
    if len(fem_sections.lines) > 0 or len(fem_sections.shells) > 0 or has_ref_points:
        sec_str = f"""element = AFFE_CARA_ELEM(\n
    MODELE=model,{shell_sections_str}{beam_sections_str}{discrete_str}
)"""

    return f"""{section_materials}
material = AFFE_MATERIAU(
    MODELE=model,
    AFFE=(
{mat_assign_str}
    )
)

# Shell elements:
#   EPAIS: thickness
#   VECTEUR: a direction of reference in the tangent plan

{sec_str}
"""


def write_shell_section(fem_sec: FemSection) -> tuple[str, str]:
    mat_name = concept_name(fem_sec.material, "material")
    sec_name = fem_sec.elset.name
    #
    local_vec = str(tuple(float(c) for c in fem_sec.local_y))
    mat_ = f'		_F(MATER=({mat_name},), GROUP_MA="{sec_name}"),\n'
    sec_str = f"""            _F(
                GROUP_MA=("{sec_name}"),
                EPAIS={fem_sec.thickness},
                VECTEUR={local_vec},
            ),
"""
    return mat_, sec_str


def write_membrane_section(fem_sec: FemSection, large_strain: bool = False) -> tuple[str, str, str]:
    """A membrane: ``MEMBRANE`` with its thickness and the direction of its local x, and a material of its own.

    A membrane solved with small strains (``DEFORMATION='PETIT'``) takes its stiffness from ``ELAS_MEMBRANE``, the
    membrane stiffness tensor already integrated over the thickness; ``EPAIS`` is used only with large strains
    (``GROT_GDEP``), where the element takes an isotropic ``ELAS`` instead (U4.42.01). A material may not hold both
    (<COMPOR5_56>, measured, 18.1.7), so each membrane section gets a ``DEFI_MATERIAU`` of its own: with
    ``large_strain``, ``ELAS``; else the isotropic plane-stress tensor times the thickness, ``E t / (1 - nu^2)`` on
    ``M_LLLL`` and ``M_TTTT``, ``nu`` times that on ``M_LLTT`` and ``G t = E t / (2 (1 + nu))`` on ``M_LTLT``, with
    the mass per unit area ``rho t``.
    """
    mat = fem_sec.material.model
    name = concept_name(fem_sec, "membrane")
    sec_name = fem_sec.elset.name
    t = fem_sec.thickness
    if large_strain:
        behaviour = f"    ELAS=_F(E={mat.E}, NU={mat.v}, RHO={mat.rho}),"
    else:
        a = mat.E * t / (1.0 - mat.v**2)
        g = mat.E * t / (2.0 * (1.0 + mat.v))
        behaviour = (
            f"    ELAS_MEMBRANE=_F(M_LLLL={a!r}, M_LLTT={a * mat.v!r}, M_LLLT=0.0, M_TTTT={a!r}, M_TTLT=0.0, "
            f"M_LTLT={g!r}, RHO={mat.rho * t!r}),"
        )
    material = f"""
{name} = DEFI_MATERIAU(
{behaviour}
)
"""
    local_x = str(tuple(float(c) for c in fem_sec.local_x))
    mat_ = f'		_F(MATER=({name},), GROUP_MA="{sec_name}"),\n'
    sec_str = f"""            _F(
                GROUP_MA=("{sec_name}"),
                EPAIS={t},
                VECT_1={local_x},
                N_INIT={fem_sec.pretension!r},
            ),
"""
    return material, mat_, sec_str


def write_truss_section(fem_sec: FemSection) -> tuple[str, str]:
    """A truss: ``BARRE`` with ``SECTION='GENERALE'`` and its area."""
    mat_name = concept_name(fem_sec.material, "material")
    sec_name = fem_sec.elset.name
    mat_ = f'		_F(MATER=({mat_name},), GROUP_MA="{sec_name}"),\n'
    sec_str = f"""            _F(
                GROUP_MA=("{sec_name}"),
                SECTION='GENERALE',
                CARA='A',
                VALE={fem_sec.area!r},
            ),
"""
    return mat_, sec_str


def write_cable_section(fem_sec: FemSection) -> tuple[str, str, str]:
    """A cable: ``CABLE`` with its area and initial tension ``N_INIT``, and a material of its own carrying
    ``CABLE=_F(EC_SUR_E=...)`` -- the compression stiffness relative to the tension one -- beside ``ELAS``."""
    mat = fem_sec.material.model
    name = concept_name(fem_sec, "cable")
    sec_name = fem_sec.elset.name
    material = f"""
{name} = DEFI_MATERIAU(
    ELAS=_F(E={mat.E}, NU={mat.v}, RHO={mat.rho}),
    CABLE=_F(EC_SUR_E={fem_sec.compression_ratio!r}),
)
"""
    mat_ = f'		_F(MATER=({name},), GROUP_MA="{sec_name}"),\n'
    sec_str = f"""            _F(
                GROUP_MA=("{sec_name}"),
                AIRE={fem_sec.area!r},
                N_INIT={fem_sec.pretension!r},
            ),
"""
    return material, mat_, sec_str


def write_beam_section(fem_sec: FemSection) -> tuple[str, str, str]:
    mat_name = concept_name(fem_sec.material, "material")
    sec_name = fem_sec.elset.name
    p = fem_sec.section.properties

    values = ",".join([str(x) for x in [p.Ax, p.Iy, p.Iz, p.Ix]])

    local_vec = str(tuple(float(c) for c in fem_sec.local_y))

    mat_ = f'		_F(MATER=({mat_name},), GROUP_MA="{sec_name}"),\n'
    sec_str = f"""            _F(
                GROUP_MA=("{sec_name}"),
                SECTION = 'GENERALE',
                CARA = ('A', 'IY', 'IZ', 'JX'),
                VALE = ({values})
            ),
"""
    orientations = f"""            _F(
                GROUP_MA = '{sec_name}',
                CARA = 'VECT_Y',
                VALE = {local_vec}
            ),
"""

    return mat_, sec_str, orientations


def write_solid_section(fem_sections: Iterable[FemSection]) -> str:
    mat_ = ""
    for fsec in fem_sections:
        mat_ += f'		_F(MATER=({concept_name(fsec.material, "material")},), GROUP_MA="{fsec.elset.name}"),\n'
    return mat_
