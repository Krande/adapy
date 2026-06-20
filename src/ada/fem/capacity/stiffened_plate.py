"""Build a stiffened-plate :class:`CapacityModel` from a mesh + panel-group spec.

This is the stiffened-plate specialization of the abstract
:class:`CapacityModelBuilder`. A future girder builder implements the same
interface and slots into :class:`~ada.fem.capacity.manager.CapacityManager`
without touching the grouping or extraction layers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from ada.config import logger
from ada.fem.capacity import extract
from ada.fem.capacity.model import (
    CapacityModel,
    CapMaterial,
    CapPlate,
    CapSection,
    CapStiffener,
)
from ada.fem.capacity.sources import PanelGroupSpec
from ada.fem.results.common import Mesh


def _cap_material(mesh: Mesh, element_id: int, gamma_m: float = 1.15) -> CapMaterial:
    mat = mesh.materials.get(extract.matno_of(mesh, element_id))
    model = getattr(mat, "model", None)
    E = float(getattr(model, "E", 2.1e11))
    fy = float(getattr(model, "sig_y", 355e6))
    poisson = float(getattr(model, "poisson", None) or 0.3)
    G = E / (2.0 * (1.0 + poisson))
    name = getattr(mat, "name", "steel")
    return CapMaterial(E=E, fy=fy, poisson=poisson, gamma_m=gamma_m, G=G, name=name)


class CapacityModelBuilder(ABC):
    """Turns a panel-group membership spec into a :class:`CapacityModel`."""

    @abstractmethod
    def build(self, mesh: Mesh, aux: extract.AuxRecords, group: PanelGroupSpec) -> CapacityModel: ...


class StiffenedPlateBuilder(CapacityModelBuilder):
    def build(self, mesh: Mesh, aux: extract.AuxRecords, group: PanelGroupSpec) -> CapacityModel:
        # Stiffener axis (used to orient plate length vs width). Use the first
        # stiffener with mesh elements; for an unstiffened field there is no
        # stiffener, so take an in-plane edge of the first plate instead (length
        # and width then become the two in-plane extents of the field); fall back
        # to global z.
        axis = np.array([0.0, 0.0, 1.0])
        for st in group.stiffeners:
            if st.element_ids:
                axis, _ = extract.beam_axis_and_span(mesh, st.element_ids)
                break
        else:
            inplane = _plate_inplane_axis(mesh, group)
            if inplane is not None:
                axis = inplane

        plates: list[CapPlate] = []
        for p in group.plates:
            if not p.element_ids:
                continue
            geono = extract.geono_of(mesh, p.element_ids[0])
            thickness = aux.thickness_by_geono.get(geono, 0.0)
            if not thickness:
                logger.warning("capacity: no GELTH thickness for plate %s (geono %s)", p.name, geono)
            length, width = extract.plate_dimensions(mesh, p.element_ids, axis)
            plates.append(
                CapPlate(
                    name=p.name,
                    thickness=thickness,
                    length=length,
                    width=width,
                    material=_cap_material(mesh, p.element_ids[0]),
                    element_ids=p.element_ids,
                )
            )

        stiffeners: list[CapStiffener] = []
        for s in group.stiffeners:
            if not s.element_ids:
                continue
            _, span = extract.beam_axis_and_span(mesh, s.element_ids)
            section = s.section if s.section is not None else _section_of(mesh, aux, s.element_ids[0])
            stiffeners.append(
                CapStiffener(
                    name=s.name,
                    section=section,
                    material=_cap_material(mesh, s.element_ids[0]),
                    span=span,
                    element_ids=s.element_ids,
                    eccentricity=s.eccentricity or 0.0,
                    continuous=s.continuous,
                )
            )

        return CapacityModel(name=group.name, plates=tuple(plates), stiffeners=tuple(stiffeners))


def _plate_inplane_axis(mesh: Mesh, group: PanelGroupSpec) -> np.ndarray | None:
    """In-plane edge direction of a group's first plate (for unstiffened fields)."""
    for plate in group.plates:
        if not plate.element_ids:
            continue
        coords = extract.element_node_coords(mesh, plate.element_ids[0])
        if len(coords) < 3:
            continue
        normal = np.cross(coords[1] - coords[0], coords[2] - coords[0])
        norm = np.linalg.norm(normal)
        if norm <= 0.0:
            continue
        normal = normal / norm
        axis = coords[1] - coords[0]
        axis = axis - (axis @ normal) * normal
        an = np.linalg.norm(axis)
        if an > 0.0:
            return axis / an
    return None


def _section_of(mesh: Mesh, aux: extract.AuxRecords, element_id: int) -> CapSection:
    """CapSection for a stiffener from adapy's parsed Section, with raw-card fallback."""
    section = _section_from_mesh(mesh, element_id)
    if section.height > 0.0 and section.web_thickness > 0.0:
        return section
    geono = extract.geono_of(mesh, element_id)
    if geono in aux.section_by_geono:
        return aux.section_by_geono[geono]
    return section


def _section_from_mesh(mesh: Mesh, element_id: int) -> CapSection:
    """Best-effort CapSection from adapy's parsed Section."""
    sec = mesh.sections.get(extract.geono_of(mesh, element_id))
    name = getattr(sec, "name", "")
    h = getattr(sec, "h", None)
    t_w = getattr(sec, "t_w", None)
    w_top = getattr(sec, "w_top", None)
    t_ftop = getattr(sec, "t_ftop", None)
    is_bulb = str(name).upper().startswith("HP")
    return CapSection(
        name=name or "",
        section_type=7 if is_bulb else 0,
        height=float(h) if h else 0.0,
        web_thickness=float(t_w) if t_w else 0.0,
        flange_width=float(w_top) if w_top else 0.0,
        flange_thickness=float(t_ftop) if t_ftop else 0.0,
    )
