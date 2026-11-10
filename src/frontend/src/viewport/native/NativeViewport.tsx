// The three.js-free viewport, first slice: load, orbit / pan / zoom, pick, select, hide, and a list of
// the model's objects. It is a second viewport implementation, not a mode of the three.js one, and this
// chunk imports no three.js (src/__tests__/viewport/nativeViewportImports.test.ts walks its import graph).
// What the three.js viewport does beyond the slice is listed here as unavailable rather than left to
// look broken.
import React from "react";
import {ensureViewportInput} from "@/viewport/input";
import {NativeScene} from "./nativeScene";
import {useNativeViewport} from "./nativeModel";

const UNAVAILABLE = [
    "section planes", "FEA results and animation", "labels, markers and plugin overlays", "measurements and gizmos",
    "screenshots and export", "overlaying several models", "the tree, info and scene panels of the main viewer",
];

const NativeViewport: React.FC = () => {
    const hostRef = React.useRef<HTMLDivElement>(null);
    const sceneRef = React.useRef<NativeScene | null>(null);
    const [, setVersion] = React.useState(0);
    const [loadError, setLoadError] = React.useState("");
    const model = useNativeViewport((s) => s.model);
    const state = useNativeViewport((s) => s.state);

    React.useEffect(() => {
        const host = hostRef.current;
        if (!host) return;
        let cancelled = false;
        const fail = (reason: string) => useNativeViewport.setState({state: "failed", error: reason});
        useNativeViewport.setState({state: "starting", error: null});
        NativeScene.create(host, ensureViewportInput(host)).then((scene) => {
            if (cancelled) return scene.dispose();
            scene.onChange = () => setVersion((v) => v + 1);
            scene.onFailure = fail;
            sceneRef.current = scene;
            useNativeViewport.setState({state: "active"});
        }, (err: unknown) => {
            if (!cancelled) fail(err instanceof Error ? err.message : String(err));
        });
        return () => {
            cancelled = true;
            sceneRef.current?.dispose();
            sceneRef.current = null;
            if (useNativeViewport.getState().state !== "failed") useNativeViewport.setState({state: "off"});
        };
    }, []);

    React.useEffect(() => {
        const scene = sceneRef.current;
        if (!scene || !model || state !== "active") return;
        try {
            scene.load(model.bytes);
            setLoadError("");
        } catch (err) {
            setLoadError(err instanceof Error ? err.message : String(err));
        }
    }, [model, state]);

    const scene = sceneRef.current;
    const objects = scene?.objects ?? [];
    return (
        <div ref={hostRef} className="w-full h-full relative" data-native-viewport-host="true">
            <div className="absolute left-2 top-14 z-10 w-72 max-h-[70%] flex flex-col rounded-sm bg-gray-800/90 text-xs text-gray-200 p-2 space-y-1">
                <div className="font-semibold">adacpp WebGPU, no three.js (experimental)</div>
                {state === "starting" && <div>Starting WebGPU…</div>}
                {loadError && <div className="text-amber-300">{loadError}</div>}
                {model && <div className="text-gray-400 truncate" title={model.sourceName}>{model.sourceName || "model"} · {objects.length} objects</div>}
                {!model && state === "active" && <div className="text-gray-400">No model loaded.</div>}
                <div className="flex space-x-1">
                    <button className="bg-blue-700 hover:bg-blue-600 rounded-sm px-2 py-1 disabled:opacity-40" disabled={!scene?.selectionSize} onClick={() => scene?.hideSelected()}>Hide selected</button>
                    <button className="bg-blue-700 hover:bg-blue-600 rounded-sm px-2 py-1" onClick={() => scene?.showAll()}>Show all</button>
                    <button className="bg-blue-700 hover:bg-blue-600 rounded-sm px-2 py-1" onClick={() => scene?.fit()}>Fit</button>
                </div>
                <div className="overflow-auto min-h-0" role="tree" aria-label="Objects">
                    {objects.map((o) => (
                        <div key={o.id} role="treeitem" aria-selected={scene?.isSelected(o.id)}
                            className={`flex items-center justify-between px-1 cursor-pointer ${scene?.isSelected(o.id) ? "bg-blue-700" : "hover:bg-gray-700"} ${scene?.isHidden(o.id) ? "opacity-50" : ""}`}
                            title={o.path.join(" / ")}
                            onClick={(e) => scene?.select(o.id, e.ctrlKey || e.metaKey || e.shiftKey)}>
                            <span className="truncate">{o.path[o.path.length - 1]}</span>
                            <button className="ml-1 text-gray-300 hover:text-white" aria-label={scene?.isHidden(o.id) ? "Show" : "Hide"}
                                onClick={(e) => { e.stopPropagation(); scene?.setHidden(o.id, !scene.isHidden(o.id)); }}>
                                {scene?.isHidden(o.id) ? "show" : "hide"}
                            </button>
                        </div>
                    ))}
                </div>
                <details>
                    <summary className="cursor-pointer text-gray-400">Not available in this mode</summary>
                    <ul className="list-disc pl-4 text-gray-400">{UNAVAILABLE.map((u) => <li key={u}>{u}</li>)}</ul>
                </details>
            </div>
        </div>
    );
};

export default NativeViewport;
