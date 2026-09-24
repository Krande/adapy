from typing import TYPE_CHECKING

from ada.config import logger
from ada.fem import FemSection
from ada.fem.steps import StepExplicit
from ada.sections import GeneralProperties, Section

if TYPE_CHECKING:
    from ada import FEM


log_fin = "Please check your result and input. This is not a validated method of solving this issue"


def sections_str(fem: "FEM"):
    solids = fem.sections.solids
    shells = fem.sections.shells
    lines = fem.sections.lines

    solid_secs_str = "\n".join([solid_section_str(so) for so in solids]) if len(solids) > 0 else "** No solid sections"
    shell_secs_str = "\n".join([shell_section_str(sh) for sh in shells]) if len(shells) > 0 else "** No shell sections"
    line_secs_str = "\n".join([line_section_str(li) for li in lines]) if len(lines) > 0 else "** No line sections"

    if shell_secs_str.strip() == "":
        shell_secs_str = "** No shell sections"

    return solid_secs_str.strip() + "\n" + shell_secs_str.strip() + "\n" + line_secs_str.strip()


def solid_section_str(fem_sec: FemSection):
    return f"""** Section: {fem_sec.name}
*Solid Section, elset={fem_sec.elset.name}, material={fem_sec.material.name}
,"""


def shell_section_str(fem_sec: FemSection):
    if fem_sec.thickness == 0:
        # A shell of no thickness has no *Shell Section form; its elements go without a section.
        # Left out as before -- but said, not silently.
        from ada.fem.formats import conversion_report

        conversion_report.current().omitted(
            "abaqus writer", "*SHELL SECTION", fem_sec.name, "a zero-thickness shell section has no Abaqus form"
        )
        return ""
    return f"""** Section: {fem_sec.name}
*Shell Section, elset={fem_sec.elset.name}, material={fem_sec.material.name}
 {fem_sec.thickness}, {fem_sec.int_points}"""


def line_section_str(fem_sec: FemSection):
    # The section's and the profile's own names (both were written as the elset's name, so a
    # section read back was renamed after its set).
    profile = fem_sec.section.name if fem_sec.section is not None else fem_sec.elset.name
    top_line = f"** Section: {fem_sec.name}  Profile: {profile}"
    density = fem_sec.material.model.rho if fem_sec.material.model.rho > 0.0 else 1e-4
    ass = fem_sec.parent.parent.get_assembly()

    rotary_str = ""
    if len(ass.fem.steps) > 0:
        initial_step = ass.fem.steps[0]
        if type(initial_step) is StepExplicit:
            rotary_str = ", ROTARY INERTIA=ISOTROPIC"
    sec_data = line_cross_sec_type_str(fem_sec)
    sec_props = line_section_props(fem_sec)
    if sec_data != "GENERAL":
        sec_str = (
            f"{top_line}\n*Beam Section, elset={fem_sec.elset.name}, material={fem_sec.material.name}, "
            + f"temperature={line_temperature_str(fem_sec)}, section={sec_data}{rotary_str}\n{sec_props}"
        )
    else:
        sec_str = f"""{top_line}
*Beam General Section, elset={fem_sec.elset.name}, section=GENERAL{rotary_str}, density={density}
 {sec_props}"""

    return sec_str


def line_section_props(fem_sec: FemSection):
    n1 = ", ".join(str(x) for x in fem_sec.local_y)
    if "line1" in fem_sec.metadata.keys():
        return fem_sec.metadata["line1"] + f"\n{n1}"

    sec = fem_sec.section
    sec_data = line_cross_sec_type_str(fem_sec)

    if sec_data == "CIRC":
        return f"{sec.r}\n {n1}"
    elif sec_data == "I":
        if sec.t_fbtn + sec.t_w > min(sec.w_top, sec.w_btn):
            # TODO: Evaluate why this was here
            # new_width = sec.t_fbtn + sec.t_w + 5e-3
            # if sec.w_btn == min(sec.w_top, sec.w_btn):
            #     sec.w_btn = new_width
            # else:
            #     sec.w_top = new_width
            logger.info(f"For {fem_sec.name}: t_fbtn + t_w > min(w_top, w_btn). {log_fin}")
        return f"{sec.h / 2}, {sec.h}, {sec.w_btn}, {sec.w_top}, {sec.t_fbtn}, {sec.t_ftop}, {sec.t_w}\n {n1}"
    elif sec_data == "BOX":
        if sec.t_w * 2 > min(sec.w_top, sec.w_btn):
            raise ValueError("Web thickness cannot be larger than section width")
        return f"{sec.w_top}, {sec.h}, {sec.t_w}, {sec.t_ftop}, {sec.t_w}, {sec.t_fbtn}\n {n1}"
    elif sec_data == "GENERAL":
        mat = fem_sec.material.model
        gp = eval_general_properties(sec)
        return f"{gp.Ax}, {gp.Iy}, {gp.Iyz}, {gp.Iz}, {gp.Ix}\n {n1}\n {mat.E:.3E}, {mat.G},{mat.alpha:.2E}"
    elif sec_data == "PIPE":
        return f"{sec.r}, {sec.wt}\n {n1}"
    elif sec_data == "L":
        return f"{sec.w_btn}, {sec.h}, {sec.t_fbtn}, {sec.t_w}\n {n1}"
    elif sec_data == "RECT":
        return f"{sec.w_btn}, {sec.h}\n {n1}"
    elif sec_data == "ARBITRARY":
        return f"{channel_arbitrary_lines(sec)}\n {n1}"
    else:
        raise NotImplementedError(f'section type "{sec.type}" is not added to Abaqus export yet')


def line_cross_sec_type_str(fem_sec: FemSection):
    if "section_type" in fem_sec.metadata.keys():
        return fem_sec.metadata["section_type"]
    sec_type = fem_sec.section.type
    from ada.sections.categories import BaseTypes

    bt = BaseTypes

    sec_map = {
        bt.CIRCULAR: "CIRC",
        bt.IPROFILE: "I",
        bt.BOX: "BOX",
        bt.GENERAL: "GENERAL",
        bt.TUBULAR: "PIPE",
        bt.ANGULAR: "L",
        # Abaqus's beam library has no channel; ARBITRARY describes it exactly (three segments),
        # where GENERAL kept only an approximation of its integrated properties.
        bt.CHANNEL: "ARBITRARY",
        bt.FLATBAR: "RECT",
    }
    sec_str = sec_map.get(fem_sec.section.type, None)
    if sec_str is None:
        raise Exception(f'Section type "{sec_type}" is not added to Abaqus beam export yet')

    return sec_str


def channel_arbitrary_lines(sec: Section) -> str:
    """A channel as an ``SECTION=ARBITRARY`` beam section: its three wall segments by centreline
    and thickness -- bottom flange, web, top flange -- web centreline on the local-2 axis.

    The reader recognises exactly this shape and rebuilds the channel from it
    (``read_sections.channel_from_arbitrary``).
    """
    tip = sec.w_btn - sec.t_w / 2
    y_btn = -(sec.h - sec.t_fbtn) / 2
    y_top = (sec.h - sec.t_ftop) / 2
    return (
        f"3, {tip}, {y_btn}, 0.0, {y_btn}, {sec.t_fbtn}\n"
        f" 0.0, {y_top}, {sec.t_w}\n"
        f" {tip}, {y_top}, {sec.t_ftop}"
    )


def line_temperature_str(fem_sec: FemSection):
    _temperature = fem_sec.metadata["temperature"] if "temperature" in fem_sec.metadata.keys() else None
    return _temperature if _temperature is not None else "GRADIENT"


def eval_general_properties(section: Section) -> GeneralProperties:
    gp = section.properties
    name = section.name
    if gp.Ix <= 0.0:
        gp.Ix = 1
        logger.warning(f"Section {name} Ix <= 0.0. Changing to 2. {log_fin}")
    if gp.Iy <= 0.0:
        gp.Iy = 2
        logger.warning(f"Section {name} Iy <= 0.0. Changing to 2. {log_fin}")
    if gp.Iz <= 0.0:
        gp.Iz = 2
        logger.warning(f"Section {name} Iz <= 0.0. Changing to 2. {log_fin}")
    if gp.Iyz <= 0.0:
        gp.Iyz = (gp.Iy + gp.Iz) / 2
        logger.warning(f"Section {name} Iyz <= 0.0. Changing to (Iy + Iz) / 2. {log_fin}")
    if gp.Iy * gp.Iz - gp.Iyz**2 < 0:
        old_y = str(gp.Iy)
        gp.Iy = 1.1 * (gp.Iy + (gp.Iyz**2) / gp.Iz)
        logger.warning(
            f"Warning! Section {name}: I(11)*I(22)-I(12)**2 MUST BE POSITIVE. " f"Mod Iy={old_y} to {gp.Iy}. {log_fin}"
        )
    if (-(gp.Iy + gp.Iz) / 2 < gp.Iyz <= (gp.Iy + gp.Iz) / 2) is False:
        raise ValueError("Iyz must be between -(Iy+Iz)/2 and (Iy+Iz)/2")
    return gp
