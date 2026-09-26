from typing import TYPE_CHECKING

from ada.fem import FemSection
from ada.fem.steps import StepExplicit
from ada.sections import Section

# The Section -> Abaqus cross-section mapping lives in ada.sections.profiles because the Abaqus/CAE
# script writer needs the same decision: the `section=` keyword and data line here, the CAE profile
# class and its arguments there. Two copies of it would drift, and the sections they disagreed about
# would still analyse. `eval_general_properties` is re-exported because callers import it from here.
from ada.sections.profiles import eval_general_properties, profile_spec  # noqa: F401

from ..grammar import format_number
from .helper_utils import render_block

if TYPE_CHECKING:
    from ada import FEM


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
    data = [f" {fem_sec.thickness}, {fem_sec.int_points}"]
    return render_block("Shell Section", params, data, [f"Section: {fem_sec.name}"])


def line_section_str(fem_sec: FemSection):
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
        # Read from an INP: the data line came in verbatim and goes back out verbatim. The reader
        # always sets `section_type` alongside `line1`, so a round-tripped section never reaches the
        # mapping below -- which is why `section_type` can only ever affect the keyword line.
        return fem_sec.metadata["line1"] + f"\n{n1}"

    spec = profile_spec(fem_sec.section)
    if spec.inp_kind == "GENERAL":
        mat = fem_sec.material.model
        return f"{spec.inp_data_line()}\n {n1}\n {format_number(mat.E)}, {mat.G},{format_number(mat.alpha)}"

    return f"{spec.inp_data_line()}\n {n1}"


def line_cross_sec_type_str(fem_sec: FemSection):
    if "section_type" in fem_sec.metadata.keys():
        return fem_sec.metadata["section_type"]

    return profile_spec(fem_sec.section).inp_kind


def channel_arbitrary_lines(sec: Section) -> str:
    """A channel as a ``SECTION=ARBITRARY`` beam section: its three wall segments by centreline
    and thickness -- bottom flange, web, top flange -- web centreline on the local-2 axis.

    The reader recognises exactly this shape and rebuilds the channel from it
    (``read_sections.channel_from_arbitrary``). The polyline itself is built in
    :func:`ada.sections.profiles._channel_spec`, with the rest of the Section -> Abaqus mapping, so
    that the CAE writer traces the same one; this function stays as the name the reader's docstring
    points at.
    """
    return profile_spec(sec).inp_data_line()


def line_temperature_str(fem_sec: FemSection):
    _temperature = fem_sec.metadata["temperature"] if "temperature" in fem_sec.metadata.keys() else None
    return _temperature if _temperature is not None else "GRADIENT"
