from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..grammar import format_number, render_keyword
from .helper_utils import get_instance_name, is_connector_set
from .write_orientations import csys_str

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Connector, ConnectorSection


def connectors_str(fem: FEM) -> str:
    return "\n".join([connector_str(con, True) for con in fem.elements.connectors])


def connector_sets_str(fems) -> str:
    """The parts' connector-only element sets, at assembly level where their connectors are.
    The set named after a connector is written with it (connector_str); any other -- a set a BC
    or history output names -- was not written at all, so what referred to it named nothing."""
    out = ""
    for fem in fems:
        own = {con.name for con in fem.elements.connectors}
        for fem_set in fem.sets:
            if fem_set.type != "elset" or fem_set.name in own or not is_connector_set(fem_set):
                continue
            ids = [str(m.id) for m in fem_set.members]
            rows = [", ".join(ids[i : i + 16]) for i in range(0, len(ids), 16)]
            out += "\n" + render_keyword("Elset", [("elset", fem_set.name)], rows)
    return out


def connector_sections_str(fem: FEM) -> str:
    return "\n".join([connector_section_str(consec) for consec in fem.connector_sections.values()])


def connector_str(connector: "Connector", written_on_assembly_level: bool) -> str:
    section_data = [f" {connector.con_type},"]
    if connector.csys is not None:
        section_data.append(f' "{connector.csys.name}",')

    end1 = get_instance_name(connector.n1, written_on_assembly_level)
    end2 = get_instance_name(connector.n2, written_on_assembly_level)
    rule = "-" * 64
    return (
        render_keyword(
            "Elset",
            [("elset", connector.name)],
            [f" {connector.id},"],
            ["", rule, f"Connector element representing {connector.name}", rule, ""],
        )
        + render_keyword("Element", [("type", "CONN3D2")], [f" {connector.id}, {end1}, {end2}"])
        + render_keyword(
            "Connector Section",
            [("elset", connector.name), ("behavior", connector.con_sec.name)],
            section_data,
        )
        + f"**\n{csys_str(connector.csys, written_on_assembly_level)}\n**"
    )


def _component_blocks(keyword: str, comp, extra=()) -> str:
    """One ``*<keyword>`` block per component. ``comp`` is a scalar (component 1) or a list
    indexed by component - 1 whose entries are scalars (linear) or ``[[x, y, ...], ...]``
    tables (nonlinear, one dependency column when a row has more than two values)."""
    if isinstance(comp, (int, float)):
        return render_keyword(keyword, [("component", 1), *extra], [f"{format_number(comp)},"])
    out = ""
    for i, entry in enumerate(comp):
        if entry is None:  # a component with no value of its own
            continue
        if not isinstance(entry, Iterable):
            out += render_keyword(keyword, [("component", i + 1), *extra], [f"{format_number(entry)},"])
        else:
            rows = [", ".join(format_number(x) for x in row) for row in entry]
            # Every table carries its OWN component number: nonlinear damping used to be
            # written as component=1 for every component, so they all landed on DOF 1.
            params = [("nonlinear", None), ("component", i + 1), ("DEPENDENCIES", 1), *extra]
            out += render_keyword(keyword, params, rows)
    return out


def connector_elastic_str(con_sec: ConnectorSection) -> str:
    return _component_blocks("Connector Elasticity", con_sec.elastic_comp)


def connector_damping_str(con_sec: ConnectorSection) -> str:
    extra = con_sec.metadata.get("abaqus", {}).get("extra_damper_args", "")
    extra_params = [(extra, None)] if extra else []
    return _component_blocks("Connector Damping", con_sec.damping_comp, extra_params)


def connector_plastic_str(con_sec: ConnectorSection) -> str:
    if con_sec.plastic_comp is None:
        return ""
    out = ""
    for i, comp in enumerate(con_sec.plastic_comp):
        out += render_keyword("Connector Plasticity", [("component", i + 1)])
        out += render_keyword(
            "Connector Hardening",
            [("definition", "TABULAR")],
            [", ".join(format_number(v) for v in row) for row in comp],
        )
    return out


def connector_rigid_str(con_sec: ConnectorSection) -> str:
    if con_sec.rigid_dofs is None:
        return ""
    return render_keyword(
        "Connector Elasticity", [("rigid", None)], [" " + ", ".join(str(x) for x in con_sec.rigid_dofs)]
    )


def connector_section_str(con_sec: "ConnectorSection") -> str:
    """The ``*Connector Behavior`` block and the blocks that belong to it, newline-terminated
    -- the writer writes these back to back, and a section that ended mid-line used to glue
    the next ``*Connector Behavior`` onto its last data line, where the reader took it as data."""
    return (
        render_keyword("Connector Behavior", [("name", con_sec.name)])
        + connector_elastic_str(con_sec)
        + connector_damping_str(con_sec)
        + connector_plastic_str(con_sec)
        + connector_rigid_str(con_sec)
    )
