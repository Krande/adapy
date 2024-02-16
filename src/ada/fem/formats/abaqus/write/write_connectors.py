from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from .helper_utils import get_instance_name
from .write_orientations import csys_str

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Connector, ConnectorSection


def format_2d_column_data(data: list[list[str | int]], column_widths: list[int], separator: str = ", ") -> str:
    # Prepare the format string based on column widths
    format_str = separator.join(f"{{:<{width}}}" for width in column_widths)

    # Format each row in the data
    formatted_rows = [format_str.format(*row) for row in data]

    # Join all rows into a single string with newline characters
    result = "\n".join(formatted_rows)

    return result


def connectors_str(fem: FEM) -> str:
    return "\n".join([connector_str(con, True) for con in fem.elements.connectors])


def connector_sections_str(fem: FEM) -> str:
    return "\n".join([connector_section_str(consec) for consec in fem.connector_sections.values()])


def connector_str(connector: "Connector", written_on_assembly_level: bool) -> str:
    csys_ref = "" if connector.csys is None else f'\n "{connector.csys.name}",'

    end1 = get_instance_name(connector.n1, written_on_assembly_level)
    end2 = get_instance_name(connector.n2, written_on_assembly_level)
    return f"""**
** ----------------------------------------------------------------
** Connector element representing {connector.name}
** ----------------------------------------------------------------
**
*Elset, elset={connector.name}
 {connector.id},
*Element, type=CONN3D2
 {connector.id}, {end1}, {end2}
*Connector Section, elset={connector.name}, behavior={connector.con_sec.name}
 {connector.con_type},{csys_ref}
**
{csys_str(connector.csys, written_on_assembly_level)}
**"""


def connector_elastic_str(con_sec: ConnectorSection) -> str:
    elast = con_sec.elastic_comp
    if isinstance(elast, float):
        return """\n*Connector Elasticity, component=1\n{0:.3E},""".format(elast)

    conn_txt = ""
    for i, comp in enumerate(elast):
        if isinstance(comp, Iterable) is False:
            conn_txt += """\n*Connector Elasticity, component={1} \n{0:.3E},""".format(comp, i + 1)
        else:
            conn_txt += f"\n*Connector Elasticity, nonlinear, component={i + 1}, DEPENDENCIES=1"
            for val in comp:
                conn_txt += "\n" + ", ".join([f"{x:>12.3E}" if u <= 1 else f",{x:>12d}" for u, x in enumerate(val)])

    return conn_txt


def connector_plastic_str(con_sec: ConnectorSection) -> str:
    plastic_comp = con_sec.plastic_comp
    if plastic_comp is None:
        return ""

    conn_txt = ""
    for i, comp in enumerate(plastic_comp):
        conn_txt += """\n*Connector Plasticity, component={}\n*Connector Hardening, definition=TABULAR""".format(i + 1)
        for val in comp:
            force, motion, rate = val
            conn_txt += "\n{}, {}, {}".format(force, motion, rate)

    return conn_txt


def connector_damping_str(con_sec: ConnectorSection) -> str:
    extra_header_str = con_sec.metadata.get("abaqus", {}).get("extra_damper_args", "")
    if extra_header_str:
        extra_header_str = f", {extra_header_str}"

    damping = con_sec.damping_comp
    if isinstance(damping, float):
        return f"\n*Connector Damping, component=1{extra_header_str}\n{damping:.3E},"

    conn_txt = ""
    for i, comp in enumerate(damping):
        conn_txt += "\n*Connector Damping, "
        if isinstance(comp, float):
            conn_txt += f"component={i + 1} "
            conn_txt += f"\n{comp:.3E},"
        else:
            conn_txt += f"component=1, nonlinear, DEPENDENCIES=1{extra_header_str}"
            table_str = format_2d_column_data(comp, [12] * len(comp[0]))
            conn_txt += f"\n{table_str}"

    return conn_txt


def connector_rigid_str(con_sec: ConnectorSection) -> str:
    rigid_dofs = con_sec.rigid_dofs

    if rigid_dofs is None:
        return ""

    return "\n*Connector Elasticity, rigid\n " + ", ".join(["{0}".format(x) for x in rigid_dofs])


def connector_section_str(con_sec: "ConnectorSection") -> str:
    conn_txt = """*Connector Behavior, name={0}""".format(con_sec.name)

    conn_txt += connector_elastic_str(con_sec)
    conn_txt += connector_damping_str(con_sec)
    conn_txt += connector_plastic_str(con_sec)
    conn_txt += connector_rigid_str(con_sec)

    return conn_txt
