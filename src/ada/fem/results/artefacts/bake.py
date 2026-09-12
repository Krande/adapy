"""Bake orchestrator: stream a result source into the on-disk artefact set."""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Callable

from .beam_solids import (
    write_beam_solids_elements,
    write_beam_solids_glb,
    write_beam_solids_warp,
)
from .fields import write_element_field_blob_streaming, write_field_blob_streaming
from .manifest import build_manifest, write_manifest
from .mesh import (
    _compute_topology,
    write_mesh_edges,
    write_mesh_elements,
    write_mesh_glb,
    write_mesh_line_edges,
)
from .protocol import FEAStreamReader
from .readers import make_stream_reader
from .specs import ElementFieldArtefactMeta, FieldArtefactMeta


def bake_fea_artefacts_from_source(
    src_path: os.PathLike,
    out_dir: os.PathLike,
    *,
    src_key: str = "",
    source_sha256: str | None = None,
    legacy_glb_url_template: str | None = None,
    include_beam_solids: bool = True,
) -> "BakeResult":
    """End-to-end bake from a source file path. Picks the right
    reader for the extension and drives the streaming bake. Raises
    ``ValueError`` for unsupported extensions; the caller (REST
    endpoint, CLI, tests) is responsible for the policy decision of
    when to surface that vs route to a different code path."""

    src_path = pathlib.Path(src_path)
    src = src_key or src_path.stem
    with make_stream_reader(src_path) as reader:
        return bake_artefacts(
            reader,
            out_dir,
            src=src,
            source_sha256=source_sha256,
            legacy_glb_url_template=legacy_glb_url_template,
            include_beam_solids=include_beam_solids,
        )


@dataclass
class BakeResult:
    out_dir: pathlib.Path
    manifest_path: pathlib.Path
    mesh_glb_path: pathlib.Path
    field_blob_paths: list[pathlib.Path] = dc_field(default_factory=list)


def bake_artefacts(
    reader: FEAStreamReader,
    out_dir: os.PathLike,
    *,
    src: str = "",
    source_sha256: str | None = None,
    legacy_glb_url_template: str | None = None,
    nodal_only: bool = True,
    include_element_fields: bool = True,
    include_beam_solids: bool = True,
    on_artefact: Callable[[pathlib.Path], None] | None = None,
) -> BakeResult:
    """Drive the streaming bake end-to-end.

    Nodal fields produce one AFBL blob each. Element fields (gauss /
    element_nodal) produce one AFEL blob per (field, element-type);
    these are grouped under one manifest record per logical field
    with ``per_type`` buckets. Set ``include_element_fields=False``
    to skip the element-field emission entirely — useful for tests
    that only exercise the nodal path.

    ``nodal_only`` is kept as a backwards-compat alias: when True,
    ``iter_field_steps`` callers still drop non-nodal specs (the
    nodal blob writer can't handle them). Element fields flow
    through the new ``iter_element_field_steps`` path instead.

    Set ``include_beam_solids=False`` to skip section-profile
    tessellation while retaining the line mesh and its result fields.
    This is useful for lightweight or headless bakes, and for native
    geometry environments where beam-solid generation is unavailable.

    ``on_artefact``: optional sink invoked with each artefact file's
    path *immediately after it is fully written* (the manifest last).
    It lets a caller ship each file as it lands — e.g. the in-browser
    bake uploads and then unlinks each blob, so the output tree never
    has to reside whole in wasm memory before a zip. Manifest
    construction reads only in-memory metas (never the blob bytes), so
    a sink that deletes the file after shipping it is safe. The
    returned ``BakeResult`` still lists every path; whether those
    files survive on disk is the sink's choice."""

    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def emit(path: pathlib.Path) -> None:
        if on_artefact is not None:
            on_artefact(path)

    geom = reader.read_mesh_geometry()

    # One topology walk feeds three writers (GLB faces, edge sidecar,
    # element sidecar). Each writer used to walk per-element shapes
    # independently — for a 100k-element mesh the savings from a
    # single pass are non-trivial and the AFEM ranges have to come
    # out of the same iteration order as the GLB faces or selection
    # would target the wrong triangles.
    topology = _compute_topology(geom)

    mesh_glb_path = out_dir / "fea.mesh.glb"
    write_mesh_glb(geom, mesh_glb_path, faces=topology.faces)
    emit(mesh_glb_path)

    # Element edges (deduped) — frontend renders them as a
    # LineSegments overlay sharing the mesh's position attribute,
    # so the wireframe shows actual element boundaries (not the
    # arbitrary diagonals from quad-face triangulation) and follows
    # the deformation automatically.
    mesh_edges_path = out_dir / "fea.mesh.edges.bin"
    n_edges = write_mesh_edges(geom, mesh_edges_path, edges=topology.edges)
    emit(mesh_edges_path)

    # Which of those edges are beams, so the viewer can draw them in their own
    # colour instead of losing them in the shell grid. Omitted entirely when the
    # model has no line elements.
    mesh_line_edges_path = out_dir / "fea.mesh.line_edges.bin"
    n_line_edges = write_mesh_line_edges(geom, mesh_line_edges_path)
    if n_line_edges:
        emit(mesh_line_edges_path)
    else:
        mesh_line_edges_path.unlink(missing_ok=True)
        mesh_line_edges_path = None

    # Per-element draw ranges — frontend hydrates these into
    # userdata.id_hierarchy + userdata.draw_ranges_<meshName> so the
    # FEA mesh enters the existing CustomBatchedMesh pick + highlight
    # pipeline without a parallel selection path.
    mesh_elements_path = out_dir / "fea.mesh.elements.bin"
    n_elements = write_mesh_elements(geom, mesh_elements_path, element_ranges=topology.element_ranges)
    emit(mesh_elements_path)

    # Beam-solid mesh — optional, depends on whether the reader has
    # section + axis info per beam (SIF via FEAResultStreamAdapter
    # today). Skipped silently when the reader returns None: the
    # frontend reads the manifest and falls back to line-only beam
    # rendering when ``beam_solids_url`` is absent.
    beam_solids_glb_path: pathlib.Path | None = None
    beam_solids_elements_path: pathlib.Path | None = None
    n_beam_solids = 0
    solid_beams = None
    if include_beam_solids:
        try:
            solid_beams = reader.try_solid_beams()
        except (AttributeError, NotImplementedError):
            pass
    beam_solids_warp_path: pathlib.Path | None = None
    n_beam_solid_verts = 0
    if solid_beams is not None and solid_beams.triangles.size:
        beam_solids_glb_path = out_dir / "fea.beam_solids.glb"
        write_beam_solids_glb(solid_beams, beam_solids_glb_path)
        emit(beam_solids_glb_path)
        beam_solids_elements_path = out_dir / "fea.beam_solids.elements.bin"
        n_beam_solids = write_beam_solids_elements(solid_beams, beam_solids_elements_path)
        emit(beam_solids_elements_path)
        # AFBV warp mapping — every solid vertex's parent beam
        # endpoints + axial parameter. Skip when the reader didn't
        # populate the vertex_* arrays (defensive: the SIF adapter
        # always does, but a future reader might omit it).
        if solid_beams.vertex_node0.size:
            beam_solids_warp_path = out_dir / "fea.beam_solids.warp.bin"
            n_beam_solid_verts = write_beam_solids_warp(solid_beams, beam_solids_warp_path)
            emit(beam_solids_warp_path)
        # AFEG element-boundary wireframe for the solid mesh. Without
        # this the beam solids render as one continuous tube — see the
        # writer docstring for the boundary-edge rules.
        # No beam-solid section outline. It drew the perimeter and end seams of
        # each extruded section, which is tessellation rather than mesh: the FE
        # model has no such edges, and a beam's mesh line is its element line
        # whether or not a section is drawn around it. The viewer stopped
        # consuming it; writing it was ~58 KB per bake of nothing.
        pass

    field_metas: list[FieldArtefactMeta] = []
    blob_paths: list[pathlib.Path] = []
    for spec in reader.field_specs():
        if nodal_only and spec.support != "nodal":
            continue
        blob_path = out_dir / f"fea.{spec.name}.bin"
        meta = write_field_blob_streaming(reader, spec, blob_path)
        field_metas.append(meta)
        blob_paths.append(blob_path)
        emit(blob_path)

    elem_field_metas: list[ElementFieldArtefactMeta] = []
    if include_element_fields:
        # Best-effort: a reader that hasn't implemented the
        # element-field protocol yet (returns from a Protocol stub or
        # raises NotImplementedError) just contributes no element
        # buckets. Surface AttributeError as the explicit signal so
        # other failures still bubble up.
        try:
            elem_specs = reader.element_field_specs()
        except (AttributeError, NotImplementedError):
            elem_specs = []
        for es in elem_specs:
            # Filename includes elem_type so each (field, type) bucket
            # gets a distinct file the frontend can range-fetch.
            blob_path = out_dir / f"fea.{es.name}.{es.elem_type}.elements.bin"
            em = write_element_field_blob_streaming(reader, es, blob_path)
            elem_field_metas.append(em)
            blob_paths.append(blob_path)
            emit(blob_path)

    # History output — time series at monitored points. Optional; the
    # bake tolerates readers that pre-date the method (AttributeError)
    # and readers that simply have no history data for this source
    # (None return).
    try:
        history = reader.try_history_records()
    except (AttributeError, NotImplementedError):
        history = None

    # CAD↔FEA lineage. Pulled from a format-specific sidecar (e.g.
    # ``<name>.adapy_fem.json`` for code_aster) that adapy's FEM
    # writer stamps at deck-write time. Readers that don't implement
    # the method, or sources without an adapy-written sidecar, just
    # produce no lineage and the manifest entry is omitted.
    try:
        lineage = reader.try_lineage()
    except (AttributeError, NotImplementedError):
        lineage = None

    # FEA input concepts (masses / BCs / load scenarios). Same sidecar
    # source as lineage — present only when adapy wrote the deck (the
    # .rmed itself has no inputs). Readers that pre-date the method, or
    # sources without a v5 sidecar, contribute nothing and the manifest
    # key is omitted.
    try:
        fem_concepts = reader.try_fem_concepts()
    except (AttributeError, NotImplementedError):
        fem_concepts = None

    # FEM node/element sets -> manifest groups (Scene > FEM groups picker, and the
    # Outliner's Groups branch). Sesam decks (SIF/SIN) report their TDSETNAM /
    # GSETMEMB sets; a reader without the method contributes nothing.
    try:
        groups = reader.try_groups()
    except (AttributeError, NotImplementedError):
        groups = None

    # What the deck calls each result case; a reader without the method leaves
    # the steps labelled by value, as before.
    try:
        step_names = reader.try_step_names()
    except (AttributeError, NotImplementedError):
        step_names = None

    # And every case it offers, stored or superposed.
    try:
        result_cases = reader.try_result_cases()
    except (AttributeError, NotImplementedError):
        result_cases = None

    manifest = build_manifest(
        src=src,
        source_sha256=source_sha256,
        mesh_geom=geom,
        mesh_glb_filename=mesh_glb_path.name,
        field_metas=field_metas,
        elem_field_metas=elem_field_metas,
        mesh_edges_filename=mesh_edges_path.name,
        mesh_line_edges_filename=(mesh_line_edges_path.name if mesh_line_edges_path else None),
        n_edges=n_edges,
        mesh_elements_filename=mesh_elements_path.name,
        n_elements=n_elements,
        history=history,
        beam_solids_glb_filename=(beam_solids_glb_path.name if beam_solids_glb_path else None),
        beam_solids_elements_filename=(beam_solids_elements_path.name if beam_solids_elements_path else None),
        beam_solids_warp_filename=(beam_solids_warp_path.name if beam_solids_warp_path else None),
        n_beam_solids=n_beam_solids,
        n_beam_solid_verts=n_beam_solid_verts,
        n_beam_total=(solid_beams.total_beams if solid_beams is not None else 0),
        beam_solids_skip_reasons=(solid_beams.skip_reasons if solid_beams is not None else None),
        lineage=lineage,
        fem_concepts=fem_concepts,
        groups=groups,
        step_names=step_names,
        result_cases=result_cases,
        legacy_glb_url_template=legacy_glb_url_template,
    )
    manifest_path = out_dir / "fea.manifest.json"
    write_manifest(manifest, manifest_path)
    emit(manifest_path)

    return BakeResult(
        out_dir=out_dir,
        manifest_path=manifest_path,
        mesh_glb_path=mesh_glb_path,
        field_blob_paths=blob_paths,
    )
