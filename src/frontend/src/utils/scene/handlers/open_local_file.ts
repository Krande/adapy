// Open files from the user's disk (File -> Open, or dropped on the 3D view). GLB and glTF are
// read in the browser and loaded straight into the scene, in any viewer: nothing is uploaded and
// nothing is kept in storage. Other formats need the server to convert them: on a REST viewer
// that converts, the user is asked, then the file is uploaded to storage and loaded as a stored
// file is (the load queue converts it and shows the progress). Anything else is refused with the
// reason (`localOpenPlan`).

import {alertDialog, confirmDialog, errorText} from "@/plugins/dialogs";
import {useModelState} from "@/state/modelState";

import {localOpenEnv, localOpenPlan} from "./localOpenPlan";

export interface OpenLocalOptions {
    /** "replace" clears the scene for the first file (File -> Open); "add" keeps what is loaded
     *  (a drop on the view). Later files are always added. */
    placement?: "replace" | "add";
}

function looksLikeExternalRefs(e: unknown): boolean {
    return /fetch|load|uri|buffer|404|Failed/i.test(errorText(e));
}

/** Read GLB / glTF `file` and put it in the scene. */
async function openInScene(file: File, placement: "replace" | "overlay"): Promise<void> {
    const {loadModel} = await import("@/components/viewer/sceneHelpers/loadModel");
    const bytes = new Uint8Array(await file.arrayBuffer());
    await loadModel({sourceName: file.name, bytes: {from: "bytes", bytes}, placement});
}

/** Upload `file` to storage and load it from there (the server converts it on the way). */
async function uploadAndOpen(file: File): Promise<void> {
    const [{uploadFile}, {useLoadQueueStore}] = await Promise.all([
        import("./upload_source_file"),
        import("@/state/loadQueueStore"),
    ]);
    await uploadFile(file, {autoConvert: false});
    useLoadQueueStore.getState().enqueue({name: file.name});
}

/** Open `files`, each as its kind allows. Problems are told in a dialog, not thrown. */
export async function openLocalFiles(files: readonly File[], opts: OpenLocalOptions = {}): Promise<void> {
    if (files.length === 0) return;
    const env = localOpenEnv();
    const plans = files.map((file) => ({file, plan: localOpenPlan(file.name, env)}));
    const refused = plans.filter((p) => p.plan.kind === "refuse");
    const uploads = plans.filter((p) => p.plan.kind === "upload").map((p) => p.file);
    const local = plans.filter((p) => p.plan.kind === "scene").map((p) => p.file);

    if (refused.length) {
        await alertDialog({
            title: refused.length === 1 ? `Cannot open ${refused[0].file.name}` : `Cannot open ${refused.length} files`,
            body: refused.map((p) => (refused.length === 1 ? "" : `${p.file.name}: `) + (p.plan as {reason: string}).reason),
            tone: "danger",
        });
    }

    let first = opts.placement !== "add" || useModelState.getState().loadedSourceNames.size === 0;
    for (const file of local) {
        try {
            await openInScene(file, first ? "replace" : "overlay");
            first = false;
        } catch (e) {
            const gltf = file.name.toLowerCase().endsWith(".gltf");
            await alertDialog({
                title: `Could not open ${file.name}`,
                body: [
                    errorText(e),
                    ...(gltf && looksLikeExternalRefs(e)
                        ? ["A .gltf whose buffers or images are separate files cannot be opened from disk: use a .glb, or a glTF with them embedded."]
                        : []),
                ],
                tone: "danger",
            });
        }
    }

    if (uploads.length) {
        const names = uploads.map((f) => f.name).join(", ");
        const ok = await confirmDialog({
            title: uploads.length === 1 ? `Open ${names}` : `Open ${uploads.length} files`,
            body: [
                `${uploads.length === 1 ? "This file is" : "These files are"} converted by the server before the viewer can show ${uploads.length === 1 ? "it" : "them"}.`,
                `${uploads.length === 1 ? "It is" : "They are"} uploaded to storage (in the current scope), converted, then loaded. Only GLB and glTF open without an upload.`,
            ],
            confirmLabel: "Upload and open",
        });
        if (!ok) return;
        for (const file of uploads) {
            try {
                await uploadAndOpen(file);
            } catch (e) {
                await alertDialog({title: `Could not upload ${file.name}`, body: errorText(e), tone: "danger"});
            }
        }
    }
}

/** Accept files dropped from the desktop on `el` (the 3D view), adding them to the scene.
 *  Drags that carry no files (a storage row, a panel tab) are left to their own targets. */
export function bindLocalFileDrop(el: HTMLElement): () => void {
    const carriesFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const over = (e: DragEvent) => {
        if (!carriesFiles(e)) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    const drop = (e: DragEvent) => {
        if (!carriesFiles(e)) return;
        e.preventDefault();
        const files = Array.from(e.dataTransfer?.files ?? []);
        void openLocalFiles(files, {placement: "add"});
    };
    el.addEventListener("dragover", over);
    el.addEventListener("drop", drop);
    return () => {
        el.removeEventListener("dragover", over);
        el.removeEventListener("drop", drop);
    };
}
