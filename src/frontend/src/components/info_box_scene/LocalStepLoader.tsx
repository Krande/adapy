import React from "react";

import {setupModelLoaderAsync} from "@/components/viewer/sceneHelpers/setupModelLoader";
import {useModelState} from "@/state/modelState";
import {convertStepToGlb} from "@/utils/stepConverter/stepConverter";

// Pick a local STEP file and convert it to GLB entirely in the browser — the OCC-free adacpp wasm
// module (no pyodide, no server upload), OPFS-backed so large files stream through pread instead of
// the wasm heap — then load the result straight into the scene.
const LocalStepLoader = () => {
    const inputRef = React.useRef<HTMLInputElement>(null);
    const [busy, setBusy] = React.useState(false);
    const [status, setStatus] = React.useState<string | null>(null);

    const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        e.target.value = ""; // allow re-selecting the same file
        if (!file) return;
        setBusy(true);
        setStatus(`Converting ${file.name}…`);
        let url: string | null = null;
        try {
            const bytes = await file.arrayBuffer();
            const res = await convertStepToGlb(bytes);
            setStatus(`Loading ${res.tris.toLocaleString()} tris (${res.backend}, ${(res.ms / 1000).toFixed(1)}s)…`);
            const blob = new Blob([res.glb], {type: "model/gltf-binary"});
            url = URL.createObjectURL(blob);
            const sourceName = `local:${file.name}`;
            const group = await setupModelLoaderAsync(url, true, undefined, sourceName);
            useModelState.getState().registerLoadedSource(sourceName, group);
            setStatus(`Loaded ${file.name} — ${res.tris.toLocaleString()} tris (${res.backend}).`);
        } catch (err) {
            setStatus(`Failed: ${err instanceof Error ? err.message : String(err)}`);
        } finally {
            if (url) URL.revokeObjectURL(url);
            setBusy(false);
        }
    };

    return (
        <div className="space-y-1 p-1">
            <input
                ref={inputRef}
                type="file"
                accept=".step,.stp,.STEP,.STP"
                className="hidden"
                onChange={onFile}
            />
            <button
                className="w-full text-sm rounded-sm px-2 py-1 bg-gray-700 text-gray-100 border border-gray-600 hover:bg-gray-600 disabled:opacity-50"
                disabled={busy}
                onClick={() => inputRef.current?.click()}
            >
                {busy ? "Converting…" : "Choose STEP file…"}
            </button>
            {status && <div className="text-xs text-gray-300 break-words">{status}</div>}
            <div className="text-[10px] text-gray-500">
                Tessellates locally via WebAssembly (no upload). Large files stream through OPFS.
            </div>
        </div>
    );
};

export default LocalStepLoader;
