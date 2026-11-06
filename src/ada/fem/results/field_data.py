from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

import numpy as np

from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes


@dataclass(frozen=True)
class FieldPresentation:
    """Optional semantic/presentation metadata for a result field.

    Readers populate this when the source exposes a hierarchy richer than a
    flat field name (the Position -> Attribute -> Component tree is
    the first consumer). Generic formats may leave it ``None`` and retain the
    existing flat-picker behaviour.
    """

    semantic_key: str
    group_path: tuple[str, ...]
    coordinate_system: str = ""
    surface: str = ""
    derived: bool = False
    unit: str = ""
    component_units: tuple[str, ...] = ()
    # For categorical fields (a material id, a section id): what each stored
    # numeric value MEANS, as (value, label) pairs. A tuple of pairs rather
    # than a dict so the dataclass stays frozen/hashable; the manifest carries
    # it as a JSON object. Empty for ordinary continuous fields.
    value_labels: tuple[tuple[float, str], ...] = ()


class FieldRole(str, Enum):
    """What a result field is, the same in every format: the per-format map of fields a consumer
    (a code check, a comparison) reads instead of a format's field names.

    A reader tags a field with a role only when it holds the field in the role's layout and
    convention below -- adapy's canonical one, which is Sesam's (a field of another format is
    converted to it by its reader). Ids are the numbers people read (external labels).

    ``NODAL_DISPLACEMENT``
        ``NodalFieldData``, rows ``[node, U1, U2, U3, U4, U5, U6]``, global axes.
    ``NODAL_REACTION``
        ``NodalFieldData``, rows ``[node, F1 .. F6]``, global axes, the force the support puts on
        the structure.
    ``SHELL_STRESS``
        ``ElementFieldData``, rows ``[element, point, SIGXX, SIGYY, TAUXY]`` (tension positive) in
        the element's axes ``Mesh.element_frames`` at the result points ``Mesh.result_points``:
        each corner and the centroid on the bottom surface (local ``z = -t/2``), then the same on
        the top -- for a 4-node shell ``n1 n2 c n4 n3``, for a 3-node one ``n1 n2 c n3``.
    ``BEAM_FORCES``
        ``ElementFieldData``, rows ``[element, station, NXX, NXY, NXZ, MXX, MXY, MXZ]`` at stations
        1, 2, 3 (start, middle, end) in the beam's local axes (x along it, z ``Mesh.vectors``):
        ``NXX`` tension positive, ``dMXZ/dx = NXY`` and ``dMXY/dx = -NXZ``.
    """

    NODAL_DISPLACEMENT = "nodal_displacement"
    NODAL_REACTION = "nodal_reaction"
    SHELL_STRESS = "shell_stress"
    BEAM_FORCES = "beam_forces"


@dataclass
class FieldData:
    name: str
    step: int
    components: list[str]
    values: np.ndarray
    eigen_freq: float = None
    eigen_value: float = None
    presentation: FieldPresentation | None = None
    # The values are a DIFFERENCE between two results (``FEAResultComparison``), so zero
    # means "unchanged" and the colour scale is centred on it.
    difference: bool = False
    #: What the field is in every format (:class:`FieldRole`), when it is held in that role's layout; None for any
    #: other field. Last, so that readers building fields by position are not affected.
    role: FieldRole | None = None


class NodalFieldType(str, Enum):
    DISP = "displacement"
    VEL = "velocity"
    FORCE = "force"
    UNKNOWN = "unknown"


@dataclass
class NodalFieldData(FieldData):
    COLS: ClassVar[list[str]] = ["node_label"]
    field_type: NodalFieldType = None

    def get_all_values(self):
        num_cols = len(self.COLS)
        num_comp = len(self.components)
        cr = list(range(num_cols, num_cols + num_comp + 1)) if num_comp != 0 else -1

        if num_comp == 0:
            return self.values[:, -1]

        result = self.values[:, cr[0] : cr[-1]]
        return result


class FieldPosition(Enum):
    NODAL = "nodal"
    INT = "integration_point"
    ELEMENT_NODAL = "element_nodal"
    ELEMENT_AVERAGE = "element_average"
    RESULT_POINT = "result_point"
    LINE_RESULT_POINT = "line_result_point"


@dataclass
class ElementFieldData(FieldData):
    """Values from element integration points"""

    field_pos: FieldPosition = FieldPosition.NODAL
    elem_type: LineShapes | ShellShapes | SolidShapes = None
    COLS: ClassVar[list[str]] = ["elem_label", "sec_num"]
    int_positions: list[tuple] = None

    def get_by_element_id(self, elem_ids: list[int], int_points: list[int] = None) -> ElementFieldData:
        data = []
        for x in self.values:
            if int(x[0]) not in elem_ids:
                continue
            if int_points is not None and x[1] not in int_points:
                continue
            data.append(x)

        return ElementFieldData(self.name, self.step, self.components, np.array(data))

    def _get_field_nodal(self, cr: int):
        nodes = self.values[:, 2]
        unique_nodes = np.unique(nodes)
        num_unique = len(unique_nodes)

        result_data = np.arange(0, num_unique)

        for i, unique_nodal in enumerate(unique_nodes):
            instances = np.where(nodes == unique_nodal)
            if cr != -1:
                field_data = self.values[instances, cr[0] : cr[-1]].ravel()
            else:
                field_data = self.values[instances, cr].ravel()
            averaged_data = sum(field_data) / len(field_data)
            result_data[i] = averaged_data

        return result_data

    def _get_field_int(self, cr) -> np.ndarray | dict[str, np.ndarray]:
        elements = self.values[:, 0]
        unique_element_ids = np.unique(elements)
        num_unique = len(unique_element_ids)

        if cr != -1:
            num_int_points = int(len(self.values) / num_unique)
            result_dict = {}
            for j, x in enumerate(self.components, start=cr[0]):
                result_data = np.zeros((num_unique, num_int_points))
                for i, unique_elements in enumerate(unique_element_ids):
                    instances = np.where(elements == unique_elements)
                    comp = self.values[instances, j]
                    result_data[i] = comp

                result_dict[x] = [result_data]

            return result_dict
        else:
            num_int_points = int(len(self.values) / num_unique) * len(self.components)
            result_data = np.zeros((num_unique, num_int_points))
            for i, unique_elements in enumerate(unique_element_ids):
                instances = np.where(elements == unique_elements)
                field_data = self.values[instances, cr].ravel()
                result_data[i] = field_data

                return result_data

    def get_all_values(self):
        num_cols = len(self.COLS)
        num_comp = len(self.components)
        cr = list(range(num_cols, num_cols + num_comp + 1)) if num_comp != 0 else -1

        if self.field_pos == self.field_pos.NODAL:
            return self._get_field_nodal(cr)
        else:
            return self._get_field_int(cr)


class LineSectionIntegrationPoints:
    ISection: dict = {1: "bottom left", 2: "bottom right", 3: "top left", 4: "top right"}
