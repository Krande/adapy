import dataclasses
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
        return ""
    return f"""** Section: {fem_sec.name}
*Shell Section, elset={fem_sec.elset.name}, material={fem_sec.material.name}
 {fem_sec.thickness}, {fem_sec.int_points}"""


def line_section_str(fem_sec: FemSection):
    top_line = f"** Section: {fem_sec.elset.name}  Profile: {fem_sec.elset.name}"
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
        bt.CHANNEL: "GENERAL",
        bt.FLATBAR: "RECT",
    }
    sec_str = sec_map.get(fem_sec.section.type, None)
    if sec_str is None:
        raise Exception(f'Section type "{sec_type}" is not added to Abaqus beam export yet')

    if fem_sec.section.type in [bt.CHANNEL]:
        logger.error(f'Profile type "{sec_type}" is not supported by Abaqus. Using a General Section instead')

    return sec_str


def line_temperature_str(fem_sec: FemSection):
    _temperature = fem_sec.metadata["temperature"] if "temperature" in fem_sec.metadata.keys() else None
    return _temperature if _temperature is not None else "GRADIENT"


def eval_general_properties(section: Section) -> GeneralProperties:
    """The section properties to write on a ``*Beam General Section``, with missing ones filled in.

    Returns a **copy**. ``Section.properties`` caches its result, so writing into it would make an
    Abaqus export permanently change the model: a Sesam or IFC export later in the same process
    would then inherit whatever this function substituted.

    ``None`` is the only marker for "never computed" -- every field of
    :class:`~ada.sections.concept.GeneralProperties` defaults to ``None``. **Zero is a computed
    answer and is kept.** That distinction is the whole point of this function's shape: ``Iyz`` is
    exactly ``0`` for every section symmetric about an axis (box, tubular, I, circular, flatbar,
    channel -- only ``calc_angular`` returns a non-zero one), and treating that zero as missing used
    to substitute ``(Iy + Iz) / 2``. That value is the *largest* a product of inertia may legally
    take, so it then failed the positive-definiteness test below and inflated ``Iy`` as well: a
    UNP200 channel went out with ``Iy`` 4.8x too large and a fabricated ``I12``, and a UNP300 5.6x.
    Both numbers reached the deck behind a log line, which is the worst way for a section to be
    wrong.

    Where a value genuinely is unknown, the substitute is the neutral one -- ``0.0`` for ``Iyz`` --
    not the extreme of its range.
    """
    gp = dataclasses.replace(section.properties)
    name = section.name

    # A real cross-section has none of these at or below zero, so here 0.0 does mean "no data".
    for attr, fallback in (("Ix", 1.0), ("Iy", 2.0), ("Iz", 2.0)):
        value = getattr(gp, attr)
        if value is None or value <= 0.0:
            setattr(gp, attr, fallback)
            logger.warning(f"Section {name} {attr} is {value}. Substituting {fallback}. {log_fin}")

    if gp.Iyz is None:
        gp.Iyz = 0.0
        logger.warning(f"Section {name} has no Iyz. Substituting 0.0, i.e. symmetric. {log_fin}")

    # With a real Iyz this cannot fail for a physically possible section, so a failure means the
    # input is inconsistent. Say so instead of adjusting Iy until the inequality holds -- a section
    # quietly made 10% stiffer is the failure this function used to produce.
    if gp.Iy * gp.Iz - gp.Iyz**2 < 0 or not -(gp.Iy + gp.Iz) / 2 < gp.Iyz <= (gp.Iy + gp.Iz) / 2:
        raise ValueError(
            f"Section {name}: I(11)*I(22) - I(12)**2 must be positive and I(12) must lie within "
            f"+/-(I(11) + I(22))/2, but Iy={gp.Iy}, Iz={gp.Iz}, Iyz={gp.Iyz}. These properties "
            f"describe no real cross-section, so Abaqus would reject the section."
        )
    return gp
