from typing import Iterable

from ada.fem import FemSection
from ada.fem.containers import FemSections


def create_sections_str(fem_sections: FemSections) -> str:
    mat_assign_str = ""

    beam_sections_str = "\n        POUTRE=(),"
    shell_sections_str = "\n        COQUE=(),"

    if len(fem_sections.shells) > 0:
        mat_assign_str_, shell_sections_str = [
            "".join(x) for x in zip(*[write_shell_section(sh) for sh in fem_sections.shells])
        ]
        mat_assign_str += mat_assign_str_
        shell_sections_str = f"\n        COQUE=(\n{shell_sections_str}\n        ),"

    if len(fem_sections.lines) > 0:
        mat_assign_str_, beam_sections_str, orientations_str = [
            "".join(x) for x in zip(*[write_beam_section(bm) for bm in fem_sections.lines])
        ]
        mat_assign_str += mat_assign_str_
        beam_sections_str = f"\n        POUTRE=(\n{beam_sections_str}\n        ),"
        beam_sections_str += f"\n        ORIENTATION=(\n{orientations_str}\n        ),"

    if len(fem_sections.solids) > 0:
        mat_assign_str += write_solid_section(fem_sections.solids)

    sec_str = ""
    if len(fem_sections.lines) > 0 or len(fem_sections.shells) > 0:
        sec_str = f"""element = AFFE_CARA_ELEM(\n
    MODELE=model,{shell_sections_str}{beam_sections_str}
)"""

    return f"""
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
    mat_name = fem_sec.material.name
    sec_name = fem_sec.elset.name
    #
    local_vec = str(tuple(fem_sec.local_y))
    mat_ = f'		_F(MATER=({mat_name},), GROUP_MA="{sec_name}"),\n'
    sec_str = f"""            _F(
                GROUP_MA=("{sec_name}"),
                EPAIS={fem_sec.thickness},
                VECTEUR={local_vec},
            ),
"""
    return mat_, sec_str


def write_beam_section(fem_sec: FemSection) -> tuple[str, str, str]:
    mat_name = fem_sec.material.name
    sec_name = fem_sec.elset.name
    p = fem_sec.section.properties

    values = ",".join([str(x) for x in [p.Ax, p.Iy, p.Iz, p.Ix]])

    local_vec = str(tuple(fem_sec.local_y))

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
        mat_ += f'		_F(MATER=({fsec.material.name},), GROUP_MA="{fsec.elset.name}"),\n'
    return mat_
