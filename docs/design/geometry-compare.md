# Geometry comparison: two providers' tessellations of the same elements

Status: implemented (`ada.geom_compare`, `routes/geometry_compare.py`, `formats/geometry_compare.py`,
migration 051).

## Why

Two providers often publish the same elements: one extracts exact geometry from the source system and
one ships that system's own display tessellation. Neither is ground truth, but where they disagree one
of them is wrong, and finding those elements by hand across many projects of 100 000+ elements is not
possible. A provider can now declare a scheduled comparison against another provider, and admins get a
gallery of what did not pass, side by side.

## Shape

* **Kind.** `geometry-compare`, a new `asset_schedules` kind driven by the sweep driver like `sweep` and
  `apply-changes` (lanes, heartbeat, cancel, skip, resume; "All projects" or one). The entry is declared
  by the provider whose geometry is side A; its `compare_with` setting names side B. Core runs no
  provider job for it.
* **Where the meshes come from.** Core's build path, for both sides. Every subject of the collection whose
  newest complete manifest claims `delivery: build` is built through `submit_node_build` (a cached build
  is free; the GLB is exactly the one a viewer loads). A provider can be compared if core can build its
  geometry -- nothing provider-specific is needed in core.
* **Where it runs.** A synthetic worker job, `geometry_compare` (feature `geometry_compare`), on a core
  worker (adapy + numpy; pictures need the offscreen renderer and are skipped without it).
* **Generic library.** `ada.geom_compare`: `glb` (read merged-mesh GLBs per element, stdlib only),
  `summary` (vectorised per-element box / volume / area / triangles / fingerprint), `measure` (one side's
  measures and the box-and-volume `judge`), `distance` (sampled symmetric surface distance), `classify`
  (verdicts and thresholds), `compare` (the whole comparison, incremental), `report` (the two-file
  report), `render` (side-by-side pictures). The vocabulary gate keeps source names out of it.

## Matching

Elements are joined by NAME, normalised (blanks, a leading `/` and case). A core build stamps `node_ids`
(GLB id -> published node) into its GLB; each such entry is named by that node's `full_name` in the
subject's spine, else its label. An entry without a node id keeps the GLB's own `id_hierarchy` name.
An element drawn by several GLBs of one side is folded (boxes widened, volumes and triangles added).
Nothing fuzzy: a miss is reported (`missing`) rather than matched to a different element. A comparison
where nothing joins warns "broken join" -- that is two sources naming elements differently, not
agreement.

## Metrics and verdicts

Per pair, in mm (GLB metres, Y-up, read as Z-up):

| Metric | Meaning |
| --- | --- |
| `box_delta_mm` | worst bounding-box face difference |
| `volume_ratio` | A / B signed mesh volume; not compared when either mesh is open |
| `area_ratio`, `triangle_ratio` | reported, never gated on |
| `hausdorff_mm`, `p95_mm`, `mean_mm` | sampled symmetric surface distance (only for undecided pairs) |
| `spacing_mm` | the sampling resolution of the distances |

The rule (`ada.geom_compare.classify`), cheapest first, thresholds from the run's settings:

1. **missing** -- on one side only (its own verdict: a source may simply not model something).
2. **pass** -- box within `box_tol_mm` (1) and volume within `vol_tol` (0.5 %) -- or volume not
   comparable (an open mesh) and the box within tolerance.
3. **fail** -- box off by more than `fail_mm` (25), or both closed and volumes off by more than
   `vol_fail` (10 %).
4. otherwise the surfaces are compared: each side sampled (vertices + area-weighted points, fixed seed),
   each sample measured to the other side's nearest TRIANGLE (exact point-to-triangle, vectorised; a
   side of more than 20 000 triangles is measured against samples). Hausdorff within `dist_tol_mm` (2)
   is a **pass** (the difference is tessellation), beyond `fail_mm` a **fail**, else **uncertain**.

Only step-4 pairs pay for triangles; a run caps them (20 000 per collection, the rest stay uncertain).

## Scale and incrementality

* Summaries are numpy, one draw range at a time; GLBs are downloaded to the job's temp directory and
  re-read only for the pairs that need triangles or pictures.
* **Fingerprints.** Each element's summary carries a hash of its triangles (positions quantised to
  0.01 mm). The collection's previous state is handed to the job; a pair whose two fingerprints did not
  move keeps its verdict, metrics and pictures without being measured or rendered again.
* Pictures only for fail / uncertain / missing, at most `max_pictures` per collection.

## Storage

* `geometry_compare_elements` (migration 051): one row per (provider A, provider B, collection, element
  key) -- verdict, reason, metrics, both fingerprints, each side's summary and node / subject, picture
  keys. Replaced per collection by every run (elements no longer present are removed). It is the
  gallery's source and the next run's memory.
* Files in the provider store under `_derived/geometry-compare/<A>/<B>/<collection>/`: `runs/<run>/`
  (`previous.json.gz`, `rows.ndjson.gz`, `summary.json`) and `pictures/<key hash>/<fp_a>_<fp_b>-<a|b>.png`
  (a picture key changes with the geometry, so a reused verdict keeps a valid picture).
* The sweep item records the counts and timings (`timings.geometry_compare`); warnings go to the run.

## Admin surface

* `GET /api/admin/geometry-compare/items?provider_a&provider_b&collection*&verdict*&q&sort&limit&offset`
  -> `{items, total, counts}` (counts per verdict over the same filter).
* `GET /api/admin/geometry-compare/picture?key=` -- PNGs under the comparison prefix only.
* Admin -> Providers -> a run of the comparison -> **Gallery**: cards with both pictures (framed by the
  same box), verdict, reason, metrics, collection and each side's node; filter by verdict, collection,
  name; sort by a metric.

## Not yet

* No deep link from a gallery card into the Sources tab on that node (the app has no node URL yet); the
  card shows each side's node id and copies the element name.
* A provider whose geometry core cannot build (meshes only in its own store) cannot be side B yet; it
  would need to publish build-delivered subjects first.
