from typing import Dict

from ada import FEM
from ada.fem import FemSection
from ada.sections.categories import BaseTypes


def sections_str(fem: FEM):
    space = 20 * " "

    box = " BOX{id:>16}{h:>6.3f}{t_w:>10.3f}{t_ftop:>8.3f}{t_fbtn:>8.3f}{w_top:>8.3f}\n"
    tub = " PIPE{id:>14}{d:>14.3f}{wt:>14.3f}\n"
    ipe = " IHPROFIL{id:>13}{h:>13.3f}{t_w:>13.3f}{w_top:>13.3f}{t_ftop:>13.3f}{w_btn:>13.3f}{t_fbtn:>13.3f}\n"

    box_str = f"' Box Profiles\n'{space}Geom ID     H     T-sid   T-bot   T-top   Width   Sh_y Sh_z\n"
    tub_str = f"' Tubulars\n'{space}Geom ID       Do         Thick   (Shear_y   Shear_z      Diam2 )\n"
    circ_str = f"' Circulars\n'{space}Geom ID       Do         Thick   (Shear_y   Shear_z      Diam2 )\n"
    ip_str = f"' I-Girders\n'{space}Geom ID     H     T-web    W-top   T-top    W-bot   T-bot Sh_y Sh_z\n"
    tp_str = f"' T-profiles\n'{space}Geom ID     H     T-web    W-top   T-top    W-bot   T-bot Sh_y Sh_z\n"
    ang_str = f"' HP profiles\n'{space}Geom ID     H     T-web    W-top   T-top    W-bot   T-bot Sh_y Sh_z\n"
    cha_str = f"' Channels\n'{space}Geom ID     H     T-web    W-top   T-top    W-bot   T-bot Sh_y Sh_z\n"
    gens_str = f"' General Beams\n'{space}Geom ID     \n"

    sections: Dict[int, FemSection] = {fs.fem_sec.id: fs.fem_sec for fs in fem.elements.lines}
    for s_id, fs in sorted(sections.items()):
        s = fs.section
        if s.type == BaseTypes.BOX:
            # BOX      100000001    0.500   0.016   0.016   0.016    0.500
            box_str += box.format(
                id=fs.id,
                h=s.h,
                t_w=s.t_w,
                t_ftop=s.t_ftop,
                t_fbtn=s.t_fbtn,
                w_top=s.w_top,
            )
        elif s.type == BaseTypes.TUBULAR:
            # PIPE      60000001       1.010       0.045
            tub_str += tub.format(id=fs.id, d=s.r * 2, wt=s.wt)
        elif s.type == BaseTypes.CIRCULAR:
            # PIPE      60000001       1.010       0.045
            circ_str += tub.format(id=fs.id, d=s.r * 2, wt=s.r * 0.99)
        elif s.type == BaseTypes.IPROFILE:
            # IHPROFIL     11011    0.590   0.013    0.300   0.025    0.300   0.025
            ip_str += ipe.format(
                id=fs.id,
                h=s.h,
                t_w=s.t_w,
                w_top=s.w_top,
                t_ftop=s.t_ftop,
                w_btn=s.w_btn,
                t_fbtn=s.t_fbtn,
            )
        elif s.type == BaseTypes.TPROFILE:
            print(f'T-Profiles currently not considered. Relevant for bm id "{fs.id}". Will use IPE for now')
            # IHPROFIL     11011    0.590   0.013    0.300   0.025    0.300   0.025
            tp_str += ipe.format(
                id=fs.id,
                h=s.h,
                t_w=s.t_w,
                w_top=s.w_top,
                t_ftop=s.t_ftop,
                w_btn=s.w_btn,
                t_fbtn=s.t_fbtn,
            )

        elif s.type == BaseTypes.ANGULAR:
            print(f'Angular-Profiles are not supported by USFOS. Bm "{fs.id}" will use GENBEAM')
            gens_str += general_beam(fs)
            # raise ValueError('Angular profiles currently not considered. Relevant for bm id "{}"'.format(fs.id))

        elif s.type == BaseTypes.CHANNEL:
            print(f'Channel-Profiles are not supported by USFOS. Bm "{fs.id}" will use GENBEAM')
            gens_str += general_beam(fs)
            # raise ValueError('Channel profiles currently not considered. Relevant for bm id "{}"'.format(fs.id))

        elif s.type == BaseTypes.GENERAL:
            gens_str += general_beam(fs)
        else:
            raise ValueError(f'Unknown section string "{s.type}"')

    return box_str + ip_str + tp_str + ang_str + cha_str + tub_str + circ_str + gens_str


def general_beam(fs: FemSection) -> str:
    s = fs.section
    p = s.properties
    return (
        f" GENBEAM{fs.id:>11}{p.Ax:>11.3E}{p.Ix:>11.3E}{p.Iy:>11.3E}{p.Iz:>11.3E}\n{p.Wxmin:>11.3E}{p.Wymin:>11.3E}"
        f"{p.Wzmin:>11.3E}{p.Shary:>11.3E}\n{p.Sharz:>11.3E}\n"
    )
