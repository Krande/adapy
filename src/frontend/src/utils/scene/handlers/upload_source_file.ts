import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {ensureConvertedGlb} from "@/services/conversion";
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
 * URL — we just PUT and watch progress. */
async function putToPresignedUrl(
    url: string,
    file: File,
    onProgress?: (loaded: number, total: number) => void,
): Promise<void> {
    await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", url);
        // S3-style presigned URLs sign a known set of headers; sending
        // an unsigned one (e.g. Authorization) makes the request fail
        // with SignatureDoesNotMatch. Stick to body only.
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
    // Sesam result database (text-based). Renders as a tessellated GLB
    // showing the first available (step, field) by default; large files
    // (>~200 MB) may need the direct-to-storage upload path.
    ".sif",
];

// Custom event the upload picker listens for; lets UI surfaces (menu
// button, context menu) ask the picker to open without each one
// owning its own hidden <input>.
export const UPLOAD_TRIGGER_EVENT = "ada-upload-trigger";

export function triggerUploadPicker(): void {
    window.dispatchEvent(new CustomEvent(UPLOAD_TRIGGER_EVENT));
}

export function uploadAcceptAttr(): string {
    return SUPPORTED_EXTS.join(",");
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
    if (!SUPPORTED_EXTS.includes(ext)) {
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
        await putToPresignedUrl(presigned.url, file, opts?.onProgress);
        await viewerApi.completeUpload(scope, key);
    } else {
        await viewerApi.putBlob(scope, key, file, {onProgress: opts?.onProgress});
    }
    await request_list_of_files_from_server();

    const autoConvert = opts?.autoConvert !== false;
    const convertEnabled = runtime.convertEnabled();
    if (autoConvert && convertEnabled && ext !== ".glb") {
        // Fire-and-forget: ensureConvertedGlb updates the conversion
        // store as it polls so the UI reflects progress without
        // blocking the upload helper.
        ensureConvertedGlb(scope, key).catch((err) => {
            console.warn("auto-convert after upload failed", err);
        });
    }
}
