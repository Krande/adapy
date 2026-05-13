import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {ensureBakedFeaManifest, ensureConvertedGlb} from "@/services/conversion";
import {runtime} from "@/runtime/config";
import {viewerApi, ScopeUrl} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

// Mirror the server's _DIRECT_UPLOAD_THRESHOLD_BYTES. The server is
// authoritative — it 413s above this — but knowing it client-side lets
// us avoid one wasted round-trip and pick the right code path up front.
const DIRECT_UPLOAD_THRESHOLD_BYTES = 200 * 1024 * 1024;

/** Upload via a presigned URL straight to the object store. Bypasses
 * the API process so multi-hundred-MB files don't buffer through
 * Python. Caller must ensure the server side has already vended the
 * URL — we just PUT and watch progress.
 *
 * When ``contentEncoding`` is set (typically ``"gzip"`` for text-
 * heavy formats like ``.sif`` / ``.ifc`` / ``.step``), the body is
 * piped through the matching ``CompressionStream`` before the PUT,
 * and the same value goes on the request as ``Content-Encoding``.
 * SigV4 doesn't sign that header by default — it rides as opaque
 * metadata, so the signature stays valid and the object store
 * records the encoding on the object's metadata.
 *
 * Fallback: browsers without ``CompressionStream`` (or where the
 * fetch can't stream a body — i.e. Safari <17) skip compression
 * silently and PUT raw bytes. The admin compression-sweep picks it
 * up later. */
async function putToPresignedUrl(
    url: string,
    file: File,
    onProgress?: (loaded: number, total: number) => void,
    contentEncoding?: string | null,
): Promise<void> {
    // XHR doesn't support streaming a ReadableStream body. For the
    // compress-on-the-fly path we have to use fetch() — at the cost
    // of losing reliable upload-progress events on the very largest
    // files (only download-progress is exposed; upload-progress
    // doesn't fire on streamed bodies in current browsers).
    if (
        contentEncoding === "gzip" &&
        typeof (globalThis as any).CompressionStream !== "undefined"
    ) {
        const cs = new (globalThis as any).CompressionStream("gzip") as
            { readable: ReadableStream<Uint8Array>; writable: WritableStream<Uint8Array> };
        const compressedBody = file.stream().pipeThrough(cs);
        const r = await fetch(url, {
            method: "PUT",
            headers: {"Content-Encoding": "gzip"},
            body: compressedBody,
            // duplex: "half" is required by fetch when the body is a
            // ReadableStream; Chrome/Firefox enforce it.
            duplex: "half",
        } as RequestInit & {duplex: "half"});
        if (!r.ok) {
            throw new Error(`presigned PUT failed: ${r.status} ${await r.text()}`);
        }
        // Approximate progress: fetch on a streamed body doesn't fire
        // upload events; report 100% once the PUT resolves so the UI
        // doesn't sit at 0%. Better-than-nothing until streams get a
        // standard progress API.
        if (onProgress) onProgress(file.size, file.size);
        return;
    }

    // Buffered path — XHR gives us reliable upload progress.
    await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", url);
        xhr.upload.addEventListener("progress", (e) => {
            if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
        });
        xhr.addEventListener("load", () => {
            if (xhr.status >= 200 && xhr.status < 300) resolve();
            else reject(new Error(`presigned PUT failed: ${xhr.status} ${xhr.responseText || ""}`));
        });
        xhr.addEventListener("error", () => reject(new Error("presigned PUT network error")));
        xhr.addEventListener("abort", () => reject(new Error("presigned PUT aborted")));
        xhr.send(file);
    });
}

// Built-in source extensions adapy itself handles (the base worker
// image carries all the parsers). Capability workers can announce
// additional extensions via their registry entry; runtime() pulls
// them from /api/config and ``acceptedSourceExts`` below merges the
// two lists for picker / drag-drop filtering.
const SUPPORTED_EXTS = [
    ".glb", ".gltf",
    ".ifc", ".step", ".stp",
    ".xml", ".inp", ".fem",
    ".sat", ".acis",
    ".obj", ".stl", ".ply", ".dae", ".off",
    // Multi-file analysis bundles. Worker unpacks + validates the
    // include chain at convert time; rejects mixed-format / ambiguous
    // entry / missing includes with a clear error.
    ".zip",
    // FEA result formats consumed by the streaming-viewer bake:
    //   .sif  Sesam result database (text-based, gzip-compressible).
    //   .rmed Code_Aster / Salome result file (HDF5).
    ".sif",
    ".rmed",
];

function acceptedSourceExts(): readonly string[] {
    const extra = runtime.extraSourceExts();
    if (extra.length === 0) return SUPPORTED_EXTS;
    const merged = new Set<string>(SUPPORTED_EXTS);
    for (const e of extra) {
        merged.add(e.startsWith(".") ? e.toLowerCase() : `.${e.toLowerCase()}`);
    }
    return Array.from(merged);
}

// True for source extensions whose viewing path is the streaming FEA
// bake (`/fea/manifest`), not the legacy single-GLB pipeline
// (`/convert`). Server-computed from the worker registry minus the
// legacy-convertable set (so .sif, which has both paths, falls out and
// keeps its eager GLB-preview behaviour). Auto-convert routes these to
// the FEA bake instead of /convert; otherwise /convert would 415.
function isStreamingOnlyExt(ext: string): boolean {
    for (const e of runtime.streamingOnlyExts()) {
        const norm = e.startsWith(".") ? e.toLowerCase() : `.${e.toLowerCase()}`;
        if (norm === ext) return true;
    }
    return false;
}

// Custom event the upload picker listens for; lets UI surfaces (menu
// button, context menu) ask the picker to open without each one
// owning its own hidden <input>.
export const UPLOAD_TRIGGER_EVENT = "ada-upload-trigger";

export function triggerUploadPicker(): void {
    window.dispatchEvent(new CustomEvent(UPLOAD_TRIGGER_EVENT));
}

export function uploadAcceptAttr(): string {
    return acceptedSourceExts().join(",");
}

function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}

/**
 * Upload a user-picked file to the viewer's storage backend under the
 * caller's current scope, then refresh the file list. Non-GLB uploads
 * also enqueue a conversion job so the file is ready to view by the
 * time the user clicks it.
 */
export async function uploadFile(
    file: File,
    opts?: {
        autoConvert?: boolean;
        onProgress?: (loaded: number, total: number) => void;
        /** Override the scope for this upload. Defaults to whatever
         * the user has selected in the scope picker. */
        scope?: ScopeUrl;
    },
): Promise<void> {
    const key = file.name;
    const ext = extOf(key);
    if (!acceptedSourceExts().includes(ext)) {
        throw new Error(`unsupported file type: ${ext || "(no extension)"}`);
    }

    const scope = opts?.scope ?? scopeUrlPart(useScopeStore.getState().current);
    if (file.size > DIRECT_UPLOAD_THRESHOLD_BYTES) {
        // Hand the bytes straight to the object store via a presigned
        // URL. If the server side can't presign (LocalStore), it 503s
        // and the error bubbles to the caller — we don't transparently
        // fall back to the buffered path because that would silently
        // 413 on this same request anyway.
        const presigned = await viewerApi.requestUploadUrl(scope, key);
        await putToPresignedUrl(
            presigned.url, file, opts?.onProgress, presigned.content_encoding,
        );
        await viewerApi.completeUpload(scope, key);
    } else {
        await viewerApi.putBlob(scope, key, file, {onProgress: opts?.onProgress});
    }
    await request_list_of_files_from_server();

    const autoConvert = opts?.autoConvert !== false;
    const convertEnabled = runtime.convertEnabled();
    if (autoConvert && convertEnabled && ext !== ".glb") {
        // Two pipelines, picked by extension:
        //   * streaming-only sources (.rmed, .odb, ...) bake via
        //     /fea/manifest — the legacy /convert path 415s for them.
        //   * everything else takes the legacy single-GLB pipeline.
        // Both update the same useConversionStore so the bottom-right
        // toast tracks progress regardless of which path ran.
        const baker = isStreamingOnlyExt(ext)
            ? ensureBakedFeaManifest(scope, key)
            : ensureConvertedGlb(scope, key);
        baker.catch((err) => {
            console.warn("auto-convert after upload failed", err);
        });
    }
}
