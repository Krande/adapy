"""The bake over chosen steps, its progress callback, and the step-major write.

A synthetic reader stands in for a many-step result: the committed Sesam
fixtures have one step each, which cannot tell a subset from the whole.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import numpy as np
import pytest

from ada.fem.results.artefacts import (
    bake_artefacts,
    read_blob_header,
    read_blob_step,
    read_elem_field_blob_header,
    read_elem_field_blob_step,
)
from ada.fem.results.artefacts.specs import (
    ElementFieldSpec,
    ElementStepValues,
    FieldSpec,
    MeshGeometry,
    StepValues,
)
from ada.fem.results.common import CellBlockData

STEP_VALUES = [1.0, 2.0, 5.0, 9.0]
N_POINTS = 4


def _nodal(step: float) -> np.ndarray:
    return np.arange(N_POINTS * 3, dtype=np.float32).reshape(N_POINTS, 3) + 100.0 * step


def _element(step: float, n_ips: int = 2, n_comp: int = 3) -> np.ndarray:
    return (np.arange(n_ips * n_comp, dtype=np.float32).reshape(1, n_ips, n_comp) - 7.0) * step


class _SyntheticReader:
    """Four steps (values 1, 2, 5, 9); one nodal field, one element field, one property field.

    ``decodes`` counts how often each step is "decoded": a step decoded for one
    field is kept until another step is asked for, the way the SIN stream
    reader keeps it, so a step-major bake decodes each step once.
    """

    def __init__(self, *, step_major: bool = False) -> None:
        self.step_major_bake = step_major
        self.decodes: dict[float, int] = {}
        self._current: float | None = None

    def _decode(self, value: float) -> None:
        if self._current != value:
            self._current = value
            self.decodes[value] = self.decodes.get(value, 0) + 1

    def read_mesh_geometry(self) -> MeshGeometry:
        points = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float)
        block = CellBlockData(cell_type="quad", data=np.array([[0, 1, 2, 3]]), identifiers=np.array([7]))
        return MeshGeometry(points=points, cell_blocks=[block], node_labels=[1, 2, 3, 4])

    def field_specs(self):
        return [
            FieldSpec(
                name="DISP",
                components=["UX", "UY", "UZ"],
                n_steps=len(STEP_VALUES),
                n_points=N_POINTS,
                support="nodal",
                step_values=list(STEP_VALUES),
                category="displacement",
                analysis_kind="static",
            )
        ]

    def element_field_specs(self):
        stress = ElementFieldSpec(
            name="STRESS",
            components=["SXX", "SYY", "SXY"],
            n_steps=len(STEP_VALUES),
            elem_type="quad",
            n_elements=1,
            n_ips=2,
            element_labels=[7],
            step_values=list(STEP_VALUES),
            element_node_indices=[[0, 1, 2, 3]],
            category="stress",
        )
        thickness = ElementFieldSpec(
            name="props.thickness",
            components=["t"],
            n_steps=1,
            elem_type="quad",
            n_elements=1,
            n_ips=1,
            element_labels=[7],
            step_values=[0.0],
            element_node_indices=[[0, 1, 2, 3]],
            category="property",
            support="element_average",
        )
        return [stress, thickness]

    def iter_field_steps(self, field_name: str):
        assert field_name == "DISP"
        for i, v in enumerate(STEP_VALUES):
            self._decode(v)
            yield StepValues(step_index=i, step_value=v, values=_nodal(v))

    def iter_element_field_steps(self, spec):
        if spec.name == "props.thickness":
            yield ElementStepValues(step_index=0, step_value=0.0, values=np.full((1, 1, 1), 0.02, dtype=np.float32))
            return
        for i, v in enumerate(STEP_VALUES):
            self._decode(v)
            yield ElementStepValues(step_index=i, step_value=v, values=_element(v))

    def try_solid_beams(self, **kwargs):
        return None

    def try_history_records(self):
        return None

    def close(self) -> None:
        pass


def _digests(d: pathlib.Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir()) if p.is_file()}


def _manifest(d: pathlib.Path) -> dict:
    return json.loads((d / "fea.manifest.json").read_text(encoding="utf-8"))


def test_default_bake_is_unchanged_by_the_new_options(tmp_path):
    """steps=None bakes every step; a progress callback changes no byte."""
    plain, watched = tmp_path / "plain", tmp_path / "watched"
    bake_artefacts(_SyntheticReader(), plain, src="s")
    bake_artefacts(_SyntheticReader(), watched, src="s", on_progress=lambda *a: None)
    assert _digests(plain) == _digests(watched)
    manifest = _manifest(plain)
    assert "baked_steps" not in manifest
    disp = next(f for f in manifest["fields"] if f["name_canonical"] == "DISP")
    assert [s["value"] for s in disp["steps"]] == STEP_VALUES


def test_subset_bake_writes_only_the_chosen_steps(tmp_path):
    whole, part = tmp_path / "whole", tmp_path / "part"
    bake_artefacts(_SyntheticReader(), whole, src="s")
    bake_artefacts(_SyntheticReader(), part, src="s", steps=[9, 2])

    manifest = _manifest(part)
    assert manifest["baked_steps"] == [2, 9]
    disp = next(f for f in manifest["fields"] if f["name_canonical"] == "DISP")
    assert disp["n_steps"] == 2
    assert [(s["i"], s["value"]) for s in disp["steps"]] == [(0, 2.0), (1, 9.0)]

    assert read_blob_header(part / "fea.DISP.bin")["n_steps"] == 2
    for k, i in ((0, 1), (1, 3)):  # the subset's step k is the whole bake's step i
        assert read_blob_step(part / "fea.DISP.bin", k).tobytes() == read_blob_step(whole / "fea.DISP.bin", i).tobytes()
        assert np.array_equal(read_blob_step(part / "fea.DISP.bin", k), _nodal(STEP_VALUES[i]))
        assert (
            read_elem_field_blob_step(part / "fea.STRESS.quad.elements.bin", k).tobytes()
            == read_elem_field_blob_step(whole / "fea.STRESS.quad.elements.bin", i).tobytes()
        )
    # A property field does not vary by step and is kept whole.
    assert read_elem_field_blob_header(part / "fea.props.thickness.quad.elements.bin")["n_steps"] == 1
    # The mesh does not depend on which steps are baked.
    for name in ("fea.mesh.glb", "fea.mesh.edges.bin", "fea.mesh.elements.bin"):
        assert (part / name).read_bytes() == (whole / name).read_bytes()


def test_a_step_the_result_does_not_have_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="steps not in this result: \\[3\\]"):
        bake_artefacts(_SyntheticReader(), tmp_path / "x", src="s", steps=[2, 3])
    with pytest.raises(ValueError, match="empty"):
        bake_artefacts(_SyntheticReader(), tmp_path / "y", src="s", steps=[])


def test_progress_counts_field_steps_with_the_total_known_up_front(tmp_path):
    calls: list[tuple[int, int, str]] = []
    bake_artefacts(_SyntheticReader(), tmp_path / "b", src="s", on_progress=lambda *a: calls.append(a))
    # One unit per step of each field: 4 nodal + 4 stress + 1 property.
    assert [c[0] for c in calls] == list(range(1, 10))
    assert {c[1] for c in calls} == {9}
    assert calls[0][2] == "DISP step 1"
    assert calls[-1][2] == "props.thickness step 0"


def test_step_major_bake_decodes_each_step_once_and_writes_the_same_bytes(tmp_path):
    field_major, step_major = _SyntheticReader(), _SyntheticReader(step_major=True)
    calls: list[tuple[int, int, str]] = []
    # The sink forces the field-major order even for a step-major reader: a
    # caller shipping each blob as it completes must still get them one by one.
    shipped: list[str] = []
    bake_artefacts(field_major, tmp_path / "fm", src="s", on_artefact=lambda p: shipped.append(p.name))
    bake_artefacts(step_major, tmp_path / "sm", src="s", on_progress=lambda *a: calls.append(a))

    assert _digests(tmp_path / "fm") == _digests(tmp_path / "sm")
    assert field_major.decodes == {v: 2 for v in STEP_VALUES}  # once per field
    assert step_major.decodes == {v: 1 for v in STEP_VALUES}  # once for all fields
    # One unit per step of every field at once.
    assert calls == [(1, 4, "step 1"), (2, 4, "step 2"), (3, 4, "step 5"), (4, 4, "step 9")]
    assert shipped[-1] == "fea.manifest.json"


def test_step_major_subset_bake(tmp_path):
    reader = _SyntheticReader(step_major=True)
    calls: list[tuple[int, int, str]] = []
    bake_artefacts(reader, tmp_path / "b", src="s", steps=[5], on_progress=lambda *a: calls.append(a))
    assert calls == [(1, 1, "step 5")]
    assert np.array_equal(read_blob_step(tmp_path / "b" / "fea.DISP.bin", 0), _nodal(5.0))
