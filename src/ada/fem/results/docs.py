"""adapy ↔ paradoc bridge for FEA result figures.

Defines the contract between adapy's FEA artefact bake (mesh GLB +
per-step field blobs + edge / element sidecars + per-mode static
posters) and the paradoc docs pipeline that turns those artefacts into
typed views (`ThreeDView` / `TableView` / `ScalarValue`).

Three layers, smallest to largest:

  * :class:`FeaDocAssets` — frozen descriptor returned by
    :func:`assets_for_docs`. Carries everything the paradoc side needs
    from one FEA case (paths, frequencies, solver name + version).
    No paradoc-specific knowledge.

  * :func:`to_paradoc_rows` — converts a :class:`FeaDocAssets` into the
    paradoc ``ThreeDData`` rows (one canonical row + N mode-view rows)
    the static-export step copies into the bundle. Direct paradoc
    import here; this module is the paradoc bridge and a user opts in
    by importing it.

  * :class:`FeaCaseFilter` — stock paradoc ``Filter`` subclass for the
    one-case-per-instance use case. Bakes lazily on first attr access
    so a doc that registers many cases doesn't run the full bake set
    unless it actually references each.

The paradoc block-sugar entrypoint :func:`register_paradoc_block_sugar`
is declared as a ``paradoc.figure_sources`` entry point in
``pyproject.toml``. Step 6 of the migration fills in the dispatcher
side; this module stubs the registration target so the entry-point
resolves today.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass, field as dc_field
from typing import TYPE_CHECKING, Any, Iterable

from paradoc.db.models import ThreeDData
from paradoc.filters import Filter, ScalarValue, ThreeDView, attr

from ada.fem.results.artefacts import (
    BakeWithPostersResult,
    bake_with_posters,
    bake_with_posters_from_source,
)

if TYPE_CHECKING:  # pragma: no cover
    from ada.fem.results.artefacts import FEAStreamReader
    from ada.fem.results.common import FEAResult


# ---------------------------------------------------------------------------
# Descriptor
# ---------------------------------------------------------------------------


@dataclass
class FeaDocAssets:
    """Everything a doc build needs from one FEA case after the bake.

    Returned by :func:`assets_for_docs`; consumed by
    :func:`to_paradoc_rows` and :class:`FeaCaseFilter`.

    Attribute semantics:

    * ``poster_paths`` — keyed by the 0-based global mode index, same
      indexing the embed's ``mountFeaArtefactViewer(modeIndex=…)`` uses.
      Empty for opt-out callers (``modes=None``); contains every step
      for the docs-default ``modes="all"``.
    * ``canonical_poster_path`` — mode-0's PNG (also written at the
      ``<mesh_glb>.png`` sibling slot so paradoc's existing
      ``<glb>.png`` lookup finds it). ``None`` when there are no
      displacement fields (e.g. a static stress-only bake).
    * ``frequencies`` — per-mode eigenfrequencies (Hz). ``None`` for
      non-eigen analyses; otherwise ``frequencies[i]`` corresponds to
      mode index ``i`` (1-based mode N = ``frequencies[N-1]``).
    """

    key: str
    bundle_dir: pathlib.Path
    manifest_path: pathlib.Path
    mesh_glb_path: pathlib.Path
    poster_paths: dict[int, pathlib.Path] = dc_field(default_factory=dict)
    canonical_poster_path: pathlib.Path | None = None
    solver: str | None = None
    solver_version: str | None = None
    frequencies: list[float] | None = None

    @property
    def n_modes(self) -> int:
        """Number of static-poster modes baked. Independent of
        ``frequencies`` so a caller that opts out of posters still
        sees the right ``frequencies`` count from the manifest."""
        return len(self.poster_paths)


# ---------------------------------------------------------------------------
# Bake + descriptor population
# ---------------------------------------------------------------------------


def _src_is_pathlike(src: Any) -> bool:
    return isinstance(src, (str, pathlib.Path, pathlib.PurePath))


def _extract_solver_and_freqs(
    bake: BakeWithPostersResult,
    fea_result: Any,
) -> tuple[str | None, str | None, list[float] | None]:
    """Pull solver name + version + eigen frequencies from whatever
    sources are available.

    * Solver / version: prefer an in-hand :class:`FEAResult`
      (``fem_format`` + ``software_version`` are authoritative).
      Falls back to ``None`` for the path-driven case until the
      manifest grows a solver field.
    * Frequencies: always from the manifest's displacement-field
      ``steps[].value`` (eigen-kind only); the bake already writes
      these so we don't re-parse the source file.
    """
    solver: str | None = None
    solver_version: str | None = None
    frequencies: list[float] | None = None

    if fea_result is not None:
        # FEAResult exposes the solver kind as `software` (a `FEATypes`
        # enum) and the parsed version string as `software_version`.
        # Older code paths called it `fem_format`; check both so a
        # future rename doesn't silently zero this out.
        fmt = getattr(fea_result, "software", None) or getattr(
            fea_result, "fem_format", None
        )
        if fmt is not None:
            solver = str(getattr(fmt, "value", fmt))
        ver = getattr(fea_result, "software_version", None)
        if ver and ver != "N/A":
            solver_version = ver

    try:
        manifest = json.loads(bake.manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return solver, solver_version, frequencies

    for f in manifest.get("fields", []) or []:
        cat = (f.get("category") or "").lower()
        name = (f.get("name_canonical") or "").upper()
        kind = (f.get("analysis_kind") or "").lower()
        is_disp = cat == "displacement" or "DEPL" in name or name == "U"
        if not is_disp or kind != "eigen":
            continue
        steps = f.get("steps") or []
        if steps:
            # Walk every displacement-eigen field and concatenate —
            # Code Aster ships N fields × 1 step, others ship 1 field
            # × N steps. Concatenation gives the unified mode list
            # the embed + FeaCaseFilter both index by global mode.
            frequencies = (frequencies or []) + [
                float(s.get("value", 0.0)) for s in steps
            ]

    return solver, solver_version, frequencies


def assets_for_docs(
    src: "pathlib.Path | str | FEAResult | FEAStreamReader",
    *,
    key: str,
    out_dir: "pathlib.Path | str",
    modes: "str | int | Iterable[int] | None" = "all",
    poster_backend: str = "pygfx",
) -> FeaDocAssets:
    """Bake the bundle + posters and return a :class:`FeaDocAssets`.

    Accepts either an on-disk result file (``.frd`` / ``.odb`` /
    ``.rmed`` / ``.sif`` / ``.sin``) — handed to
    :func:`bake_with_posters_from_source` — or an in-memory
    :class:`FEAResult` / :class:`FEAStreamReader`, handed to
    :func:`bake_with_posters` directly. The output descriptor is
    paradoc-agnostic; :func:`to_paradoc_rows` and :class:`FeaCaseFilter`
    consume it on the paradoc side.

    ``modes`` follows :func:`bake_with_posters`' contract. Default
    ``"all"`` because the downstream doc may export to PDF / DOCX /
    ODT, and interactive figures must carry a static counterpart per
    mode in those formats.
    """
    out_dir = pathlib.Path(out_dir)
    fea_result: Any = None

    if _src_is_pathlike(src):
        bake = bake_with_posters_from_source(
            pathlib.Path(src),
            out_dir,
            src_key=key,
            modes=modes,
            poster_backend=poster_backend,
        )
    elif hasattr(src, "read_mesh_geometry"):
        # FEAStreamReader (Protocol).
        bake = bake_with_posters(
            src, out_dir, src=key, modes=modes, poster_backend=poster_backend,
        )
    elif hasattr(src, "results"):
        # FEAResult — keep the reference so we can pull fem_format +
        # software_version below.
        fea_result = src
        bake = bake_with_posters(
            src, out_dir, src=key, modes=modes, poster_backend=poster_backend,
        )
    else:
        raise TypeError(
            f"assets_for_docs: unsupported src type {type(src).__name__}; "
            "expected pathlib.Path | str | FEAResult | FEAStreamReader."
        )

    solver, solver_version, frequencies = _extract_solver_and_freqs(
        bake, fea_result,
    )

    return FeaDocAssets(
        key=key,
        bundle_dir=bake.out_dir,
        manifest_path=bake.manifest_path,
        mesh_glb_path=bake.mesh_glb_path,
        poster_paths=dict(bake.poster_paths),
        canonical_poster_path=bake.canonical_poster_path,
        solver=solver,
        solver_version=solver_version,
        frequencies=frequencies,
    )


# ---------------------------------------------------------------------------
# paradoc ThreeDData adapter
# ---------------------------------------------------------------------------


def to_paradoc_rows(
    assets: FeaDocAssets,
    *,
    base_dir: "pathlib.Path | str",
    camera_pos: str = "iso_3",
    caption: str | None = None,
    mode_caption_template: str = "Mode {mode}",
) -> list[ThreeDData]:
    """Convert a :class:`FeaDocAssets` into paradoc ``ThreeDData`` rows.

    Returns one canonical row (``source_type='fea_artefact_bundle'``)
    plus one row per baked mode-view
    (``source_type='fea_artefact_bundle_mode_view'``). All glb / image
    paths are stored relative to ``base_dir`` because paradoc's
    static-export step looks up source files relative to the doc
    bundle root, not the case dir.

    Paths point at the SAME ``fea.mesh.glb`` for every row; mode-view
    rows distinguish themselves through ``metadata.fea_mode_index``,
    which the embed's ``mountFeaArtefactViewer(modeIndex=…)`` reads.
    The canonical row's GLB is the same file but its
    ``image_path`` points at the canonical (un-deformed / mode-1)
    PNG so the static fallback shows the reference geometry.
    """
    base = pathlib.Path(base_dir)
    mesh_sha = hashlib.sha256(assets.mesh_glb_path.read_bytes()).hexdigest()
    mesh_size = assets.mesh_glb_path.stat().st_size
    rel_glb = str(assets.mesh_glb_path.relative_to(base))

    rows: list[ThreeDData] = []

    canonical_metadata: dict = {
        "fea_bundle_dir": str(assets.bundle_dir.relative_to(base)),
        "fea_manifest_path": str(assets.manifest_path.relative_to(base)),
    }
    if assets.canonical_poster_path and assets.canonical_poster_path.is_file():
        canonical_metadata["image_path"] = str(
            assets.canonical_poster_path.relative_to(base)
        )
    rows.append(
        ThreeDData(
            key=assets.key,
            glb_path=rel_glb,
            format="glb",
            camera_pos=camera_pos,
            caption=caption or f"{assets.solver or 'FEA'} — {assets.key}.",
            sha256=mesh_sha,
            size=mesh_size,
            source_type="fea_artefact_bundle",
            metadata=canonical_metadata,
        )
    )

    for mode_idx in sorted(assets.poster_paths.keys()):
        poster = assets.poster_paths[mode_idx]
        metadata: dict = {
            "fea_bundle_key": assets.key,
            "fea_mode_index": mode_idx,
        }
        if poster.is_file():
            metadata["image_path"] = str(poster.relative_to(base))
        mode_n = mode_idx + 1
        rows.append(
            ThreeDData(
                key=f"{assets.key}_mode_{mode_n}",
                glb_path=rel_glb,
                format="glb",
                camera_pos=camera_pos,
                caption=mode_caption_template.format(mode=mode_n),
                sha256=mesh_sha,
                size=mesh_size,
                source_type="fea_artefact_bundle_mode_view",
                metadata=metadata,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# paradoc Filter (lazy-bake)
# ---------------------------------------------------------------------------


class FeaCaseFilter(Filter):
    """Stock paradoc Filter for one FEA case — lazy bake on first attr access.

    Construction::

        case = FeaCaseFilter(
            "ca_solid_o1",
            "results/cantilever.frd",   # or a FEAResult / FEAStreamReader
            out_dir=Path("_assets/ca_solid_o1"),
        )
        one.filter_registry.register(case)

    Markdown surface::

        ${ ca_solid_o1.canonical }      → ThreeDView (un-deformed)
        ${ ca_solid_o1.mode_3 }         → ThreeDView (mode 3 pinned)
        ${ ca_solid_o1.solver }         → ScalarValue ("calculix")
        ${ ca_solid_o1.solver_version } → ScalarValue ("2.22")
        ${ ca_solid_o1.n_modes }        → int

    ``mode_N`` is resolved dynamically via ``__getattr__`` — there's
    no static ``mode_3`` method, so a filter registering 20 modes
    doesn't grow a 20-method body. Each ``mode_N`` access pulls a
    :class:`paradoc.filters.ThreeDView` whose ``glb_key`` matches the
    mode-view row :func:`to_paradoc_rows` registers for the same key.

    The bake fires the first time *any* attr is accessed (via the
    ``self.assets`` lazy property). Subsequent accesses reuse the
    cached :class:`FeaDocAssets`. Bake errors propagate out of the
    first access — they don't get silently swallowed and re-tried on
    every reference.
    """

    # __getattr__ on @attr-decorated names would shadow paradoc's
    # @attr discovery and break the resolver. Keep the dynamic shape
    # behind a clear sentinel prefix that no `@attr` method uses.
    _DYNAMIC_PREFIX = "mode_"

    def __init__(
        self,
        name: str,
        src: "pathlib.Path | str | FEAResult | FEAStreamReader",
        out_dir: "pathlib.Path | str",
        *,
        modes: "str | int | Iterable[int] | None" = "all",
        poster_backend: str = "pygfx",
        camera_preset: str = "iso_3",
    ) -> None:
        super().__init__(name=name)
        self._src = src
        self._out_dir = pathlib.Path(out_dir)
        self._modes = modes
        self._poster_backend = poster_backend
        self._camera_preset = camera_preset
        self._assets: FeaDocAssets | None = None

    @property
    def assets(self) -> FeaDocAssets:
        """Trigger the bake on first access; cache thereafter."""
        if self._assets is None:
            self._assets = assets_for_docs(
                self._src,
                key=self.name,
                out_dir=self._out_dir,
                modes=self._modes,
                poster_backend=self._poster_backend,
            )
        return self._assets

    # --- @attr surface ----------------------------------------------------

    @attr
    def canonical(self) -> ThreeDView:
        a = self.assets
        return ThreeDView(
            glb_key=a.key,
            caption=f"{self.name} — un-deformed.",
            camera_preset=self._camera_preset,
            image_path=a.canonical_poster_path,
        )

    @attr
    def solver(self) -> ScalarValue:
        return ScalarValue(value=self.assets.solver or "unknown")

    @attr
    def solver_version(self) -> ScalarValue:
        return ScalarValue(value=self.assets.solver_version or "unknown")

    @attr
    def n_modes(self) -> int:
        return self.assets.n_modes

    # --- dynamic .mode_N --------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # __getattr__ only fires when normal lookup misses, so it
        # doesn't shadow the @attr-decorated methods above.
        if not name.startswith(self._DYNAMIC_PREFIX):
            raise AttributeError(name)
        try:
            mode_n = int(name[len(self._DYNAMIC_PREFIX):])
        except ValueError as exc:
            raise AttributeError(name) from exc
        if mode_n < 1:
            raise AttributeError(
                f"{name}: mode index must be 1-based and positive."
            )
        a = self.assets
        idx = mode_n - 1
        if idx not in a.poster_paths:
            available = sorted(i + 1 for i in a.poster_paths)
            raise AttributeError(
                f"{name}: mode {mode_n} not in baked poster set "
                f"(have {available}). Pass modes=… to widen the bake."
            )
        view = ThreeDView(
            glb_key=f"{a.key}_mode_{mode_n}",
            caption=f"{self.name} — mode {mode_n}.",
            camera_preset=self._camera_preset,
            image_path=a.poster_paths[idx],
        )
        # Wrap in the same .attr marker so the resolver treats this
        # like the static @attr methods. Returning a bare ThreeDView
        # would still work for `${ x.mode_3 }` substitution today,
        # but `Filter.list_attrs()` and future cache keys discover by
        # the marker.
        return _make_attr_returning(view)


def _make_attr_returning(value: Any):
    """Wrap ``value`` in a zero-arg callable that the @attr marker
    recognises. paradoc's ``_is_attr`` checks ``callable(obj) and
    getattr(obj, _ATTR_MARKER, False)`` — by returning a tagged
    callable from ``__getattr__`` we keep the dynamic ``mode_N``
    indistinguishable from a real ``@attr`` method to the resolver.
    """

    def _attr_view():
        return value

    setattr(_attr_view, "__paradoc_attr__", True)
    return _attr_view


# ---------------------------------------------------------------------------
# Paradoc entry-point hook (block-sugar registration target)
# ---------------------------------------------------------------------------


def register_paradoc_block_sugar(dispatcher: Any) -> None:
    """Entry-point target for the ``paradoc.figure_sources`` group.

    Step 6 of the migration will wire ``figure_source: fea_artefact_bundle``
    block sugar through this function — paradoc's startup will iterate
    its ``paradoc.figure_sources`` entry points and call each with the
    dispatcher object, letting plugins register handlers without
    paradoc itself naming the implementing packages.

    For now this is a stub: declaring the entry point in
    ``pyproject.toml`` today means paradoc's discovery hook (also
    landing in step 6) finds something callable, and the rest of the
    migration can ship incrementally.
    """
    # Step 6: dispatcher.register(
    #     "fea_artefact_bundle", _handle_fea_artefact_bundle,
    # )
    return None
