// In-browser conversion pipeline using the NATIVE (no-pyodide) adacpp CAD→GLB wasm modules
// (adacpp_step_glb / adacpp_ifc_glb). STEP/STP + IFC → GLB. Fetches source bytes from storage, runs
// the embind module in a Web Worker, PUTs the resulting GLB back to storage (same derived-key
// contract as the server + pyodide paths), and records a metrics-rich audit row so the conversion
// shows up in the audit panel with a "WASM" badge exactly like the pyodide path.

import {useConversionStore, ConversionJob} from "@/state/conversionStore";
import {viewerApi, ScopeUrl, TargetFormat} from "@/services/viewerApi";
import {
    nativeCadToGlb,
    nativeCadToGlbStreaming,
    nativeCadGlbOpfsAvailable,
    type CadKind,
} from "@/utils/nativeConvert/cadGlbConverter";

// Distinguishes the native embind module from the pyodide path in the audit panel.
const WASM_IMAGE_TAG = "wasm:native-cadglb";

// Above this source size we prefer the OPFS-streaming tier (source read off-disk via pread, bounded
// RSS) over buffering the whole source into the wasm heap — provided OPFS + a presigned URL are both
// available. Below it, the buffered MEMFS path is simpler and plenty (and the validated default).
const OPFS_STREAM_THRESHOLD = 100 * 1024 * 1024; // 100 MB

function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}

// Which native module handles a source extension (STEP/STP → step_glb, IFC → ifc_glb).
function cadKindFor(sourceKey: string): CadKind | null {
    const ext = extOf(sourceKey);
    if (ext === ".step" || ext === ".stp") return "step";
    if (ext === ".ifc") return "ifc";
    return null;
}

/** Does a native (no-pyodide) module handle this (source, target)? {STEP,STP,IFC} → GLB. */
export function nativeCadGlbSupported(sourceKey: string, targetFormat: TargetFormat): boolean {
    return targetFormat === "glb" && cadKindFor(sourceKey) !== null;
}

export async function convertViaWasmNativeAndUpload(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat = "glb",
    opts?: {auditRunId?: string | null},
): Promise<string> {
    const kind = cadKindFor(sourceKey);
    if (targetFormat !== "glb" || !kind) {
        throw new Error(`native wasm module only converts STEP/IFC → GLB (got ${sourceKey} → ${targetFormat})`);
    }
    const storeKey = `${sourceKey}::${targetFormat}`;
    const store = useConversionStore.getState();
    const startedAt = Date.now();
    const job: ConversionJob = {
        sourceKey: storeKey,
        jobId: "wasm-native",
        derivedKey: "",
        status: "running",
        progress: 0.05,
        stage: "fetching source",
        error: null,
        startedAt,
    };
    store.setJob(storeKey, job);

    // Open the audit row first so the conversion shows as "running". Best-effort.
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
    const finishAudit = async (body: Parameters<typeof viewerApi.auditLocalUpdate>[2]) => {
        if (!auditJobId) return;
        try {
            await viewerApi.auditLocalUpdate(scope, auditJobId, body);
        } catch {
            /* best-effort */
        }
    };

    try {
        // Prefer the OPFS-streaming tier for large sources: mint a presigned URL, feature-detect
        // OPFS, and stream the STEP through OPFS (bounded RSS) so a multi-GB deck never has to fit
        // the wasm heap. Falls back to buffering the whole source into the wasm heap when the source
        // is small, presign is unavailable (local-disk backends 503), or OPFS isn't supported. A
        // streaming run is NOT retried buffered — re-reading a huge deck into the heap would just OOM.
        const upper = kind.toUpperCase();
        let streamUrl: {url: string; size: number} | null = null;
        try {
            const dl = await viewerApi.requestDownloadUrl(scope, sourceKey);
            if (dl.size >= OPFS_STREAM_THRESHOLD && (await nativeCadGlbOpfsAvailable(kind))) {
                streamUrl = {url: dl.url, size: dl.size};
            }
        } catch {
            streamUrl = null; // presign unavailable → buffered fallback
        }

        let glb: ArrayBuffer;
        let products: number;
        let ms: number;
        let readBytes: number;
        if (streamUrl) {
            store.setJob(storeKey, {
                ...(store.jobs[storeKey] || job),
                progress: 0.15,
                stage: `streaming ${upper} → GLB in browser (native, OPFS)`,
            });
            ({glb, products, ms} = await nativeCadToGlbStreaming(kind, streamUrl.url));
            readBytes = streamUrl.size; // source size; only streamed, never staged whole
        } else {
            const sourceBuf = await viewerApi.getBlob(scope, sourceKey);
            readBytes = sourceBuf.byteLength; // capture before the buffer is transferred to the worker

            store.setJob(storeKey, {
                ...(store.jobs[storeKey] || job),
                progress: 0.15,
                stage: `converting ${upper} → GLB in browser (native)`,
            });

            ({glb, products, ms} = await nativeCadToGlb(kind, sourceBuf));
        }
        const outBytes = new Uint8Array(glb);

        store.setJob(storeKey, {
            ...(store.jobs[storeKey] || job),
            progress: 0.9,
            stage: `uploading derived (${products.toLocaleString()} products, ${Math.round(ms)} ms)`,
        });

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
