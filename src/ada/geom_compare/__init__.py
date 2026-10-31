"""Compare two geometry sources' tessellations of the same elements.

Generic: a source is a set of GLBs in the merged-mesh layout adapy writes (``glb``), elements are
joined by name, and nothing here knows who produced either side.

* ``glb`` -- read (and write) per-element geometry of a merged-mesh GLB, stdlib only.
* ``summary`` -- per-element box, volume, area, triangles and fingerprint, vectorised (numpy).
* ``measure`` -- one side's measures (a triangle soup, or an adapy object's exact solids) and the
  box-and-volume ``judge``.
* ``distance`` -- the sampled symmetric surface distance of two meshes.
* ``classify`` -- pass / uncertain / fail / missing, with explicit ``Thresholds``.
* ``compare`` -- the whole comparison of two sources, incremental by fingerprint.
* ``report`` -- the two-file report (``compare_files``) and its one-line ``summarise``.
* ``render`` -- side-by-side pictures of a pair, through adapy's offscreen renderer.
"""

from .classify import (
    FAIL,
    MISSING,
    PASS,
    UNCERTAIN,
    VERDICTS,
    PairResult,
    Thresholds,
    classify,
)
from .compare import CompareResult, GlbSource, compare_sources
from .measure import Measure, Verdict, judge

__all__ = [
    "FAIL",
    "MISSING",
    "PASS",
    "UNCERTAIN",
    "VERDICTS",
    "CompareResult",
    "GlbSource",
    "Measure",
    "PairResult",
    "Thresholds",
    "Verdict",
    "classify",
    "compare_sources",
    "judge",
]
