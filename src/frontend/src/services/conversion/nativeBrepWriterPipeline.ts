// In-browser conversion pipeline using the NATIVE (no-pyodide) adacpp_brep_writer wasm module.
// STEP→IFC and IFC→STEP. Fetches source bytes, runs the embind writer in a Web Worker, PUTs the
// output back to storage (same derived-key contract as the server + pyodide paths), and records a
// metrics-rich audit row (WASM badge). Buffered MEMFS IO; an OPFS-streaming tier can follow the
// CAD→GLB pattern for very large B-rep files.

import {useConversionStore, ConversionJob} from "@/state/conversionStore";
import {viewerApi, ScopeUrl, TargetFormat} from "@/services/viewerApi";
import {
    nativeBrepWrite,
    nativeBrepWriteStreaming,
    nativeBrepWriterOpfsAvailable,
    type BrepDir,
} from "@/utils/nativeConvert/brepWriterConverter";

const WASM_IMAGE_TAG = "wasm:native-brepwriter";

// Above this source size, prefer the OPFS-streaming tier (source read off-disk, bounded RSS) when
// OPFS + a presigned URL are available; below it, the buffered MEMFS path.
const OPFS_STREAM_THRESHOLD = 100 * 1024 * 1024; // 100 MB

function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}

// The conversion direction for a (source ext, target). STEP/STP→IFC and IFC→STEP only.
function brepDirFor(sourceKey: string, targetFormat: TargetFormat): BrepDir | null {
    const ext = extOf(sourceKey);
    if ((ext === ".step" || ext === ".stp") && targetFormat === "ifc") return "step2ifc";
    if (ext === ".ifc" && targetFormat === "step") return "ifc2step";
    return null;
}

/** Does the native B-rep writer handle this (source, target)? STEP→IFC / IFC→STEP only. */
export function nativeBrepWriterSupported(sourceKey: string, targetFormat: TargetFormat): boolean {
    return brepDirFor(sourceKey, targetFormat) !== null;
}

export async function convertViaWasmBrepAndUpload(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat,
    opts?: {auditRunId?: string | null},
): Promise<string> {
    const dir = brepDirFor(sourceKey, targetFormat);
    if (!dir) {
        throw new Error(`native B-rep writer only does STEP→IFC / IFC→STEP (got ${sourceKey} → ${targetFormat})`);
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
        const label = dir === "step2ifc" ? "STEP → IFC" : "IFC → STEP";
        // Prefer OPFS streaming for large sources (bounded RSS): presign + feature-detect OPFS. Falls
        // back to buffering the whole source into the wasm heap when small, presign-less, or no OPFS.
        // A streaming run is NOT retried buffered (re-reading a huge file would OOM).
        let streamUrl: {url: string; size: number} | null = null;
        try {
            const dl = await viewerApi.requestDownloadUrl(scope, sourceKey);
            if (dl.size >= OPFS_STREAM_THRESHOLD && (await nativeBrepWriterOpfsAvailable())) {
                streamUrl = {url: dl.url, size: dl.size};
            }
        } catch {
            streamUrl = null; // presign unavailable → buffered fallback
        }

        let output: ArrayBuffer;
        let products: number;
        let ms: number;
        let readBytes: number;
        if (streamUrl) {
            store.setJob(storeKey, {
                ...(store.jobs[storeKey] || job),
                progress: 0.2,
                stage: `writing ${label} in browser (native, OPFS)`,
            });
            ({output, products, ms} = await nativeBrepWriteStreaming(dir, streamUrl.url));
            readBytes = streamUrl.size; // source size; only streamed, never staged whole
        } else {
            const sourceBuf = await viewerApi.getBlob(scope, sourceKey);
            readBytes = sourceBuf.byteLength; // capture before the buffer is transferred to the worker

            store.setJob(storeKey, {
                ...(store.jobs[storeKey] || job),
                progress: 0.2,
                stage: `writing ${label} in browser (native)`,
            });

            ({output, products, ms} = await nativeBrepWrite(dir, sourceBuf));
        }
        const outBytes = new Uint8Array(output);

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
