// The three.js-free viewport, first slice: load, orbit / pan / zoom, pick, select, hide, and a list of
// the model's objects. It is a second viewport implementation, not a mode of the three.js one, and this
// chunk imports no three.js (src/__tests__/viewport/nativeViewportImports.test.ts walks its import graph).
import React from "react";
import {ensureViewportInput} from "@/viewport/input";
import {installShortcutDispatcher} from "@/layout/shortcutDispatch";
import {bindCoreShortcuts} from "@/layout/shortcuts";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useSectionStore} from "@/state/sectionStore";
import {applyFeaDeformationGate} from "@/utils/scene/fea/deformationGate";
import {tickFeaAnimation} from "@/utils/scene/fea/feaAnimationDriver";
import {notifyModelLoaded, notifyViewportPick, setActiveViewport} from "@/viewport/api/viewportApi";
import {runBeforeDraw} from "@/viewport/api/beforeDraw";
import {viewportShortcutHandlers} from "@/viewport/api/viewportShortcuts";
import {NativeOrientationGizmo} from "./NativeOrientationGizmo";
import {NativeScene, objectKey, splitObjectKey} from "./nativeScene";
import {objectAt} from "@/viewport/api/objectAt";
import {bindSectionGizmo} from "./sectionGizmo";
import {bindBoxSelectionGizmo} from "./boxSelectionGizmo";
import {createNativeViewportApi} from "./nativeViewportApi";
import {bindNativeHistory} from "./nativeViewportHistory";
import {useNativeViewport} from "./nativeModel";
import {glbBufferViews} from "@/utils/glb/glbBufferView";
import {takeConceptClick} from "@/utils/fem/conceptPicking";

/** "#rrggbb" (sRGB) as linear rgb, which the renderer takes; null when it is not that. */
function linearRgb(hex: string): [number, number, number] | null {
    const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
    if (!m) return null;
    const linear = (c: number) => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
    return [linear(parseInt(m[1], 16) / 255), linear(parseInt(m[2], 16) / 255), linear(parseInt(m[3], 16) / 255)];
}

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
            scene.beforeFrame = (dt) => {
                tickFeaAnimation(dt);
                // After the tick: a plugin holding the model undeformed has the last word.
                applyFeaDeformationGate();
                // What is pinned to the result (a label on an element, an outline) moves with it.
                runBeforeDraw();
                const fea = useFeaAnimationStore.getState();
                return fea.isPlaying && fea.sessionActive;
            };
            scene.onPick = (key) => notifyViewportPick({ref: key ? splitObjectKey(key) : null});
            sceneRef.current = scene;
            if (scene.failure) return fail(scene.failure);
            const api = createNativeViewportApi(scene);
            const withdraw = setActiveViewport(api);
            // A glyph of an analysis concept (a mass, a support, a load) is asked before the model.
            scene.clickFirst = (clientX, clientY, additive) => {
                const view = host.getBoundingClientRect();
                return takeConceptClick(api, clientX - view.left, clientY - view.top, additive, async () => (await scene.pickHit(clientX, clientY))?.world ?? null);
            };
            // The viewport's keys (hide, show all, fit, named views, orbit and pan by key), while it is up.
            const unbind = bindCoreShortcuts(viewportShortcutHandlers((run) => ({run})));
            const releaseDispatcher = installShortcutDispatcher();
            scene.objectOver = (clientX, clientY, surface) => {
                const view = host.getBoundingClientRect();
                const ref = objectAt(api, clientX - view.left, clientY - view.top, surface);
                return ref ? objectKey(ref.model, ref.id) : null;
            };
            // Section planes: the same store the three.js viewport cuts by.
            const cut = () => {
                const {planes, capColor} = useSectionStore.getState();
                scene.setSectionPlanes(planes.filter((p) => p.enabled), linearRgb(capColor));
            };
            cut();
            const uncut = useSectionStore.subscribe(cut);
            const unbindGizmo = bindSectionGizmo(scene);
            // Selection boxes: the same store the three.js viewport's controller draws.
            const unbindBoxes = bindBoxSelectionGizmo(scene);
            // Undo and Redo: their keys, and a history that never outlives the scene it names.
            const unbindHistory = bindNativeHistory(scene);
            release = () => {
                unbindHistory();
                unbindBoxes();
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
            const added = model.placement === "overlay" && scene.objects.length > 0;
            scene.load(model.bytes, key, {add: added, sourceName: model.sourceName});
            setLoadError("");
            // The Scene tree and the object info list and name this model from here on.
            notifyModelLoaded({
                added, model: key, sourceName: model.sourceName, sceneExtras: scene.sceneExtras,
                adaExtData: scene.adaExtData, bufferView: glbBufferViews(model.bytes),
            });
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
                {!model && result && <div className="text-gray-400 truncate" title={result}>{result} · result</div>}
                {!model && !result && state === "active" && <div className="text-gray-400">No model loaded.</div>}
            </div>
        </div>
    );
};

export default NativeViewport;
