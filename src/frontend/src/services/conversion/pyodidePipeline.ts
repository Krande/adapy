// In-browser conversion pipeline using Pyodide.
// Source formats supported, picked by extension (see wasmSupport.ts):
//   - .ifc                       → ifcopenshell (wasm wheel) + trimesh
//   - .step / .stp               → adacpp (wasm wheel) via adapy.cad
//   - .obj/.stl/.ply/.gltf/...   → trimesh
// Fetches source bytes from storage, runs the conversion in a Web
// Worker, then PUTs the resulting GLB back to storage so the existing
// VIEW_FILE_OBJECT path can serve it. Records a metrics-rich audit row
// (audit/local) so in-browser conversions show up in the audit panel
// exactly like worker conversions.

import {useConversionStore, ConversionJob} from "@/state/conversionStore";
import {convertViaPyodide, convertViaPyodideStream} from "@/utils/pyodide/pyodide_converter";
import {viewerApi, ScopeUrl, TargetFormat} from "@/services/viewerApi";
import {detectWasmFormat, isWasmFeaSource, wasmSupportsConversion} from "./wasmSupport";

// Identifies in-browser conversions in the audit panel (worker_image_tag
// prefix "wasm:" → "WASM" badge).
const WASM_IMAGE_TAG = "wasm:pyodide-0.29.4";

export async function convertViaPyodideAndUpload(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat = "glb",
    opts?: {auditRunId?: string | null},
): Promise<string> {
    const detected = detectWasmFormat(sourceKey);
    if (!detected) {
        throw new Error(
            `pyodide pipeline does not support source extension for ${sourceKey}`,
        );
    }
    if (!wasmSupportsConversion(sourceKey, targetFormat)) {
        throw new Error(
            `pyodide pipeline does not support ${detected.format} → ${targetFormat}`,
        );
    }
    const {format, ext} = detected;
    const storeKey = `${sourceKey}::${targetFormat}`;
    const store = useConversionStore.getState();
    const startedAt = Date.now();
    const job: ConversionJob = {
        sourceKey: storeKey,
        jobId: "pyodide",
        derivedKey: "",
        status: "running",
        progress: 0.05,
        stage: "fetching source",
        error: null,
        startedAt,
    };
    store.setJob(storeKey, job);

    // Open the audit row first so the conversion is visible as "running"
    // in the panel. Best-effort: a DB-less deployment (or a transient
    // failure) must not block the conversion itself.
    let auditJobId: string | null = null;
    try {
        auditJobId = await viewerApi.auditLocalCreate(scope, {
            key: sourceKey,
            target_format: targetFormat,
            audit_run_id: opts?.auditRunId ?? null,
            image_tag: WASM_IMAGE_TAG,
        });
    } catch {
        /* proceed without an audit row */
    }

    const finishAudit = async (
        body: Parameters<typeof viewerApi.auditLocalUpdate>[2],
    ) => {
        if (!auditJobId) return;
        try {
            await viewerApi.auditLocalUpdate(scope, auditJobId, body);
        } catch {
            /* best-effort — a lost audit update must not surface to the user */
        }
    };

    try {
        // Streaming path: a Sesam .sin → GLB reads the source in ranges from a
        // presigned (Range-capable) URL instead of downloading the whole file,
        // so a multi-GB deck never has to be staged in wasm memory. Only the
        // presign *availability* gates this — if the backend can't mint a GET
        // URL (local-disk deployments 503), fall back to the buffered path.
        // A presigned URL that then fails mid-convert is NOT retried buffered:
        // re-downloading a huge file would just OOM.
        let downloadUrl: {url: string; size: number} | null = null;
        if (format === "fea_glb" && ext === "sin") {
            try {
                const dl = await viewerApi.requestDownloadUrl(scope, sourceKey);
                downloadUrl = {url: dl.url, size: dl.size};
            } catch {
                downloadUrl = null; // presign unavailable → buffered fallback
            }
        }

        let outBytes: Uint8Array;
        let readBytes: number;
        if (downloadUrl) {
            store.setJob(storeKey, {
                ...job,
                progress: 0.15,
                stage: `streaming ${format} → ${targetFormat} in browser`,
            });
            outBytes = await convertViaPyodideStream(format, downloadUrl.url, {
                ext,
                target: targetFormat,
                size: downloadUrl.size,
                onLog: (msg) => store.setJob(storeKey, {...(store.jobs[storeKey] || job), stage: msg}),
            });
            readBytes = downloadUrl.size; // source size; only a fraction is actually fetched
        } else {
            const sourceBuf = await viewerApi.getBlob(scope, sourceKey);
            readBytes = sourceBuf.byteLength; // capture before the buffer is transferred to the worker

            store.setJob(storeKey, {
                ...job,
                progress: 0.15,
                stage: `converting ${format} → ${targetFormat} in browser`,
            });

            outBytes = await convertViaPyodide(format, sourceBuf, {
                ext,
                target: targetFormat,
                onLog: (msg) => store.setJob(storeKey, {...(store.jobs[storeKey] || job), stage: msg}),
            });
        }

        store.setJob(storeKey, {
            ...(store.jobs[storeKey] || job),
            progress: 0.9,
            stage: "uploading derived",
        });

        // putDerivedBlob computes the canonical derived key server-side
        // (and skips its own auto-audit via managed_audit=1).
        const derivedKey = await viewerApi.putDerivedBlob(scope, sourceKey, targetFormat, outBytes);

        store.setJob(storeKey, {
            ...(store.jobs[storeKey] || job),
            progress: 1.0,
            stage: "ready",
            status: "done",
            derivedKey,
        });

        await finishAudit({
            status: "done",
            duration_ms: Date.now() - startedAt,
            read_bytes: readBytes,
            write_bytes: outBytes.byteLength,
        });
        return derivedKey;
    } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        store.setJob(storeKey, {
            ...(store.jobs[storeKey] || job),
            status: "error",
            stage: "error",
            error: msg,
        });
        await finishAudit({
            status: "error",
            duration_ms: Date.now() - startedAt,
            error: msg,
        });
        throw err;
    }
}

function feaExt(sourceKey: string): string {
    const lower = sourceKey.toLowerCase();
    const dot = lower.lastIndexOf(".");
    return dot >= 0 ? lower.slice(dot + 1) : "";
}

/**
 * Bake a FEA result file (.rmed/.med/.sif/.sin) into the streaming-viewer
 * artefact tree entirely in the browser, then upload the tree (as a zip)
 * to ``/fea/artefacts``. The existing streaming FEA reader then consumes
 * ``_derived/<source>.fea/`` unchanged — the WASM counterpart of
 * ``viewerApi.feaManifest`` (the server worker bake). Records a
 * metrics-rich audit row like the GLB path.
 */
export async function convertViaPyodideFeaBake(
    scope: ScopeUrl,
    sourceKey: string,
    opts?: {auditRunId?: string | null},
): Promise<void> {
    if (!isWasmFeaSource(sourceKey)) {
        throw new Error(`pyodide FEA bake does not support source: ${sourceKey}`);
    }
    const ext = feaExt(sourceKey);
    const storeKey = `${sourceKey}::fea`;
    const store = useConversionStore.getState();
    const startedAt = Date.now();
    const job: ConversionJob = {
        sourceKey: storeKey,
        jobId: "pyodide",
        derivedKey: "",
        status: "running",
        progress: 0.05,
        stage: "fetching source",
        error: null,
        startedAt,
    };
    store.setJob(storeKey, job);

    let auditJobId: string | null = null;
    try {
        auditJobId = await viewerApi.auditLocalCreate(scope, {
            key: sourceKey,
            target_format: "fea_artefacts",
            audit_run_id: opts?.auditRunId ?? null,
            image_tag: WASM_IMAGE_TAG,
        });
    } catch {
        /* proceed without an audit row */
    }

    const finishAudit = async (
        body: Parameters<typeof viewerApi.auditLocalUpdate>[2],
    ) => {
        if (!auditJobId) return;
        try {
            await viewerApi.auditLocalUpdate(scope, auditJobId, body);
        } catch {
            /* best-effort */
        }
    };

    try {
        // Stream a .sin bake from a presigned Range URL so a multi-GB deck
        // never has to be staged in wasm memory (the source can exceed the
        // wasm32 ceiling outright). Falls back to the buffered getBlob path
        // when presign is unavailable (local-disk backends 503). Other FEA
        // sources (.rmed/.med/.sif) stay buffered for now.
        let downloadUrl: {url: string; size: number} | null = null;
        if (ext === "sin") {
            try {
                const dl = await viewerApi.requestDownloadUrl(scope, sourceKey);
                downloadUrl = {url: dl.url, size: dl.size};
            } catch {
                downloadUrl = null;
            }
        }

        let zip: Uint8Array;
        let readBytes: number;
        if (downloadUrl) {
            store.setJob(storeKey, {...job, progress: 0.15, stage: "streaming FEA bake in browser"});
            zip = await convertViaPyodideStream("fea", downloadUrl.url, {
                ext,
                size: downloadUrl.size,
                onLog: (msg) => store.setJob(storeKey, {...(store.jobs[storeKey] || job), stage: msg}),
            });
            readBytes = downloadUrl.size; // source size; only a fraction is actually fetched
        } else {
            const sourceBuf = await viewerApi.getBlob(scope, sourceKey);
            readBytes = sourceBuf.byteLength;

            store.setJob(storeKey, {...job, progress: 0.15, stage: "baking FEA result in browser"});

            zip = await convertViaPyodide("fea", sourceBuf, {
                ext,
                onLog: (msg) => store.setJob(storeKey, {...(store.jobs[storeKey] || job), stage: msg}),
            });
        }

        store.setJob(storeKey, {
            ...(store.jobs[storeKey] || job),
            progress: 0.9,
            stage: "uploading artefacts",
        });

        const manifestKey = await viewerApi.uploadFeaArtefacts(scope, sourceKey, zip);

        store.setJob(storeKey, {
            ...(store.jobs[storeKey] || job),
            progress: 1.0,
            stage: "done",
            status: "done",
            derivedKey: manifestKey,
        });

        await finishAudit({
            status: "done",
            duration_ms: Date.now() - startedAt,
            read_bytes: readBytes,
            write_bytes: zip.byteLength,
        });
    } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        store.setJob(storeKey, {
            ...(store.jobs[storeKey] || job),
            status: "error",
            stage: "error",
            error: msg,
        });
        await finishAudit({
            status: "error",
            duration_ms: Date.now() - startedAt,
            error: msg,
        });
        throw err;
    }
}
