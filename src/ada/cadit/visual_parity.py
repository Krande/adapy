"""Cross-format visual-parity validation.

The same model exported to different structure-preserving formats (GLB, IFC,
Genie XML, STEP) and rendered must show the *same number of visualized
elements*. A divergence means a converter silently dropped, merged, or invented
geometry on the way through that format — exactly the class of audit failure
that a count-only smoke test misses (e.g. an empty scene, an IFC that imports no
geometry, a STEP that loses solids).

The metric is the number of renderable scene entries built with ``merge_meshes``
disabled, so each physical object maps to one entry (placeholder point clouds
that the converter seeds for empty scenes are not counted). Mesh-only formats
(STL/OBJ/PLY) are intentionally excluded: they carry no per-object identity and
always collapse to a single mesh soup, so they cannot preserve an element count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ada.config import logger

if TYPE_CHECKING:
    import trimesh

    from ada import Assembly

# Structure-preserving formats: (writer(assembly, path), reader(path) -> Assembly, suffix).
# STEP is written via the OCC writer (full geometry, not just extrusions) and
# read via the streaming reader with an OCC fallback (reader="auto").
_FORMAT_IO: dict[str, tuple[Callable, Callable, str]] = {}


def _register_default_formats() -> None:
    if _FORMAT_IO:
        return
    import ada

    _FORMAT_IO["ifc"] = (lambda a, p: a.to_ifc(p), lambda p: ada.from_ifc(p), ".ifc")
    _FORMAT_IO["xml"] = (lambda a, p: a.to_genie_xml(p), lambda p: ada.from_genie_xml(p), ".xml")
    _FORMAT_IO["step"] = (lambda a, p: a.to_stp(p), lambda p: ada.from_step(p, reader="auto"), ".step")


def visualized_element_count(scene: "trimesh.Scene") -> int:
    """Number of renderable elements in a trimesh scene.

    Counts mesh / polyline entries one-per-object (build the scene with
    ``merge_meshes=False``); excludes the placeholder point cloud the converter
    seeds for otherwise-empty scenes.
    """
    import trimesh

    n = 0
    for geom in scene.geometry.values():
        if isinstance(geom, trimesh.PointCloud):
            continue  # empty-scene placeholder
        n += 1
    return n


def assembly_element_count(assembly: "Assembly") -> int:
    """Visualized-element count for an adapy Assembly (unmerged scene)."""
    return visualized_element_count(assembly.to_trimesh_scene(merge_meshes=False))


@dataclass
class ParityResult:
    counts: dict[str, int]  # format label -> visualized element count ("source" is the baseline)
    expected: int  # the baseline (source) count
    consistent: bool  # True iff every format matches the baseline
    mismatches: dict[str, int] = field(default_factory=dict)  # format -> count, for the ones that differ
    errors: dict[str, str] = field(default_factory=dict)  # format -> error message when that format failed to round-trip

    def summary(self) -> str:
        status = "OK" if self.consistent and not self.errors else "MISMATCH"
        parts = [f"{k}={v}" for k, v in self.counts.items()]
        if self.errors:
            parts += [f"{k}=ERR" for k in self.errors]
        return f"[{status}] expected={self.expected} " + " ".join(parts)


def load_assembly_auto(path: str | Path) -> "Assembly":
    """Load a source model from disk by suffix, using the same readers the parity
    round-trip exports through. STEP uses ``reader="auto"`` (streaming fast-path,
    OCC fallback) so a large source doesn't force an OCC load."""
    import ada

    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".ifc":
        return ada.from_ifc(p)
    if ext in (".step", ".stp"):
        return ada.from_step(p, reader="auto")
    if ext in (".sat", ".acis"):
        return ada.from_acis(p)
    if ext == ".xml":
        return ada.from_genie_xml(p)
    if ext in (".fem", ".inp", ".sif", ".sin"):
        return ada.from_fem(p)
    raise ValueError(f"visual_parity: no loader for source suffix {ext!r}")


def parity_for_source_file(
    path: str | Path,
    formats: tuple[str, ...] = ("ifc", "xml", "step"),
    *,
    work_dir: str | Path | None = None,
) -> ParityResult:
    """Load a source model from disk and run :func:`cross_format_parity` on it."""
    return cross_format_parity(load_assembly_auto(path), formats, work_dir=work_dir)


def cross_format_parity(
    assembly: "Assembly",
    formats: tuple[str, ...] = ("ifc", "xml", "step"),
    *,
    work_dir: str | Path | None = None,
) -> ParityResult:
    """Export ``assembly`` to each structure-preserving ``format``, reload it, and
    compare the visualized-element count against the source.

    Returns a :class:`ParityResult`. A format that fails to round-trip is recorded
    in ``errors`` (and treated as inconsistent) rather than aborting the others.
    """
    import tempfile

    _register_default_formats()

    baseline = assembly_element_count(assembly)
    counts: dict[str, int] = {"source": baseline}
    errors: dict[str, str] = {}

    tmp_ctx = None
    if work_dir is None:
        tmp_ctx = tempfile.TemporaryDirectory()
        work_dir = tmp_ctx.name
    work_dir = Path(work_dir)

    try:
        for fmt in formats:
            io = _FORMAT_IO.get(fmt)
            if io is None:
                errors[fmt] = f"unknown format {fmt!r}"
                continue
            writer, reader, suffix = io
            out = work_dir / f"parity{suffix}"
            try:
                writer(assembly, out)
                counts[fmt] = assembly_element_count(reader(out))
            except Exception as ex:  # noqa: BLE001 - record and continue with the other formats
                errors[fmt] = f"{type(ex).__name__}: {ex}"
                logger.warning(f"cross_format_parity: {fmt} round-trip failed: {ex}")
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()

    mismatches = {k: v for k, v in counts.items() if k != "source" and v != baseline}
    consistent = not mismatches and not errors
    return ParityResult(
        counts=counts, expected=baseline, consistent=consistent, mismatches=mismatches, errors=errors
    )
