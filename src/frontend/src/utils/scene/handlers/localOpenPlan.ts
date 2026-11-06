// File -> Open and a file dropped on the 3D view: which files open straight into the scene, which
// go through storage (upload, then the server converts them), and why the others cannot open.
// Light, so the menu command builds its picker without the scene code.
import {runtime} from "@/runtime/config";

import {LOCAL_SCENE_EXTS, acceptedSourceExts, extOf} from "./sourceExts";

export type LocalOpenPlan =
    /** Read in the browser and loaded, no server involved. */
    | {readonly kind: "scene"}
    /** Uploaded to storage, then converted and loaded as a stored file is. */
    | {readonly kind: "upload"}
    | {readonly kind: "refuse"; readonly reason: string};

export interface LocalOpenEnv {
    /** The viewer talks to a REST server, with storage. */
    readonly rest: boolean;
    /** That server converts files (`CONVERT_ENABLED`). */
    readonly convertEnabled: boolean;
    /** What storage accepts (`acceptedSourceExts`). */
    readonly uploadExts: readonly string[];
}

export function localOpenEnv(): LocalOpenEnv {
    return {rest: runtime.isRestMode(), convertEnabled: runtime.convertEnabled(), uploadExts: acceptedSourceExts()};
}

/** How the file `name` opens here. */
export function localOpenPlan(name: string, env: LocalOpenEnv): LocalOpenPlan {
    const ext = extOf(name);
    if (LOCAL_SCENE_EXTS.includes(ext)) return {kind: "scene"};
    const what = ext ? `A ${ext} file` : "A file without an extension";
    if (!env.uploadExts.includes(ext)) return {kind: "refuse", reason: `${what} cannot be opened in the viewer.`};
    if (!env.rest) {
        return {
            kind: "refuse",
            reason: `${what} is converted by a server, and this viewer is not connected to one. Only GLB and glTF files open here.`,
        };
    }
    if (!env.convertEnabled) {
        return {kind: "refuse", reason: `${what} is converted by the server, and this server does not convert files. Open a GLB or glTF instead.`};
    }
    return {kind: "upload"};
}

/** The file picker's `accept`: the files that open in some way here. */
export function localOpenAccept(env: LocalOpenEnv): string {
    const exts = env.rest && env.convertEnabled ? [...new Set([...LOCAL_SCENE_EXTS, ...env.uploadExts])] : LOCAL_SCENE_EXTS;
    return exts.join(",");
}

/** File -> Open (and Ctrl+O): the picker now, from the user's gesture; the chosen files open in
 *  the scene, replacing what is loaded (`openLocalFiles`, loaded once a file is picked). */
export function openLocalFilePicker(): void {
    void pickLocalFiles(localOpenAccept(localOpenEnv())).then(async (files) => {
        if (!files.length) return;
        const {openLocalFiles} = await import("./open_local_file");
        await openLocalFiles(files, {placement: "replace"});
    });
}

/** Open the browser's file picker; resolves to the chosen files (none when cancelled). Call it
 *  from the user's click or key press: the browser shows a picker only then. */
export function pickLocalFiles(accept: string, multiple = true): Promise<File[]> {
    return new Promise((resolve) => {
        if (typeof document === "undefined") {
            resolve([]);
            return;
        }
        const input = document.createElement("input");
        input.type = "file";
        input.accept = accept;
        input.multiple = multiple;
        input.style.display = "none";
        const done = (files: File[]) => {
            input.remove();
            resolve(files);
        };
        input.addEventListener("change", () => done(Array.from(input.files ?? [])), {once: true});
        input.addEventListener("cancel", () => done([]), {once: true});
        document.body.appendChild(input);
        input.click();
    });
}
