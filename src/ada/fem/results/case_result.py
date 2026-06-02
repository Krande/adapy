"""Per-case result wrapper for paradoc-driven FEA reports.

The verification report has historically carried a ``FeaVerificationResult``
dataclass in its own ``utils.py`` to bundle a live :class:`FEAResult`
together with run identity (name, solver), free-form metadata, and a
small JSON serialisation surface for cache replay. Every paradoc-driven
FEA report needs a wrapper of that shape, so the common skeleton lives
here and individual reports subclass to add analysis-specific summaries
(``eig_data`` for eigen analyses, future ``stress_data`` /
``history_data`` for static / transient analyses, etc.).

What lives in this module:

* :class:`FeaCaseResult` — the common skeleton. Holds ``name``,
  ``fem_format``, the live ``results``, ``metadata``, and
  ``last_modified``; exposes :attr:`safe_name` for paradoc Filter / bundle
  keys and ``save_to_json`` / ``from_json`` for cache replay. Subclasses
  override ``_extra_payload`` / ``_hydrate_extras`` to add their own
  fields to the JSON shape.

* :func:`walk_cached_case_results` — walks a cache directory of JSON
  snapshots, decoding each via the report's ``case_cls.from_json``.
  Robust to non-case-result cache files (solver-version snapshots, debug
  dumps): undecodable entries are logged and skipped.

The :func:`bake_fea_bundles` / :func:`collect_fea_bundles` helpers in
:mod:`ada.fem.results.docs` consume any case wrapper that exposes
``name`` and ``results`` — i.e. anything subclassing this — without
caring about the analysis-specific extras.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar, Iterable, Optional, Union

from ada.fem.results.common import FEAResult

if TYPE_CHECKING:  # pragma: no cover
    # Avoid the runtime import — ``ada.fem.formats.abaqus.post_processing``
    # imports ``EigenDataSummary`` from ``ada.fem.results``, which would
    # circle back through this module's parent package initializer.
    from ada.fem.formats.abaqus.post_processing import FEAResultV2


logger = logging.getLogger(__name__)


_FILTER_NAME_RE = re.compile(r"[^0-9A-Za-z_]")


def _safe_identifier(name: str) -> str:
    """Map ``name`` to a paradoc-Filter-safe identifier.

    Strips a file-extension suffix (legacy adapy result names leaked
    ``.rmed`` / ``.frd``), replaces non-identifier characters with
    ``_``, prefixes ``_`` when the first character is a digit, and
    falls back to ``"case"`` for an empty result. Idempotent — calling
    twice yields the same string.

    Exposed at module level (rather than only as a method) so the JSON
    cache loader can normalise names before instantiating a case wrapper.
    """
    stem = pathlib.Path(name).stem if "." in name else name
    cleaned = _FILTER_NAME_RE.sub("_", stem)
    if not cleaned:
        return "case"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


@dataclass
class FeaCaseResult:
    """One FEA case's identity + live result + cacheable metadata.

    Common skeleton for per-report case wrappers. Subclass to add
    analysis-specific summaries:

    .. code-block:: python

        @dataclass
        class FeaVerificationResult(FeaCaseResult):
            eig_data: EigenDataSummary | None = None

            def _extra_payload(self) -> dict:
                return {"eigen_mode_data": self.eig_data.to_dict()}

            def _hydrate_extras(self, payload: dict) -> None:
                eig = EigenDataSummary([])
                eig.from_dict(payload["eigen_mode_data"])
                self.eig_data = eig

    Attributes
    ----------
    name :
        Case identifier. Conventionally matches the case directory name
        under the report's ``_assets/`` tree. Treat as the bundle key —
        :attr:`safe_name` returns the normalised form paradoc requires.
    fem_format :
        Lowercase solver name (``"calculix"``, ``"code_aster"``,
        ``"abaqus"``, ``"sesam"``). Reports use this to bucket by solver
        for per-solver report sections.
    results :
        The live :class:`FEAResult` / :class:`FEAResultV2`. ``None`` on
        the cache-replay path (a JSON snapshot rehydrated this wrapper
        but the source solver file is absent — typical CI shape).
    metadata :
        Free-form per-case dict. Verification stuffs the mesh axes
        (``geo`` / ``elo`` / ``hexquad`` / ``reduced_integration``) here
        so the comparison-table builder can group by them; param_models
        and future reports use it for their own per-case dimensions.
    last_modified :
        Timestamp the wrapper was created or last cached. Used to
        invalidate stale JSON snapshots if a report grows a freshness
        policy; today it's informational only.
    """

    name: str
    fem_format: str
    results: Optional[Union[FEAResult, "FEAResultV2"]] = None
    metadata: dict = field(default_factory=dict)
    last_modified: datetime = field(default_factory=datetime.now)

    # ----- identifier hygiene ------------------------------------------------

    @cached_property
    def safe_name(self) -> str:
        """Paradoc-Filter-safe form of :attr:`name`.

        Extension stripped, non-identifier characters mapped to ``_``,
        leading digit prefixed. Idempotent — the postprocess task
        typically does ``case.name = case.safe_name`` once after
        construction so every downstream consumer sees the same
        identifier; subsequent reads of either attribute return the
        same value.
        """
        return _safe_identifier(self.name)

    # ----- JSON cache I/O ----------------------------------------------------

    #: JSON files in a cache dir whose stems match these names get
    #: skipped by :func:`walk_cached_case_results`. Reports add their
    #: own well-known non-result filenames to this set via the
    #: ``skip_stems=`` argument; the class default covers the names
    #: adapy reports have historically reserved.
    CACHE_SKIP_STEMS: ClassVar[frozenset[str]] = frozenset({"software_versions", "debug"})

    def save_to_json(self, cache_filepath: "pathlib.Path | str") -> None:
        """Persist a JSON snapshot of this case for offline replay.

        Writes the common fields plus whatever :meth:`_extra_payload`
        returns. Filename gets a ``.json`` suffix if absent.
        """
        path = pathlib.Path(cache_filepath).with_suffix(".json")
        # Key ordering matches the legacy verification-report convention:
        # common fields first, subclass extras second, ``last_modified`` last.
        # Stable order keeps the committed ``_cache/*.json`` diffs limited
        # to actual data changes when a new bake re-writes the file.
        payload = {
            "name": self.name,
            "fem_format": self.fem_format,
            "metadata": self.metadata,
            **self._extra_payload(),
            "last_modified": self.last_modified.timestamp(),
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=4)

    def _extra_payload(self) -> dict:
        """Hook for subclasses to add analysis-specific fields to the
        JSON payload. Return value is merged into the common dict by
        :meth:`save_to_json`. Default: empty (no extras)."""
        return {}

    @classmethod
    def from_json(cls, cache_filepath: "pathlib.Path | str") -> "FeaCaseResult":
        """Reconstruct an instance from its on-disk JSON snapshot.

        Normalises ``name`` via :func:`_safe_identifier` so cache
        entries written under legacy ``.rmed``-suffixed names match
        post-fix live-run keys (otherwise the cache and the live run
        end up as separate case rows). Delegates analysis-specific
        field hydration to :meth:`_hydrate_extras`; the live
        :attr:`results` slot stays ``None`` because the snapshot doesn't
        carry the source solver file.
        """
        path = pathlib.Path(cache_filepath)
        with open(path) as f:
            payload = json.load(f)

        instance = cls(
            name=_safe_identifier(payload["name"]),
            fem_format=payload["fem_format"],
            results=None,
            metadata=payload.get("metadata", {}),
            last_modified=datetime.fromtimestamp(payload["last_modified"]),
        )
        instance._hydrate_extras(payload)
        return instance

    def _hydrate_extras(self, payload: dict) -> None:
        """Hook for subclasses to decode analysis-specific fields from a
        cached JSON payload. Mirrors :meth:`_extra_payload`. Default:
        no-op."""
        return None


def walk_cached_case_results(
    case_cls: type[FeaCaseResult],
    cache_dir: "pathlib.Path | str",
    *,
    skip_names: Iterable[str] = (),
    skip_stems: Iterable[str] = (),
) -> list[FeaCaseResult]:
    """Walk ``cache_dir/*.json`` and decode each via
    ``case_cls.from_json``.

    Cache-only / CI counterpart to a live solver run. Returns one
    instance per JSON snapshot that decodes cleanly; callers typically
    merge the list with their live-result list (the
    :func:`bake_fea_bundles` / :func:`collect_fea_bundles` pair has the
    on-disk-bundle analogue for poster artefacts).

    Filename guards
    ---------------
    * ``skip_stems`` — filenames (without ``.json``) to skip up front.
      Defaults to the class's :attr:`CACHE_SKIP_STEMS` so
      ``software_versions.json`` and similar reserved cache files don't
      get fed to ``from_json``. Pass extra stems to widen the filter
      without overriding the class defaults.
    * Files that survive the stem filter but fail to decode (malformed
      JSON, missing required fields) are logged and dropped — the walk
      never raises on a single bad file.

    Result guards
    -------------
    * ``skip_names`` — case names to exclude after decoding. Use this
      to dedupe against a live-result list: pass ``{r.name for r in
      live_results}`` so cached entries that overlap with fresh runs
      stay out of the merged set.
    """
    skip = set(skip_names)
    skip_stem = set(skip_stems) | set(getattr(case_cls, "CACHE_SKIP_STEMS", ()))
    out: list[FeaCaseResult] = []
    for path in sorted(pathlib.Path(cache_dir).rglob("*.json")):
        if path.stem in skip_stem:
            continue
        try:
            entry = case_cls.from_json(path)
        except (KeyError, json.JSONDecodeError, TypeError) as exc:
            logger.info(f"skipping {path.name}: not a {case_cls.__name__} cache ({exc})")
            continue
        if entry.name in skip:
            continue
        out.append(entry)
    return out


__all__ = [
    "FeaCaseResult",
    "walk_cached_case_results",
]
