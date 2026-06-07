import React from "react";
import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";

import {sceneRef, cameraRef, rendererRef} from "@/state/refs";
import {requestRender} from "@/state/perfStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useModelState, loadedSourceGroups} from "@/state/modelState";
import {viewerApi} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

// Headless controller for the FEM scene panel's "Beams as solid" toggle.
//
// FEM beam (line) elements render as line geometry in the main glb by default. The solid
// (swept-profile) representation ships as a separate beam_solids sidecar glb the worker bakes
// next to the main glb (`<derived>.glb.beam_solids.glb`). When the toggle flips on we lazy-
// fetch + parse that sidecar per loaded model, recenter it into the model's frame, and swap:
// hide the model's line geometry, show the solid beams. Flipping off restores the lines.
//
// Mirrors FemConceptsController: a self-contained container group, recenter-by-translation,
// and subscriptions to the store + model state. Renders nothing.

function sidecarKey(sourceName: string): string {
    // The worker stores the sidecar at "<main glb derived key>.beam_solids.glb"; mirror the
    // derived_key_for(target='glb') convention used by overlay_file_in_scene.
    const glbKey = sourceName.toLowerCase().endsWith(".glb") ? sourceName : `_derived/${sourceName}.glb`;
    return `${glbKey}.beam_solids.glb`;
}

const BeamSolidsController: React.FC = () => {
    React.useEffect(() => {
        let cleanup: (() => void) | null = null;
        let raf = 0;
        const tryInit = () => {
            if (!rendererRef.current || !sceneRef.current || !cameraRef.current) {
                raf = requestAnimationFrame(tryInit); // wait for ThreeCanvas to set refs
                return;
            }
            cleanup = init(sceneRef.current);
        };
        tryInit();
        return () => {
            cancelAnimationFrame(raf);
            cleanup?.();
        };
    }, []);
    return null;
};

function init(scene: THREE.Scene): () => void {
    const container = new THREE.Group();
    container.name = "__beam_solids__";
    container.userData.__excludeFromFit = true; // keep solids out of zoom-to-all
    scene.add(container);

    const loader = new GLTFLoader();
    // sourceName -> loaded sidecar group (null = fetched, none available for this model)
    const loaded = new Map<string, THREE.Group | null>();
    const inflight = new Set<string>(); // guard against a rapid toggle double-loading

    const disposeGroup = (g: THREE.Group) => {
        g.traverse((o: any) => {
            o.geometry?.dispose?.();
            const m = o.material;
            if (m) (Array.isArray(m) ? m : [m]).forEach((x: any) => x.dispose?.());
        });
        container.remove(g);
    };

    // Show/hide a loaded model's line geometry (beam centerlines + element wireframe). A model
    // whose beams are shown as solids should not also draw their centerlines.
    const setModelLinesVisible = (sourceName: string, visible: boolean) => {
        const g = loadedSourceGroups.get(sourceName);
        if (!g) return;
        g.traverse((o: any) => {
            if (o.isLineSegments || o.isLine) o.visible = visible;
        });
    };

    // Sidecar verts are raw model coordinates; the main model is recentered by
    // modelStore.translation, so mirror that offset onto the sidecar group.
    const recenter = (g: THREE.Group) => {
        const t = useModelState.getState().translation;
        if (t) g.position.copy(t);
        else g.position.set(0, 0, 0);
    };

    const ensureLoaded = async (sourceName: string) => {
        if (loaded.has(sourceName) || inflight.has(sourceName)) return;
        inflight.add(sourceName);
        try {
            const scope = scopeUrlPart(useScopeStore.getState().current);
            let buf: ArrayBuffer;
            try {
                buf = await viewerApi.getBlob(scope, sidecarKey(sourceName));
            } catch {
                loaded.set(sourceName, null); // 404 / no swept-able beams for this model
                return;
            }
            const gltf = await new Promise<any>((resolve, reject) =>
                loader.parse(buf, "", resolve, reject),
            );
            const g: THREE.Group = gltf.scene;
            g.name = `__beam_solids__${sourceName}`;
            g.traverse((o: any) => o.layers?.set(1)); // non-pickable overlay
            recenter(g);
            // Honour the live toggle state — the user may have flipped it off mid-fetch.
            g.visible = useFemConceptsStore.getState().showBeamsSolid;
            container.add(g);
            loaded.set(sourceName, g);
            requestRender();
        } finally {
            inflight.delete(sourceName);
        }
    };

    const apply = () => {
        const show = useFemConceptsStore.getState().showBeamsSolid;
        const sources = useModelState.getState().loadedSourceNames;
        for (const src of sources) {
            setModelLinesVisible(src, !show);
            const g = loaded.get(src);
            if (show) {
                if (g === undefined) void ensureLoaded(src);
                else if (g) g.visible = true;
            } else if (g) {
                g.visible = false;
            }
        }
        requestRender();
    };

    // Drop sidecars for models that are no longer loaded.
    const pruneStale = () => {
        const sources = useModelState.getState().loadedSourceNames;
        for (const [src, g] of [...loaded.entries()]) {
            if (!sources.has(src)) {
                if (g) disposeGroup(g);
                loaded.delete(src);
            }
        }
    };

    apply();

    const unsubStore = useFemConceptsStore.subscribe(apply);
    const unsubModel = useModelState.subscribe((s, prev) => {
        if (s.loadedSourceNames !== prev.loadedSourceNames) {
            pruneStale();
            apply();
        } else if (s.translation !== prev.translation) {
            for (const g of loaded.values()) if (g) recenter(g);
            requestRender();
        }
    });

    return () => {
        unsubStore();
        unsubModel();
        for (const g of loaded.values()) if (g) disposeGroup(g);
        loaded.clear();
        scene.remove(container);
        requestRender();
    };
}

export default BeamSolidsController;
