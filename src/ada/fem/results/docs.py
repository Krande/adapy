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
import logging
import pathlib
import shutil
from dataclasses import dataclass
from dataclasses import field as dc_field
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

logger = logging.getLogger(__name__)


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
        fmt = getattr(fea_result, "software", None) or getattr(fea_result, "fem_format", None)
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
            frequencies = (frequencies or []) + [float(s.get("value", 0.0)) for s in steps]

    return solver, solver_version, frequencies


def assets_from_bundle_dir(
    bundle_dir: "pathlib.Path | str",
    *,
    key: str | None = None,
) -> FeaDocAssets:
    """Build a :class:`FeaDocAssets` from an already-baked bundle dir.

    Cache-only / CI builds run the verification report against
    bundles that were baked in a previous pass and committed to the
    repo. This helper walks the bundle dir, picks up the poster PNGs
    matching the filename convention :func:`bake_with_posters` writes
    (``fea.mesh.png`` for mode 1, ``fea.mesh.mode_<N>.png`` for N ≥ 2),
    and parses the manifest for frequencies + step count. No re-bake,
    no FEAResult required.

    ``key`` defaults to the bundle dir's basename, matching the keying
    convention :func:`bake_with_posters_from_source` uses with
    ``src_key=stem``.
    """
    bundle = pathlib.Path(bundle_dir)
    if key is None:
        key = bundle.name
    manifest_path = bundle / "fea.manifest.json"
    mesh_glb_path = bundle / "fea.mesh.glb"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"assets_from_bundle_dir: {manifest_path} not present — "
            "the bundle hasn't been baked yet. Use assets_for_docs() "
            "to bake-and-register, or point at the right directory."
        )
    if not mesh_glb_path.is_file():
        raise FileNotFoundError(f"missing {mesh_glb_path}")

    # Walk the disk for the per-mode posters the bake convention
    # writes. Mode 1 sits at the `<glb>.png` sibling; modes ≥ 2 at
    # `fea.mesh.mode_<N>.png`. Discovery is filename-driven (not
    # manifest-driven) so a bundle that baked fewer posters than the
    # manifest's displacement-step count registers exactly those that
    # exist on disk — un-baked modes get no row.
    poster_paths: dict[int, pathlib.Path] = {}
    canonical = mesh_glb_path.with_suffix(".png")
    if canonical.is_file():
        poster_paths[0] = canonical
    for png in bundle.glob("fea.mesh.mode_*.png"):
        match = png.stem  # e.g. "fea.mesh.mode_7"
        suffix = match.split("_")[-1]
        try:
            mode_n = int(suffix)
        except ValueError:
            continue
        if mode_n >= 2:
            poster_paths[mode_n - 1] = png

    canonical_path = poster_paths.get(0)

    # Synthesise a minimal BakeWithPostersResult-shaped object for the
    # solver/frequency extractor. We don't have a FEAResult, so solver
    # name / version stay None — the manifest doesn't carry them
    # today. Frequencies come from the manifest's step values.
    class _BakeStub:
        manifest_path = bundle / "fea.manifest.json"

    _stub = _BakeStub()
    solver, solver_version, frequencies = _extract_solver_and_freqs(_stub, None)

    return FeaDocAssets(
        key=key,
        bundle_dir=bundle,
        manifest_path=manifest_path,
        mesh_glb_path=mesh_glb_path,
        poster_paths=poster_paths,
        canonical_poster_path=canonical_path,
        solver=solver,
        solver_version=solver_version,
        frequencies=frequencies,
    )


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
            src,
            out_dir,
            src=key,
            modes=modes,
            poster_backend=poster_backend,
        )
    elif hasattr(src, "results"):
        # FEAResult — keep the reference so we can pull fem_format +
        # software_version below.
        fea_result = src
        bake = bake_with_posters(
            src,
            out_dir,
            src=key,
            modes=modes,
            poster_backend=poster_backend,
        )
    else:
        raise TypeError(
            f"assets_for_docs: unsupported src type {type(src).__name__}; "
            "expected pathlib.Path | str | FEAResult | FEAStreamReader."
        )

    solver, solver_version, frequencies = _extract_solver_and_freqs(
        bake,
        fea_result,
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
    rel_glb = assets.mesh_glb_path.relative_to(base).as_posix()

    rows: list[ThreeDData] = []

    canonical_metadata: dict = {
        "fea_bundle_dir": assets.bundle_dir.relative_to(base).as_posix(),
        "fea_manifest_path": assets.manifest_path.relative_to(base).as_posix(),
    }
    if assets.canonical_poster_path and assets.canonical_poster_path.is_file():
        canonical_metadata["image_path"] = assets.canonical_poster_path.relative_to(base).as_posix()
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
            metadata["image_path"] = poster.relative_to(base).as_posix()
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
# Batch bake / collect — multi-case helpers a report's @task body calls
# ---------------------------------------------------------------------------


def bake_fea_bundles(
    cases: Iterable[Any],
    *,
    out_dir: "pathlib.Path | str",
    modes: "str | int | Iterable[int]" = "all",
) -> dict[str, FeaDocAssets]:
    """Bake one FEA artefact bundle per case under ``out_dir/<case.name>/``.

    The per-report orchestration shape every paradoc-driven FEA report
    needs: walk a collection of solved cases, wipe each case's bundle
    directory, re-bake fresh, and hand back a name → :class:`FeaDocAssets`
    mapping the downstream task can hang ``ThreeDOutcome`` /
    ``FilterOutcome`` off of.

    Each entry in ``cases`` must expose:

    * ``case.name: str`` — case identifier; becomes the bundle dir name
      and :attr:`FeaDocAssets.key`. Must be a valid Python identifier
      so paradoc's Filter registry accepts it.
    * ``case.results`` — a live :class:`FEAResult` / :class:`FEAResultV2`,
      or ``None``. Entries without a live result are skipped (the
      cache-only / CI replay path: the committed bundle on disk is what
      the report renders against in that case).

    Duck-typed rather than requiring a specific wrapper class so each
    report can keep its own per-case dataclass (e.g. verification's
    ``FeaVerificationResult`` for eigen analyses, future param_models
    counterparts for static / nonlinear analyses) without inheriting
    from a shared adapy base. The two attribute names are the contract.

    Wipes each case dir before re-baking so a stale per-mode PNG from
    an older run can't outlive the new bundle. Per-case bake failures
    are logged and dropped: a single broken case never kills the loop;
    the report degrades to the "figures unavailable" placeholder for
    that case via :func:`collect_fea_bundles` returning nothing for it.
    """
    out_dir = pathlib.Path(out_dir)
    out: dict[str, FeaDocAssets] = {}
    for case in cases:
        fea_result = getattr(case, "results", None)
        if fea_result is None:
            logger.info(f"{case.name}: no FEAResult attached (cache-only) — " "skipping FEA artefact bake")
            continue
        case_dir = out_dir / case.name
        if case_dir.exists():
            shutil.rmtree(case_dir)
        try:
            assets = assets_for_docs(
                fea_result,
                key=case.name,
                out_dir=case_dir,
                modes=modes,
            )
            out[case.name] = assets
            logger.info(f"{case.name}: baked FEA artefacts → {case_dir.name}/ " f"(n_modes={assets.n_modes})")
        except Exception as exc:
            logger.warning(f"{case.name}: FEA artefact bake failed: {exc}", exc_info=True)
    return out


def collect_fea_bundles(
    assets_dir: "pathlib.Path | str",
    *,
    skip_keys: Iterable[str] = (),
) -> list[FeaDocAssets]:
    """Walk ``assets_dir/*/fea.manifest.json`` and load each as
    :class:`FeaDocAssets`.

    Cache-only / CI counterpart to :func:`bake_fea_bundles`: when the
    bake step didn't fire (no ``ADAPY_*_REGEN_ASSETS`` env flag, no
    solver installed, etc.), these are the committed bundles the report
    renders against.

    ``skip_keys`` excludes case names already baked fresh, so live bakes
    take precedence over stale committed bundles for the same case:

    .. code-block:: python

        fresh = bake_fea_bundles(results, out_dir=ASSETS_DIR)
        cached = collect_fea_bundles(ASSETS_DIR, skip_keys=set(fresh))

    Load failures (corrupt manifest, missing mesh GLB) are logged and
    dropped — same robustness contract as :func:`bake_fea_bundles`.
    """
    skip = set(skip_keys)
    out: list[FeaDocAssets] = []
    for manifest_path in sorted(pathlib.Path(assets_dir).rglob("fea.manifest.json")):
        case_dir = manifest_path.parent
        case = case_dir.name
        if case in skip:
            continue
        try:
            out.append(assets_from_bundle_dir(case_dir, key=case))
        except Exception as exc:
            logger.warning(f"could not load bundle at {case_dir}: {exc}")
    return out


# ---------------------------------------------------------------------------
# paradoc Filter (lazy-bake)
# ---------------------------------------------------------------------------


#: Maximum mode count the class pre-generates ``mode_1`` … ``mode_<N>``
#: methods for. A class-level cap is required because paradoc's filter
#: cache walks ``inspect.getsource(getattr(filter_cls, attr_name))`` —
#: i.e. class-level lookup that bypasses ``__getattr__`` — to hash the
#: implementation. Dynamic instance-level resolution would raise
#: ``AttributeError`` out of that pass and crash the build. 30 covers
#: every eigen analysis the verification report runs today (10 modes
#: per case × a 3× margin); bump if a downstream report exceeds it.
_MAX_MODE_ATTRS = 30


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

    ``mode_1`` … ``mode_<MAX>`` are pre-attached as ``@attr`` class
    methods (see the ``_attach_mode_attrs`` block below). The actual
    poster rendering is governed by the ``modes=`` constructor kwarg
    — a ``${ case.mode_5 }`` reference where mode 5 wasn't baked
    still resolves to a :class:`ThreeDView` with the correct
    ``glb_key``; paradoc's static export then shows a missing-asset
    placeholder for the un-baked mode. This split keeps the class
    surface stable while letting per-case bakes choose how many
    posters to materialise.

    The bake fires the first time *any* attr is accessed (via the
    ``self.assets`` lazy property). Subsequent accesses reuse the
    cached :class:`FeaDocAssets`. Bake errors propagate out of the
    first access — they don't get silently swallowed and re-tried on
    every reference.
    """

    def __init__(
        self,
        name: str,
        src: "pathlib.Path | str | FEAResult | FEAStreamReader | None" = None,
        out_dir: "pathlib.Path | str | None" = None,
        *,
        modes: "str | int | Iterable[int] | None" = "all",
        poster_backend: str = "pygfx",
        camera_preset: str = "iso_3",
        assets: "FeaDocAssets | None" = None,
    ) -> None:
        """Construct a per-case filter.

        Two construction styles:

        1. **Lazy-bake** — pass ``src`` + ``out_dir``. The bake fires on
           first attr access via :func:`assets_for_docs`. Useful when the
           filter is the one driving the bake (e.g. ad-hoc scripts).

        2. **Pre-baked** — pass ``assets`` (a :class:`FeaDocAssets` from
           :func:`assets_for_docs` / :func:`assets_from_bundle_dir`).
           Common path in pipelines that need to bake eagerly to
           register paradoc ``ThreeDData`` rows before the filter
           resolver runs; the filter then just surfaces the already-
           computed paths without re-baking.

        Use :meth:`from_assets` / :meth:`from_bundle_dir` for the
        pre-baked styles — they're a touch more discoverable and
        document intent at the call site.
        """
        super().__init__(name=name)
        if assets is None and (src is None or out_dir is None):
            raise ValueError(
                "FeaCaseFilter: must supply either `assets=…` (pre-baked) " "or both `src` + `out_dir` (lazy-bake)."
            )
        self._src = src
        self._out_dir = pathlib.Path(out_dir) if out_dir is not None else None
        self._modes = modes
        self._poster_backend = poster_backend
        self._camera_preset = camera_preset
        self._assets: FeaDocAssets | None = assets

    @classmethod
    def from_assets(
        cls,
        assets: FeaDocAssets,
        *,
        camera_preset: str = "iso_3",
    ) -> "FeaCaseFilter":
        """Build a filter from an already-baked :class:`FeaDocAssets`.

        ``assets.key`` becomes the filter ``name`` (paradoc requires a
        valid Python identifier — make sure ``key`` already satisfies
        that on the call site).
        """
        return cls(
            name=assets.key,
            assets=assets,
            camera_preset=camera_preset,
        )

    @classmethod
    def from_bundle_dir(
        cls,
        bundle_dir: "pathlib.Path | str",
        *,
        key: str | None = None,
        camera_preset: str = "iso_3",
    ) -> "FeaCaseFilter":
        """Build a filter from a committed bundle directory.

        Cache-only build path — :func:`assets_from_bundle_dir` walks
        the dir for the manifest + posters; no re-bake.
        """
        a = assets_from_bundle_dir(bundle_dir, key=key)
        return cls.from_assets(a, camera_preset=camera_preset)

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

    def _mode_view(self, mode_n: int) -> ThreeDView:
        """Build the :class:`ThreeDView` for a 1-based mode index.

        Called by every generated ``mode_<N>`` class attr below. The
        view's ``glb_key`` matches :func:`to_paradoc_rows`' mode-view
        row key, so the resolver finds the right ``ThreeDData`` entry
        in the paradoc asset store regardless of whether the poster
        for this specific mode was actually baked — un-baked modes
        render with a placeholder image, but the glb_key still
        addresses the bundle file the interactive viewer mounts.
        """
        a = self.assets
        idx = mode_n - 1
        return ThreeDView(
            glb_key=f"{a.key}_mode_{mode_n}",
            caption=f"{self.name} — mode {mode_n}.",
            camera_preset=self._camera_preset,
            image_path=a.poster_paths.get(idx),
        )


def _make_mode_attr(mode_n: int):
    """Build a single ``mode_<N>`` ``@attr`` method.

    Cosmetic ``__name__`` / ``__qualname__`` lines keep ``inspect``
    output and AST cache keys stable as ``FeaCaseFilter.mode_<N>``
    instead of every generated method showing up as ``_per_mode``.
    """

    @attr
    def _per_mode(self) -> ThreeDView:
        return self._mode_view(mode_n)

    _per_mode.__name__ = f"mode_{mode_n}"
    _per_mode.__qualname__ = f"FeaCaseFilter.mode_{mode_n}"
    return _per_mode


for _mode_n in range(1, _MAX_MODE_ATTRS + 1):
    setattr(FeaCaseFilter, f"mode_{_mode_n}", _make_mode_attr(_mode_n))


# ---------------------------------------------------------------------------
# Paradoc entry-point hook (block-sugar registration target)
# ---------------------------------------------------------------------------


def register_paradoc_block_sugar(dispatcher: Any) -> None:
    """Entry-point target for the ``paradoc.figure_sources`` group.

    Registers ``figure_source: fea_artefact_bundle`` so a user can drop
    a raw solver result (``.frd`` / ``.odb`` / ``.rmed`` / ``.sif`` /
    ``.sin``) into a markdown block and get back one interactive figure
    per mode + matching static PNGs::

        <!-- paradoc:figure
        figure_source: fea_artefact_bundle
        figure_title: Cantilever frequency analysis
        source_inp: files/cantilever.frd
        camera_pos: iso_3
        layout: per_mode      # or "gallery" (TODO)
        n_modes: 10           # optional cap; default = every mode
        poster_backend: pygfx # or "chromium"
        -->

    Each mode produces one ``ThreeDData`` row + one PNG figure tag,
    using the same bake / poster pipeline :class:`FeaCaseFilter` runs
    under the hood. Static counterparts are mandatory in this codebase
    (PDF / DOCX / ODT exports can't run the interactive slider), so the
    per-mode static figures are the default and ``layout`` only
    controls the *visual* grouping.

    The spec + filter classes are defined inside this function to keep
    the module-load cost low when adapy is imported without paradoc
    present — only paradoc's entry-point dispatcher calls in here, and
    paradoc is in scope by the time it does.
    """
    from pathlib import Path
    from typing import Literal, Optional

    from paradoc.figure_sources.filters.base import FigureSourceFilter, RenderResult
    from paradoc.figure_sources.models import BaseFigureSource
    from pydantic import Field

    class FeaArtefactBundle(BaseFigureSource):
        """Block-sugar spec for ``figure_source: fea_artefact_bundle``.

        ``source_inp`` points at the raw solver result file paradoc
        bakes into the FEA artefact bundle at build time. ``layout``
        controls how the per-mode figures are grouped in the rendered
        document; ``n_modes`` caps how many modes get rendered (default
        = all displacement steps the bundle carries).
        """

        figure_source: Literal["fea_artefact_bundle"] = "fea_artefact_bundle"
        source_inp: Path = Field(
            ...,
            description=(
                "Path to a raw FEA result file (.frd / .odb / .rmed / "
                ".sif / .sin). Relative paths resolve against the doc "
                "bundle root (paradoc convention)."
            ),
        )
        layout: Literal["per_mode", "gallery"] = Field(
            "per_mode",
            description=(
                "How to group the per-mode figures. `per_mode` (default) "
                "emits one figure block per mode — best for engineering "
                "docs that walk through modes individually. `gallery` "
                "is reserved for a future grid layout; today it falls "
                "back to `per_mode` with a warning."
            ),
        )
        n_modes: Optional[int] = Field(
            default=None,
            description=(
                "Optional cap on the number of modes to render. None "
                "(default) means every displacement step the bundle "
                "carries. The bake stops at the cap to keep large "
                "transient analyses from blowing out the doc size."
            ),
        )
        poster_backend: Literal["pygfx", "chromium"] = Field(
            "pygfx",
            description=(
                "Offscreen backend for the per-mode PNG posters. Same "
                "split as the cad_model_file source: pygfx is fast and "
                "pure-Python; chromium drives the production embed for "
                "bit-identical output, at ~5 s/PNG."
            ),
        )

    dispatcher.register_spec("fea_artefact_bundle", FeaArtefactBundle)

    class FeaArtefactBundleFilter(FigureSourceFilter):
        """Bake a raw FEA result into an artefact bundle + emit one
        figure per mode.

        Uses :func:`assets_for_docs` under the hood — same code path
        :class:`FeaCaseFilter` runs when invoked from Python. The
        difference is purely the entry point: this filter handles the
        markdown-block flow, where the user never touches Python.
        """

        figure_source = "fea_artefact_bundle"

        def render(self, spec, *, key):  # type: ignore[override]
            if not isinstance(spec, FeaArtefactBundle):
                raise TypeError(f"FeaArtefactBundleFilter received non-FEA spec: " f"{type(spec).__name__}")

            source_path = Path(spec.source_inp)
            if not source_path.is_absolute():
                source_path = (self.bundle_root / source_path).resolve()
            if not source_path.exists():
                raise FileNotFoundError(f"FEA source file not found: {source_path}")

            # Bake into a per-key directory under the bundle's 3D assets
            # tree. Layout mirrors the cad_model_file source so paradoc's
            # static export's relative-path heuristics keep working.
            bundle_dir = self.bundle_root / "assets" / "3d" / key
            bundle_dir.mkdir(parents=True, exist_ok=True)

            modes = "all" if spec.n_modes is None else spec.n_modes
            assets = assets_for_docs(
                source_path,
                key=key,
                out_dir=bundle_dir,
                modes=modes,
                poster_backend=spec.poster_backend,
            )

            if spec.layout == "gallery":
                # Gallery layout (single block with a CSS grid of PNGs)
                # needs paradoc-side markup support that's not in tree
                # yet. Fall back to per_mode so the user gets the right
                # static counterparts even if the visual grouping isn't
                # ideal. Log a warning so the caller can track.
                import logging

                logging.getLogger(__name__).warning(
                    "fea_artefact_bundle: layout='gallery' not yet "
                    "supported; falling back to layout='per_mode' for "
                    "key %r.",
                    key,
                )

            # Resolve paths bundle-relative (paradoc convention). The
            # canonical mesh GLB is shared across all per-mode rows —
            # the embed picks the right mode via ``fea_mode_index`` in
            # metadata.
            mesh_glb_rel = assets.mesh_glb_path.relative_to(self.bundle_root).as_posix()
            mesh_sha = hashlib.sha256(assets.mesh_glb_path.read_bytes()).hexdigest()
            mesh_size = assets.mesh_glb_path.stat().st_size

            results: list[RenderResult] = []
            for mode_idx in sorted(assets.poster_paths.keys()):
                poster = assets.poster_paths[mode_idx]
                if not poster.is_file():
                    continue
                png_rel = poster.relative_to(self.bundle_root).as_posix()
                mode_n = mode_idx + 1
                results.append(
                    RenderResult(
                        png_path=png_rel,
                        glb_path=mesh_glb_rel,
                        glb_sha256=mesh_sha,
                        glb_size=mesh_size,
                        caption=f"{spec.figure_title} — mode {mode_n}",
                        camera_pos=spec.camera_pos,
                        source_type=self.figure_source,
                        metadata={
                            "source_inp": str(source_path),
                            "image_path": png_rel,
                            "fea_bundle_key": key,
                            "fea_mode_index": mode_idx,
                            "fea_manifest_path": assets.manifest_path.relative_to(self.bundle_root).as_posix(),
                        },
                    )
                )

            if not results:
                raise RuntimeError(
                    f"fea_artefact_bundle: bake produced no posters for "
                    f"{source_path} — does the result file carry "
                    "displacement fields?"
                )
            return results

    dispatcher.register_filter(FeaArtefactBundleFilter)
