"""Stream-reader registry: per-suffix factories for the supported result formats."""

from __future__ import annotations

import os
import pathlib
from typing import Callable, Iterable

import numpy as np

from .protocol import FEAStreamReader
from .step_subset import normalize_steps, restrict_to_steps
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


#: The most result rows (RV* records, over every case including the load
#: combinations still to be superposed) a SIN is materialised whole with, unless
#: told otherwise. Materialised, a row costs a couple of kilobytes once its derived
#: fields are counted, so this is a peak of the order of 4-5 GB.
SIN_MATERIALISE_MAX_ROWS = 2_000_000


def _sin_too_big_to_materialise(path: pathlib.Path, max_rows: int | None = None) -> bool:
    """Whether reading ``path`` whole would hold more than ``max_rows`` result rows.

    Cheap: the record counts come from the pointer tables, the stored cases from
    each RV* record's first word, the combinations from their recipes -- no
    result value is read. Unstored combinations count as the average stored
    case's rows each, which is what superposing them materialises. A file that
    cannot be sized this way is left to the default reader.
    """
    from ada.fem.formats.sesam.results.read_sin import (
        _RV_TYPE_NAMES,
        SinReader,
        read_result_combination_terms,
    )
    from ada.fem.formats.sesam.results.sin_reader import open_sin

    limit = SIN_MATERIALISE_MAX_ROWS if max_rows is None else max_rows
    try:
        with open_sin(str(path)) as sin:
            rows = sum(
                int(np.count_nonzero(sin.type_blocks[name].pointer_table))
                for name in _RV_TYPE_NAMES
                if name in sin.type_blocks
            )
            if rows > limit:
                return True
            if not rows:
                return False
            stored = SinReader(sin=sin).stored_steps()
            unstored = sum(
                1 for ires, recipe in read_result_combination_terms(sin).items() if recipe and ires not in stored
            )
    except Exception:  # noqa: BLE001 - sizing is advice; the reader proper reports a bad file
        return False
    if not stored or not unstored:
        return False
    return rows * (len(stored) + unstored) / len(stored) > limit


def _make_sin_reader(path: pathlib.Path, steps: "Iterable[int] | None" = None) -> "FEAStreamReader":
    # Pure-Python Sesam Norsam-binary reader (see
    # ada.fem.formats.sesam.results.read_sin). No Prepost.exe shell-out
    # and no SIF text intermediate — feeds the streaming bake directly.
    #
    # The full-materialise adapter is the faster of the two and enriches step
    # labels from SESTRA.LIS eigen-frequencies; the per-step SinStreamReader is
    # slower but its peak RSS stays flat in step count. On the streamer path step
    # labels fall back to the IRES mode index (no LIS enrichment).
    #
    # ADA_FEA_SIN_STREAMER (the admin "Stream SIN FEA bake" toggle) picks one:
    # on -> always stream, off -> always materialise. Unset, a deck whose result
    # tables would not fit is streamed (_sin_too_big_to_materialise): a deck that
    # stores 13 cases and leaves 90 load combinations to superpose needs over
    # 40 GB materialised and about 3 GB streamed.
    #
    # ``steps`` (result-case numbers) always takes the streamer, restricted to
    # those cases: the full-materialise adapter reads every case before it can
    # offer one, which on a deck of a few hundred combinations is hours and tens
    # of gigabytes for the sake of one case.
    import os

    choice = os.environ.get("ADA_FEA_SIN_STREAMER", "").strip().lower()
    if (
        steps is not None
        or choice in {"1", "true", "yes", "on"}
        or (choice not in {"0", "false", "no", "off"} and _sin_too_big_to_materialise(path))
    ):
        from ada.fem.formats.sesam.results.read_sin import SinStreamReader
        from ada.fem.formats.sesam.results.sin_reader import open_sin

        return SinStreamReader(open_sin(str(path)), steps=steps)

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


def _make_radanim_reader(path: pathlib.Path) -> "FEAStreamReader":
    from ada.fem.formats.opencourant.results.container import make_radanim_stream_reader

    return make_radanim_stream_reader(path)


def _ensure_builtin_stream_readers() -> None:
    if getattr(_ensure_builtin_stream_readers, "_done", False):
        return
    # setdefault: a downstream registration for the same suffix wins.
    _STREAM_READERS.setdefault(".rmed", _make_rmed_reader)
    _STREAM_READERS.setdefault(".sif", _make_sif_reader)
    _STREAM_READERS.setdefault(".sin", _make_sin_reader)
    # OpenCourant explicit runs: a zip of the raw animation states (see
    # ada.fem.formats.opencourant.results.container).
    _STREAM_READERS.setdefault(".radanim", _make_radanim_reader)
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


def make_stream_reader(src_path: os.PathLike, *, steps: Iterable[int] | None = None) -> FEAStreamReader:
    """Open the right streaming reader for a source file's extension.

    Dispatch goes through ``_STREAM_READERS``; built-ins (``.rmed`` /
    ``.sif``) self-register on first call. Caller is responsible for
    closing the returned reader (use as a context manager).

    ``steps``: offer only these steps, by value (for Sesam, the result-case
    numbers). A factory that takes ``steps`` gets them and can skip reading the
    rest (``.sin`` does); any other reader is narrowed by
    :func:`~.step_subset.restrict_to_steps`. None, the default, offers every
    step, as before."""

    _ensure_builtin_stream_readers()
    src_path = pathlib.Path(src_path)
    ext = src_path.suffix.lower()
    factory = _STREAM_READERS.get(ext)
    if factory is None:
        raise ValueError(
            f"no streaming reader for FEA source extension {ext!r}; " f"registered: {sorted(_STREAM_READERS)}"
        )
    if steps is None:
        return factory(src_path)
    steps = normalize_steps(steps)
    if _takes_steps(factory):
        return factory(src_path, steps=steps)
    return restrict_to_steps(factory(src_path), steps)


def _takes_steps(factory: _StreamReaderFactory) -> bool:
    import inspect

    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return False
    return "steps" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
