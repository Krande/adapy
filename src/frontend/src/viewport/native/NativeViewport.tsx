// The three.js-free viewport, first slice: load, orbit / pan / zoom, pick, select, hide, and a list of
// the model's objects. It is a second viewport implementation, not a mode of the three.js one, and this
// chunk imports no three.js (src/__tests__/viewport/nativeViewportImports.test.ts walks its import graph).
// What the three.js viewport does beyond the slice is listed here as unavailable rather than left to
// look broken.
import React from "react";
import {ensureViewportInput} from "@/viewport/input";
import {installShortcutDispatcher} from "@/layout/shortcutDispatch";
import {bindCoreShortcuts} from "@/layout/shortcuts";
import {useSectionStore} from "@/state/sectionStore";
import {notifyModelLoaded, notifyViewportPick, setActiveViewport} from "@/viewport/api/viewportApi";
import {viewportShortcutHandlers} from "@/viewport/api/viewportShortcuts";
import {NativeOrientationGizmo} from "./NativeOrientationGizmo";
import {NativeScene} from "./nativeScene";
import {bindSectionGizmo} from "./sectionGizmo";
import {createNativeViewportApi} from "./nativeViewportApi";
import {useNativeViewport} from "./nativeModel";

/** "#rrggbb" (sRGB) as linear rgb, which the renderer takes; null when it is not that. */
function linearRgb(hex: string): [number, number, number] | null {
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!m) return null;
    const linear = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return [linear(parseInt(m[1], 16) / 255), linear(parseInt(m[2], 16) / 255), linear(parseInt(m[3], 16) / 255)];
}

const UNAVAILABLE = [
    "FEA results and animation", "number labels and plugin overlays", "box selection", "3D-mouse input",
    "export", "overlaying several models",
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
        let release: (() => void) | null = null;
        const fail = (reason: string) => useNativeViewport.setState({state: "failed", error: reason});
        useNativeViewport.setState({state: "starting", error: null});
        NativeScene.create(host, ensureViewportInput(host)).then((scene) => {
            if (cancelled) return scene.dispose();
            scene.onChange = () => setVersion((v) => v + 1);
            scene.onFailure = fail;
            scene.onPick = (id) => notifyViewportPick({ref: id ? {model: scene.modelKey, id} : null});
            sceneRef.current = scene;
            if (scene.failure) return fail(scene.failure);
            const withdraw = setActiveViewport(createNativeViewportApi(scene));
            // The viewport's keys (hide, show all, fit, named views, orbit and pan by key), while it is up.
            const unbind = bindCoreShortcuts(viewportShortcutHandlers((run) => ({run})));
            const releaseDispatcher = installShortcutDispatcher();
            // Section planes: the same store the three.js viewport cuts by.
            const cut = () => {
                const {planes, capColor} = useSectionStore.getState();
                scene.setSectionPlanes(planes.filter((p) => p.enabled), linearRgb(capColor));
            };
            cut();
            const uncut = useSectionStore.subscribe(cut);
            const unbindGizmo = bindSectionGizmo(scene);
            release = () => {
                unbindGizmo();
                uncut();
                releaseDispatcher();
                unbind();
                withdraw();
            };
            useNativeViewport.setState({state: "active"});
        }, (err: unknown) => {
            if (!cancelled) fail(err instanceof Error ? err.message : String(err));
        });
        return () => {
            cancelled = true;
            release?.();
            sceneRef.current?.dispose();
            sceneRef.current = null;
            if (useNativeViewport.getState().state !== "failed") useNativeViewport.setState({state: "off"});
        };
    }, []);

    React.useEffect(() => {
        const scene = sceneRef.current;
        if (!scene || !model || state !== "active") return;
        try {
            const key = `native-${model.seq}`;
            scene.load(model.bytes, key);
            setLoadError("");
            // The Scene tree and the object info list and name this model from here on.
            notifyModelLoaded({model: key, sourceName: model.sourceName, sceneExtras: scene.sceneExtras});
        } catch (err) {
            setLoadError(err instanceof Error ? err.message : String(err));
        }
    }, [model, state]);

    const scene = sceneRef.current;
    const objects = scene?.objects ?? [];
    return (
        <div ref={hostRef} className="w-full h-full relative" data-native-viewport-host="true">
            {scene && state === "active" && <NativeOrientationGizmo scene={scene}/>}
            <div className="absolute left-2 top-14 z-10 w-72 max-h-[70%] flex flex-col rounded-sm bg-gray-800/90 text-xs text-gray-200 p-2 space-y-1">
                <div className="font-semibold">adacpp WebGPU, no three.js (experimental)</div>
                {state === "starting" && <div>Starting WebGPU…</div>}
                {loadError && <div className="text-amber-300">{loadError}</div>}
                {model && <div className="text-gray-400 truncate" title={model.sourceName}>{model.sourceName || "model"} · {objects.length} objects</div>}
                {!model && state === "active" && <div className="text-gray-400">No model loaded.</div>}
                <details>
                    <summary className="cursor-pointer text-gray-400">Not available in this mode</summary>
                    <ul className="list-disc pl-4 text-gray-400">{UNAVAILABLE.map((u) => <li key={u}>{u}</li>)}</ul>
                </details>
            </div>
        </div>
    );
};

export default NativeViewport;
