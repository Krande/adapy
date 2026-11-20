// The three.js-free viewport, first slice: load, orbit / pan / zoom, pick, select, hide, and a list of
// the model's objects. It is a second viewport implementation, not a mode of the three.js one, and this
// chunk imports no three.js (src/__tests__/viewport/nativeViewportImports.test.ts walks its import graph).
import React from "react";
import {ensureViewportInput} from "@/viewport/input";
import {installShortcutDispatcher} from "@/layout/shortcutDispatch";
import {bindCoreShortcuts} from "@/layout/shortcuts";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {playbackClock} from "@/state/playbackClock";
import {bindModelAnimationTrack} from "@/state/modelAnimationTrack";
import {useSectionStore} from "@/state/sectionStore";
import {applyFeaDeformationGate} from "@/utils/scene/fea/deformationGate";
import {tickFeaAnimation} from "@/utils/scene/fea/feaAnimationDriver";
import {notifyModelLoaded, notifyViewportPick, setActiveViewport} from "@/viewport/api/viewportApi";
import {runBeforeDraw} from "@/viewport/api/beforeDraw";
import {viewportShortcutHandlers} from "@/viewport/api/viewportShortcuts";
import {NativeOrientationGizmo} from "./NativeOrientationGizmo";
import {NativeStats} from "./NativeStats";
import {NativeScene, objectKey, splitObjectKey} from "./nativeScene";
import {objectAt} from "@/viewport/api/objectAt";
import {bindSectionGizmo} from "./sectionGizmo";
import {bindBoxSelectionGizmo} from "./boxSelectionGizmo";
import {createNativeViewportApi} from "./nativeViewportApi";
import {bindNativeHistory} from "./nativeViewportHistory";
import {useNativeViewport} from "./nativeModel";
import {glbBufferViews} from "@/utils/glb/glbBufferView";
import {takeConceptClick} from "@/utils/fem/conceptPicking";
import {takeClashClick} from "@/utils/scene/clashOverlayPicks";
import {linearHexText} from "@/utils/scene/sceneColor";
import {describeScenePick, modelPoint, offerScenePick, scenePicksHeld} from "@/plugins/scenePicks";
import {offerSceneClick, sceneClicksHeld} from "@/viewport/api/sceneClicks";

const NativeViewport: React.FC = () => {
    const hostRef = React.useRef<HTMLDivElement>(null);
    const sceneRef = React.useRef<NativeScene | null>(null);
    const [, setVersion] = React.useState(0);
    const model = useNativeViewport((s) => s.model);
    const state = useNativeViewport((s) => s.state);

    React.useEffect(() => {
        const host = hostRef.current;
        if (!host) return;
        let cancelled = false;
        let release: (() => void) | null = null;
        const fail = (reason: string) => useNativeViewport.setState({state: "failed", error: reason});
        useNativeViewport.setState({state: "starting", error: null, loadError: null, objectCount: 0});
        NativeScene.create(host, ensureViewportInput(host)).then((scene) => {
            if (cancelled) return scene.dispose();
            scene.onChange = () => {
                setVersion((v) => v + 1);
                // Performance > Renderer shows the count; it changes only when the objects do.
                if (useNativeViewport.getState().objectCount !== scene.objects.length) {
                    useNativeViewport.setState({objectCount: scene.objects.length});
                }
            };
            scene.onFailure = fail;
            // A playing result: the same driver the three.js viewport ticks, once per frame, for as
            // long as it plays. It writes the deformation scale to the result's layer.
            scene.beforeFrame = (dt) => {
                tickFeaAnimation(dt);
                // After the tick: a plugin holding the model undeformed has the last word.
                applyFeaDeformationGate();
                // What is pinned to the result (a label on an element, an outline) moves with it.
                runBeforeDraw();
                // The playback clock shows this frame's moment; it keeps the loop awake only while it plays.
                const clockPlaying = playbackClock.tick(performance.now());
                const fea = useFeaAnimationStore.getState();
                return clockPlaying || (fea.isPlaying && fea.sessionActive);
            };
            scene.onPick = (key, world) =>
                notifyViewportPick({
                    ref: key ? splitObjectKey(key) : null,
                    // In the model's own coordinates, as the other viewport reports its clicks.
                    point: world ? modelPoint(world, scene.sceneOffset) : null,
                });
            sceneRef.current = scene;
            if (scene.failure) return fail(scene.failure);
            const api = createNativeViewportApi(scene);
            const withdraw = setActiveViewport(api);
            // A glyph of an analysis concept (a mass, a support, a load) is asked before the model.
            scene.clickFirst = async (clientX, clientY, additive) => {
                // A core tool holding the scene's clicks (the Measure tab) has them first, wherever they
                // land: such a click picks and selects nothing. Counted from the view the pick reads.
                if (sceneClicksHeld()) {
                    const rect = scene.viewRect();
                    if (offerSceneClick({x: clientX - rect.left, y: clientY - rect.top, additive})) return true;
                }
                const view = host.getBoundingClientRect();
                // A clash marker or contact patch (the Clashes tab's overlay) is drawn on the geometry
                // it describes: asked before the model, as on the three.js viewport.
                if (takeClashClick(api, clientX - view.left, clientY - view.top)) return true;
                if (await takeConceptClick(api, clientX - view.left, clientY - view.top, additive, async () => (await scene.pickHit(clientX, clientY))?.world ?? null)) return true;
                // Then a plugin holding the scene's clicks (`ctx.scene.pickObjects`): the object
                // under the cursor is handed to it, and a click it takes selects nothing.
                if (!scenePicksHeld()) return false;
                const hit = await scene.pickHit(clientX, clientY);
                const key = scene.objectOver?.(clientX, clientY, hit?.world ?? null) ?? hit?.id ?? null;
                if (!key) return false;
                const ref = splitObjectKey(key);
                return offerScenePick({
                    model: ref.model,
                    id: ref.id,
                    // Its name and file come from the trees, which this chunk does not import.
                    ...describeScenePick(ref.model, ref.id),
                    // Where on it, in the model's own coordinates: this viewport places its models
                    // at world = model + offset. An object found only as a drawn line has no surface hit.
                    point: hit ? modelPoint(hit.world, scene.sceneOffset) : null,
                    additive,
                });
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
                scene.setSectionPlanes(planes.filter((p) => p.enabled), linearHexText(capColor));
            };
            cut();
            const uncut = useSectionStore.subscribe(cut);
            const unbindGizmo = bindSectionGizmo(scene);
            // Selection boxes: the same store the three.js viewport's controller draws.
            const unbindBoxes = bindBoxSelectionGizmo(scene);
            // Undo and Redo: their keys, and a history that never outlives the scene it names.
            const unbindHistory = bindNativeHistory(scene);
            // The clock starts playing: the frame loop, which runs only while something changes, wakes up.
            const unwake = playbackClock.onWake(() => scene.invalidate());
            bindModelAnimationTrack();
            release = () => {
                unwake();
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
            scene.load(model.bytes, key, {add: added, sourceName: model.sourceName, posable: model.posable, keepCamera: model.keepCamera});
            useNativeViewport.setState({loadError: null, objectCount: scene.objects.length});
            // The Scene tree and the object info list and name this model from here on.
            // The scene read the file's JSON to find the objects without a colour: its extras are
            // handed on parsed, so the panels' side does not parse them a second time.
            const extras = scene.parsedExtras;
            notifyModelLoaded({
                added, model: key, sourceName: model.sourceName, displayName: model.displayName,
                sceneExtras: extras ? "" : scene.sceneExtras, ...(extras ? {extras} : {}),
                adaExtData: scene.adaExtData, bufferView: glbBufferViews(model.bytes),
            });
        } catch (err) {
            useNativeViewport.setState({loadError: err instanceof Error ? err.message : String(err)});
        }
    }, [model, state]);

    // Its state, model and load errors are shown under Performance > Renderer, not over the panels.
    const scene = sceneRef.current;
    return (
        <div ref={hostRef} className="w-full h-full relative" data-native-viewport-host="true">
            {scene && state === "active" && <NativeOrientationGizmo scene={scene}/>}
            {scene && state === "active" && <NativeStats scene={scene}/>}
        </div>
    );
};

export default NativeViewport;
