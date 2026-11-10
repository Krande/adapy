// The three.js-free viewport, first slice: load, orbit / pan / zoom, pick, select, hide, and a list of
// the model's objects. It is a second viewport implementation, not a mode of the three.js one, and this
// chunk imports no three.js (src/__tests__/viewport/nativeViewportImports.test.ts walks its import graph).
// What the three.js viewport does beyond the slice is listed here as unavailable rather than left to
// look broken.
import React from "react";
import {ensureViewportInput} from "@/viewport/input";
import {installShortcutDispatcher} from "@/layout/shortcutDispatch";
import {bindCoreShortcuts} from "@/layout/shortcuts";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useSectionStore} from "@/state/sectionStore";
import {tickFeaAnimation} from "@/utils/scene/fea/feaAnimationDriver";
import {notifyModelLoaded, notifyViewportPick, setActiveViewport} from "@/viewport/api/viewportApi";
import {viewportShortcutHandlers} from "@/viewport/api/viewportShortcuts";
import {NativeScene} from "./nativeScene";
import {createNativeViewportApi} from "./nativeViewportApi";
import {useNativeViewport} from "./nativeModel";

const UNAVAILABLE = [
    "section plane gizmo and filled cuts", "FEA element (integration point) fields, beam sections and picking in a result",
    "labels, markers and plugin overlays", "measurements and gizmos",
    "screenshots and export", "overlaying several models",
];

const NativeViewport: React.FC = () => {
    const hostRef = React.useRef<HTMLDivElement>(null);
    const sceneRef = React.useRef<NativeScene | null>(null);
    const [, setVersion] = React.useState(0);
    const [loadError, setLoadError] = React.useState("");
    const model = useNativeViewport((s) => s.model);
    const state = useNativeViewport((s) => s.state);
    // A result shown through result layers is not a model handed over as bytes: it is named here.
    const result = useFeaAnimationStore((s) => (s.resultLayer ? s.sourceName : null));

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
            // A playing result: the same driver the three.js viewport ticks, once per frame, for as
            // long as it plays. It writes the deformation scale to the result's layer.
            scene.onFrame = (dt) => {
                tickFeaAnimation(dt);
                const fea = useFeaAnimationStore.getState();
                return fea.isPlaying && fea.sessionActive;
            };
            scene.onPick = (id) => notifyViewportPick({ref: id ? {model: scene.modelKey, id} : null});
            sceneRef.current = scene;
            if (scene.failure) return fail(scene.failure);
            const withdraw = setActiveViewport(createNativeViewportApi(scene));
            // The viewport's keys (hide, show all, fit, named views, orbit and pan by key), while it is up.
            const unbind = bindCoreShortcuts(viewportShortcutHandlers((run) => ({run})));
            const releaseDispatcher = installShortcutDispatcher();
            // Section planes: the same store the three.js viewport cuts by.
            const cut = () => scene.setSectionPlanes(useSectionStore.getState().planes.filter((p) => p.enabled));
            cut();
            const uncut = useSectionStore.subscribe(cut);
            release = () => {
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

    // A result starts playing: the frame loop, which runs only while something changes, wakes up.
    React.useEffect(
        () =>
            useFeaAnimationStore.subscribe((s, p) => {
                if (s.isPlaying && !p.isPlaying) sceneRef.current?.invalidate();
            }),
        [],
    );

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
            <div className="absolute left-2 top-14 z-10 w-72 max-h-[70%] flex flex-col rounded-sm bg-gray-800/90 text-xs text-gray-200 p-2 space-y-1">
                <div className="font-semibold">adacpp WebGPU, no three.js (experimental)</div>
                {state === "starting" && <div>Starting WebGPU…</div>}
                {loadError && <div className="text-amber-300">{loadError}</div>}
                {model && <div className="text-gray-400 truncate" title={model.sourceName}>{model.sourceName || "model"} · {objects.length} objects</div>}
                {!model && result && <div className="text-gray-400 truncate" title={result}>{result} · result</div>}
                {!model && !result && state === "active" && <div className="text-gray-400">No model loaded.</div>}
                <details>
                    <summary className="cursor-pointer text-gray-400">Not available in this mode</summary>
                    <ul className="list-disc pl-4 text-gray-400">{UNAVAILABLE.map((u) => <li key={u}>{u}</li>)}</ul>
                </details>
            </div>
        </div>
    );
};

export default NativeViewport;
