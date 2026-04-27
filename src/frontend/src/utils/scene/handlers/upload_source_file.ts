import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {ensureConvertedGlb} from "@/services/conversion";
import {runtime} from "@/runtime/config";
import {viewerApi} from "@/services/viewerApi";

const SUPPORTED_EXTS = [
    ".glb", ".gltf",
    ".ifc", ".step", ".stp",
    ".xml", ".inp", ".fem",
    ".sat", ".acis",
    ".obj", ".stl", ".ply", ".dae", ".off",
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
 * Upload a user-picked file to the viewer's storage backend, then
 * refresh the file list. Non-GLB uploads also enqueue a conversion
 * job so the file is ready to view by the time the user clicks it.
 */
export async function uploadFile(file: File, opts?: {autoConvert?: boolean}): Promise<void> {
    const key = file.name;
    const ext = extOf(key);
    if (!SUPPORTED_EXTS.includes(ext)) {
        throw new Error(`unsupported file type: ${ext || "(no extension)"}`);
    }

    await viewerApi.putBlob(key, file);
    await request_list_of_files_from_server();

    const autoConvert = opts?.autoConvert !== false;
    const convertEnabled = runtime.convertEnabled();
    if (autoConvert && convertEnabled && ext !== ".glb") {
        // Fire-and-forget: ensureConvertedGlb updates the conversion
        // store as it polls so the UI reflects progress without
        // blocking the upload helper.
        ensureConvertedGlb(key).catch((err) => {
            console.warn("auto-convert after upload failed", err);
        });
    }
}
