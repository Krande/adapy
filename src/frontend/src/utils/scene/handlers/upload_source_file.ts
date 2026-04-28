import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {ensureConvertedGlb} from "@/services/conversion";
import {runtime} from "@/runtime/config";
import {viewerApi, ScopeUrl} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

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
    await viewerApi.putBlob(scope, key, file, {onProgress: opts?.onProgress});
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
