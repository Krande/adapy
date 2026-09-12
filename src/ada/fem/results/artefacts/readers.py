"""Stream-reader registry: per-suffix factories for the supported result formats."""

from __future__ import annotations

import os
import pathlib
from typing import Callable

import numpy as np

from .protocol import FEAStreamReader
from .stream_adapter import FEAResultStreamAdapter

# ---------------------------------------------------------------------------
# Bake orchestrator
# ---------------------------------------------------------------------------


_StreamReaderFactory = Callable[[pathlib.Path], "FEAStreamReader"]


_STREAM_READERS: dict[str, _StreamReaderFactory] = {}


def register_stream_reader(suffix: str, factory: _StreamReaderFactory) -> None:
    """Register a streaming-reader factory for files ending in ``suffix``.

    ``factory(path)`` must return an object satisfying ``FEAStreamReader``.
    Registrations override built-ins for the same suffix — downstream
    packages (e.g. an Abaqus-aware worker registering ``.odb``) should
    call this at startup before any bake call."""

    _STREAM_READERS[suffix] = factory


def _make_rmed_reader(path: pathlib.Path) -> "FEAStreamReader":
    from ada.fem.formats.code_aster.read.med_stream_reader import RmedStreamReader

    return RmedStreamReader(path)


def _make_sif_reader(path: pathlib.Path) -> "FEAStreamReader":
    # Default: the per-step SifStreamReader — peak result RSS stays flat in step
    # count (one step resident at a time, via the byte-offset index), so a
    # many-mode deck can't OOM the bake. It streams per *field* (each RV card is
    # one contiguous block), so it's a single pass, ~+11% wall vs the full
    # materialise, and it enriches nodal step labels from SESTRA.LIS just like
    # the adapter. ADA_FEA_SIF_STREAMER=0/false/off forces the full-materialise
    # adapter (e.g. to rule the streamer out when triaging).
    import os

    if os.environ.get("ADA_FEA_SIF_STREAMER", "").strip().lower() in {"0", "false", "no", "off"}:
        from ada.fem.formats.sesam.results.read_sif import read_sif_file

        return FEAResultStreamAdapter(read_sif_file(path))

    from ada.fem.formats.sesam.results.sif_stream import SifStreamReader

    return SifStreamReader(path)


def _make_sin_reader(path: pathlib.Path) -> "FEAStreamReader":
    # Pure-Python Sesam Norsam-binary reader (see
    # ada.fem.formats.sesam.results.read_sin). No Prepost.exe shell-out
    # and no SIF text intermediate — feeds the streaming bake directly.
    #
    # Default: the full-materialise adapter — fastest, and it enriches
    # step labels from SESTRA.LIS eigen-frequencies. The admin "Stream
    # SIN FEA bake" toggle sets ADA_FEA_SIN_STREAMER to opt into the
    # per-step SinStreamReader instead: ~1.7x slower but peak RSS stays
    # flat in step count, for many-mode / large decks whose full result
    # would OOM the worker. On the streamer path step labels fall back to
    # the IRES mode index (no LIS enrichment).
    import os

    if os.environ.get("ADA_FEA_SIN_STREAMER", "").strip().lower() in {"1", "true", "yes", "on"}:
        from ada.fem.formats.sesam.results.read_sin import SinStreamReader
        from ada.fem.formats.sesam.results.sin_reader import open_sin

        return SinStreamReader(open_sin(str(path)))

    from ada.fem.formats.sesam.results.read_sin import read_sin_file

    return FEAResultStreamAdapter(read_sin_file(path))


def _make_fem_reader(path: pathlib.Path) -> "FEAStreamReader":
    """Stream-reader for a results-less FEM mesh (.inp / .fem).

    A design-model FEM deck is a mesh with no solver results. Reading it through the same
    streaming-artefact pipeline as real results (mesh + edges + beam-solids, just an empty
    fields list) is what unifies FE-mesh visualisation onto one path. Multipart decks are
    merged first; ``FEM.to_mesh()`` supplies the section/material tables the beam-solid
    tessellation needs.
    """
    import ada
    from ada.fem.concat import concatenate_fem_meshes
    from ada.fem.results.common import ElementBlock, FEAResult

    assembly = ada.from_fem(path)
    parts = [
        p for p in assembly.get_all_parts_in_assembly(include_self=True) if p.fem is not None and len(p.fem.nodes) > 0
    ]
    if not parts:
        raise ValueError(f"no FEM mesh found in {path}")

    # Non-destructive merge: keep each part's FEM in the assembly tree (concepts are scraped
    # from the un-merged assembly below) while producing one merged Mesh for the viewer.
    # FEM.to_mesh() per part supplies the section/material tables the beam-solid path needs.
    mesh, part_offsets = concatenate_fem_meshes(parts)
    # to_elem_blocks() emits row-index node_refs (array-substrate convention); the adapter +
    # geometry/field readers expect node IDs (they remap id->index). Convert once here.
    ids = mesh.nodes.identifiers
    rebuilt: list[ElementBlock] = []
    for b in mesh.elements:
        if b.node_refs_are_indices:
            nref = ids[np.asarray(b.node_refs)]
            rebuilt.append(ElementBlock(b.elem_info, nref, b.identifiers, node_refs_are_indices=False))
        else:
            rebuilt.append(b)
    mesh.elements = rebuilt
    # What the elements ARE — thickness, material, section — as category
    # "property" fields, from each element's FemSection. Same fields the Sesam
    # result reader emits, so Inspect's property colouring works on a design
    # deck or an exported input deck too. Best-effort decoration: a deck the
    # builder cannot describe still bakes as a plain mesh.
    try:
        from ada.fem.formats.sesam.results.property_fields import (
            build_property_fields_from_fem,
        )

        property_fields = build_property_fields_from_fem(parts, part_offsets, mesh)
    except Exception as e:  # noqa: BLE001
        from ada.config import get_logger

        get_logger().warning("FEM bake: property fields skipped: %s", e)
        property_fields = []
    result = FEAResult(name=parts[0].fem.name or path.stem, software="adapy", results=property_fields, mesh=mesh)
    reader = FEAResultStreamAdapter(result)
    # Scrape FEA input concepts (masses / BCs / load scenarios) so the Scene > FEM panel can
    # draw the glyph overlay. Built from the (intact) assembly — positions are coordinates.
    try:
        from ada.extension.fem_concepts_builder import build_combined_fem_concepts

        fc = build_combined_fem_concepts(assembly)
        if fc is not None:
            reader._fem_concepts = fc.model_dump(mode="json", exclude_none=True)
    except Exception as e:  # noqa: BLE001 — concepts are best-effort decoration
        from ada.config import get_logger

        get_logger().debug("FEM bake: fem_concepts scrape failed: %s", e)

    # Scrape each part's node/element sets into manifest groups for the Scene > FEM groups picker
    # (the streaming mesh.glb carries no ADA_EXT). Member ids carry the same per-part offset as
    # the merged mesh so EL{id}/P{id} resolve against the AFEM element ranges. Multi-part set
    # names are prefixed with the part name to disambiguate.
    try:
        groups: list[dict] = []
        multipart = len(parts) > 1
        for p, (nid_off, elid_off) in zip(parts, part_offsets):
            for fset in p.fem.sets:
                is_nset = fset.type == fset.TYPES.NSET
                off = nid_off if is_nset else elid_off
                prefix = "P" if is_nset else "EL"
                mids = getattr(fset, "_member_ids", None)
                if mids is None:
                    mids = [m.id for m in fset.members if getattr(m, "id", None) is not None]
                members = [f"{prefix}{int(i) + off}" for i in mids]
                if members:
                    name = f"{p.name}_{fset.name}" if multipart else fset.name
                    groups.append(
                        {"name": name, "members": members, "fe_object_type": "node" if is_nset else "element"}
                    )
        reader._groups = groups or None
    except Exception as e:  # noqa: BLE001 — groups are best-effort decoration
        from ada.config import get_logger

        get_logger().debug("FEM bake: groups scrape failed: %s", e)
    return reader


def _ensure_builtin_stream_readers() -> None:
    if getattr(_ensure_builtin_stream_readers, "_done", False):
        return
    # setdefault: a downstream registration for the same suffix wins.
    _STREAM_READERS.setdefault(".rmed", _make_rmed_reader)
    _STREAM_READERS.setdefault(".sif", _make_sif_reader)
    _STREAM_READERS.setdefault(".sin", _make_sin_reader)
    # Design-model FEM meshes flow through the same streaming bake (mesh + beam-solids, no
    # result fields) so FE-mesh visualisation has a single path. (.rmed keeps its native
    # results streamer above; plain .med is a mesh-only deck read via from_fem.)
    _STREAM_READERS.setdefault(".inp", _make_fem_reader)
    _STREAM_READERS.setdefault(".fem", _make_fem_reader)
    _STREAM_READERS.setdefault(".med", _make_fem_reader)
    _ensure_builtin_stream_readers._done = True  # type: ignore[attr-defined]


def fea_artefact_extensions() -> frozenset[str]:
    """Set of source-file suffixes the streaming bake can open."""

    _ensure_builtin_stream_readers()
    return frozenset(_STREAM_READERS)


def is_fea_artefact_source(src_key_or_path) -> bool:
    """True if the source extension is in scope for the streaming bake."""

    suffix = pathlib.PurePosixPath(str(src_key_or_path)).suffix.lower()
    return suffix in fea_artefact_extensions()


def make_stream_reader(src_path: os.PathLike) -> FEAStreamReader:
    """Open the right streaming reader for a source file's extension.

    Dispatch goes through ``_STREAM_READERS``; built-ins (``.rmed`` /
    ``.sif``) self-register on first call. Caller is responsible for
    closing the returned reader (use as a context manager)."""

    _ensure_builtin_stream_readers()
    src_path = pathlib.Path(src_path)
    ext = src_path.suffix.lower()
    factory = _STREAM_READERS.get(ext)
    if factory is None:
        raise ValueError(
            f"no streaming reader for FEA source extension {ext!r}; " f"registered: {sorted(_STREAM_READERS)}"
        )
    return factory(src_path)
