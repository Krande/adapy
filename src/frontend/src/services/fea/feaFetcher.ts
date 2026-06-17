// Storage-layer abstraction for the FEA artefact bundle loader.
//
// The bake (`ada.fem.results.artefacts.bake_artefacts`) emits a set of
// files alongside `fea.manifest.json`:
//
//   fea.mesh.glb           — un-deformed geometry
//   fea.mesh.edges.bin     — element-edge wireframe sidecar
//   fea.mesh.elements.bin  — per-element draw ranges (AFEM)
//   fea.<field>.bin        — nodal field blobs (AFBL)
//   fea.<field>.<type>.elements.bin — element-field blobs (AFEL)
//   fea.beam_solids.*      — optional beam-solid mesh + sidecars
//
// The standalone adapy-viewer fetches these out of a per-source
// `_derived/<src>.fea/` namespace via `viewerApi.getBlob`. paradoc
// fetches them out of `<bundle>/assets/3d/<key>/` via paradoc-serve's
// `/api/docs/{id}/3d/{key}/fea/{filename:path}` endpoint, or — in
// static mode — a relative path under the SPA's asset base.
//
// The orchestration logic (load mesh, parse blobs, build morph
// targets, drive animations) is identical regardless of where the
// bytes come from. Passing a `FeaFetcher` lets both call sites share
// the same loader.

/**
 * Resolve a manifest-relative filename to raw bytes.
 *
 * The argument is exactly what the manifest fields refer to
 * (`field.blob.url`, `manifest.mesh.url`, `manifest.mesh.edges_url`,
 * etc.) — flat filenames like `fea.U.bin`, `fea.mesh.glb`,
 * `fea.mesh.edges.bin`. The fetcher's job is to translate that into
 * whatever its storage convention requires (per-source `_derived/`
 * prefix for the WS-bake-job, `/api/docs/.../fea/<filename>` URL for
 * paradoc) and return the bytes.
 *
 * Concrete implementations:
 *   * `makeViewerApiFetcher(scope, sourceKey)` — wraps
 *     `viewerApi.getBlob` with the `_derived/<src>.fea/` prefix.
 *     Used by the standalone viewer.
 *   * `makeParadocFetcher(apiBase, docId, key)` — wraps
 *     `authedFetch` against paradoc-serve's REST endpoint or the
 *     static asset URL. Used by paradoc-embed.
 */
export type FeaFetcher = (filename: string) => Promise<ArrayBuffer>;

/**
 * Fetch a byte range `[start, end]` (inclusive) of a manifest-relative
 * file. Returns the bytes plus whether the server actually honoured the
 * range (`ranged: true` ⇒ a 206 with exactly that window; `ranged:
 * false` ⇒ the server ignored Range and sent the whole object, e.g. a
 * legacy gzip-at-rest field blob). Callers use `ranged` to decide
 * between using the buffer directly as one step vs. parsing the whole
 * blob and slicing.
 *
 * This is what makes opening a many-step field (a 200-mode eigen deck)
 * fast: the viewer pulls only the shown step (~one stride) instead of
 * downloading every step's values up front.
 */
export type FeaRangeFetcher = (
    filename: string,
    start: number,
    end: number,
) => Promise<{buf: ArrayBuffer; ranged: boolean}>;
