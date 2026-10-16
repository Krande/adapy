import dataclasses
from typing import TYPE_CHECKING

from ada.config import logger
from ada.fem import FemSection
from ada.fem.steps import StepExplicit
from ada.sections import GeneralProperties, Section
from ada.sections.categories import BaseTypes

from ..grammar import format_number
from .helper_utils import render_block

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
    params = [("elset", fem_sec.elset.name), ("material", fem_sec.material.name)]
    return render_block("Solid Section", params, [","], [f"Section: {fem_sec.name}"])


def shell_section_str(fem_sec: FemSection):
    if fem_sec.thickness == 0:
        # A shell of no thickness has no *Shell Section form; its elements go without a section.
        # Left out as before -- but said, not silently.
        from ada.fem.formats import conversion_report

        conversion_report.current().omitted(
            "abaqus writer", "*SHELL SECTION", fem_sec.name, "a zero-thickness shell section has no Abaqus form"
        )
        return ""
    params = [("elset", fem_sec.elset.name), ("material", fem_sec.material.name)]
    if fem_sec.is_membrane:
        # *Membrane Section: the thickness alone (Abaqus Keywords Guide; Calculix reads the same).
        return render_block("Membrane Section", params, [f" {fem_sec.thickness},"], [f"Section: {fem_sec.name}"])
    data = [f" {fem_sec.thickness}, {fem_sec.int_points}"]
    return render_block("Shell Section", params, data, [f"Section: {fem_sec.name}"])


#: The suffix of the material a cable section is written with when its own material is not tension-only: a copy with
#: ``*No Compression`` (a material option in Abaqus, so it cannot be put on the section).
NO_COMPRESSION_SUFFIX = "_NC"


def cable_material_name(fem_sec: FemSection) -> str:
    """The material a cable section is written with: its own when that is already tension-only, else its
    ``*No Compression`` copy (:func:`cable_materials`)."""
    if fem_sec.material.metadata.get("no_compression", False) is True:
        return fem_sec.material.name
    return fem_sec.material.name + NO_COMPRESSION_SUFFIX


def truss_section_str(fem_sec: FemSection, material_name: str | None = None):
    """A truss or cable section: ``*Solid Section`` on the T3D elements, with the cross-section area as its data."""
    profile = fem_sec.section.name if fem_sec.section is not None else fem_sec.elset.name
    area = fem_sec.area
    if area is None or area <= 0.0:
        raise ValueError(f"{fem_sec.theory.value} section {fem_sec.name!r} has no cross-section area")
    if material_name is None:
        material_name = cable_material_name(fem_sec) if fem_sec.is_cable else fem_sec.material.name
    params = [("elset", fem_sec.elset.name), ("material", material_name)]
    top = [f"Section: {fem_sec.name}  Profile: {profile}  Theory: {fem_sec.theory.value}"]
    return render_block("Solid Section", params, [f" {format_number(area)},"], top)


def line_section_str(fem_sec: FemSection):
    if fem_sec.is_axial_only:
        return truss_section_str(fem_sec)
    # The section's and the profile's own names (both were written as the elset's name, so a
    # section read back was renamed after its set).
    profile = fem_sec.section.name if fem_sec.section is not None else fem_sec.elset.name
    top_line = [f"Section: {fem_sec.name}  Profile: {profile}"]
    density = fem_sec.material.model.rho if fem_sec.material.model.rho > 0.0 else 1e-4
    ass = fem_sec.parent.parent.get_assembly()

    rotary = []
    if len(ass.fem.steps) > 0:
        initial_step = ass.fem.steps[0]
        if type(initial_step) is StepExplicit:
            rotary = [("ROTARY INERTIA", "ISOTROPIC")]
    sec_data = line_cross_sec_type_str(fem_sec)
    sec_props = line_section_props(fem_sec)
    if sec_data != "GENERAL":
        params = [
            ("elset", fem_sec.elset.name),
            ("material", fem_sec.material.name),
            ("temperature", line_temperature_str(fem_sec)),
            ("section", sec_data),
            *rotary,
        ]
        return render_block("Beam Section", params, [sec_props], top_line)
    params = [("elset", fem_sec.elset.name), ("section", "GENERAL"), *rotary, ("density", format_number(density))]
    return render_block("Beam General Section", params, [f" {sec_props}"], top_line)


def line_section_props(fem_sec: FemSection):
    n1 = ", ".join(str(x) for x in fem_sec.local_y)
    if "line1" in fem_sec.metadata.keys():
        return fem_sec.metadata["line1"] + f"\n{n1}"

    sec = fem_sec.section
    sec_data = line_cross_sec_type_str(fem_sec)

    if sec_data == "CIRC":
        return f"{sec.r}\n {n1}"
    elif sec_data == "I" and sec.type == BaseTypes.TPROFILE:
        return f"{t_as_i_section_lines(sec)}\n {n1}"
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
        return f"{gp.Ax}, {gp.Iy}, {gp.Iyz}, {gp.Iz}, {gp.Ix}\n {n1}\n {format_number(mat.E)}, {mat.G},{format_number(mat.alpha)}"
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
    bt = BaseTypes

    sec_map = {
        bt.CIRCULAR: "CIRC",
        bt.IPROFILE: "I",
        # Abaqus' beam library has no `section=T`. Its own preprocessor spells a T as an I-section
        # with the bottom flange zeroed, so that is what adapy writes too -- see
        # `t_as_i_section_lines`, which is where the zeroing happens.
        bt.TPROFILE: "I",
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


def t_as_i_section_lines(sec: Section) -> str:
    """A T as a ``SECTION=I`` beam section: an I-section with its bottom flange zeroed.

    Abaqus has no ``section=T``. Asked to export a member carrying its own ``TProfile``,
    Abaqus/CAE writes ``section=I`` with ``b1`` and ``t1`` at zero and the flange in the
    ``b2``/``t2`` slots, so this is the kernel's own encoding rather than a choice made here.

    The zeroing is the whole of it, and it is not cosmetic. adapy stores a T in the
    *collapsed-bottom-flange* form -- ``w_btn = t_w`` and ``t_fbtn = t_ftop`` (see
    :mod:`ada.sections.string_to_section` and :mod:`ada.sections.from_geometry`) -- so handing those
    two fields to the ordinary I-section line would describe a T with a small stub flange on the
    bottom: extra material, extra stiffness, and no error anywhere. Writing zeros instead says what
    adapy means, which is that there is no bottom flange.

    ``l`` is the distance from the section origin to the section's bottom. adapy's profile outlines
    are centred on the beam axis, so it is ``h / 2``, as for an I.
    """
    return f"{sec.h / 2}, {sec.h}, 0.0, {sec.w_top}, 0.0, {sec.t_ftop}, {sec.t_w}"


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
