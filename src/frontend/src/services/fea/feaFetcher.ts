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

// Session-wide kill switch for per-step Range fetches. Some deployments
// (a reverse proxy that mishandles Range/206, a service worker, an old
// backend without Range support) make a ranged request fail outright
// ("Failed to fetch"). The first such failure flips this off and every
// field load falls back to the whole-blob fetch — so per-step is never
// *worse* than the old path, just not faster. Reset on page reload.
let _rangeSupported = true;
export function feaRangeSupported(): boolean {
    return _rangeSupported;
}
export function disableFeaRange(): void {
    _rangeSupported = false;
}

// ── OPFS (browser-materialised artefacts) ───────────────────────────────────
//
// A load combination materialised in the browser lives in the OPFS store
// (services/fea/opfsFeaStore.ts), owned by the FEA engine worker. These
// fetchers read it through that worker: drop-ins for the viewer-API ones, so
// every loader downstream is unaware of where a case was computed.

/** Read a file under one source's store tree (``cases/<d>/<f>``,
 *  ``base/<f>``, ``envelopes/<field>/<f>``), whole or an inclusive range;
 *  null when the store does not hold it (or not that range). */
export type FeaStoreReader = (
    rel: string,
    range?: {start: number; end: number},
) => Promise<ArrayBuffer | null>;

function missing(filename: string): Error {
    const err = new Error(`not in the browser FEA store: ${filename}`);
    (err as {status?: number}).status = 404;
    return err;
}

/** A ``FeaFetcher`` over the store. ``storePrefix`` maps a manifest-relative
 *  filename to its store path (``cases/101-ab12cd34/`` stays as is; a base
 *  filename becomes ``base/<f>``). ``fallback`` answers what the store does
 *  not hold. */
export function makeOpfsFetcher(
    read: FeaStoreReader,
    opts: {storePath?: (filename: string) => string; fallback?: FeaFetcher} = {},
): FeaFetcher {
    const storePath = opts.storePath ?? defaultStorePath;
    return async (filename) => {
        let buf: ArrayBuffer | null = null;
        try {
            buf = await read(storePath(filename));
        } catch {
            buf = null;
        }
        if (buf) return buf;
        if (opts.fallback) return opts.fallback(filename);
        throw missing(filename);
    };
}

/** The ranged counterpart: always ``ranged: true`` when the store answers. */
export function makeOpfsRangeFetcher(
    read: FeaStoreReader,
    opts: {storePath?: (filename: string) => string; fallback?: FeaRangeFetcher} = {},
): FeaRangeFetcher {
    const storePath = opts.storePath ?? defaultStorePath;
    return async (filename, start, end) => {
        let buf: ArrayBuffer | null = null;
        try {
            buf = await read(storePath(filename), {start, end});
        } catch {
            buf = null;
        }
        if (buf) return {buf, ranged: true};
        if (opts.fallback) return opts.fallback(filename, start, end);
        throw missing(filename);
    };
}

/** Manifest-relative filename -> store path: the case / envelope trees keep
 *  their relative path, anything else is a base blob. */
export function defaultStorePath(filename: string): string {
    const f = filename.replace(/^\/+/, "");
    if (f.startsWith("cases/") || f.startsWith("envelopes/")) return f;
    return `base/${f}`;
}

// ── local routes ────────────────────────────────────────────────────────────
//
// Which manifest-relative paths of a source the browser store answers. The
// local case engine registers a case's directory once it has materialised
// it there; ``makeViewerApiFetcher`` consults this before going to the server,
// so the renderer reads a browser-computed case through the same fetcher it
// reads everything else with.

interface LocalRoute {
    prefix: string;
    read: FeaStoreReader;
}

const LOCAL_ROUTES = new Map<string, LocalRoute[]>();

function routeKey(scope: string, sourceKey: string): string {
    return `${scope}::${sourceKey.replace(/^\/+/, "")}`;
}

/** Serve every path of ``(scope, sourceKey)`` under ``prefix`` from ``read``,
 *  which is handed the MANIFEST-RELATIVE filename (map it with
 *  ``defaultStorePath``). A null answer falls through to the server.
 *  Returns the unregister function. */
export function registerLocalFeaRoute(
    scope: string,
    sourceKey: string,
    prefix: string,
    read: FeaStoreReader,
): () => void {
    const key = routeKey(scope, sourceKey);
    const route: LocalRoute = {prefix: prefix.replace(/^\/+/, ""), read};
    const list = (LOCAL_ROUTES.get(key) ?? []).filter((r) => r.prefix !== route.prefix);
    list.push(route);
    LOCAL_ROUTES.set(key, list);
    return () => {
        const cur = LOCAL_ROUTES.get(key);
        if (!cur) return;
        const next = cur.filter((r) => r !== route);
        if (next.length) LOCAL_ROUTES.set(key, next);
        else LOCAL_ROUTES.delete(key);
    };
}

/** The store reader that answers ``filename`` of a source, if any. */
export function localFeaRoute(scope: string, sourceKey: string, filename: string): FeaStoreReader | null {
    const list = LOCAL_ROUTES.get(routeKey(scope, sourceKey));
    if (!list) return null;
    const f = filename.replace(/^\/+/, "");
    let best: LocalRoute | null = null;
    for (const r of list) {
        if (f.startsWith(r.prefix) && (!best || r.prefix.length > best.prefix.length)) best = r;
    }
    return best?.read ?? null;
}

export function clearLocalFeaRoutes(): void {
    LOCAL_ROUTES.clear();
}
