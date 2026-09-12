import React from "react";

import {useViewerRefs} from "@/state/AdaViewerContext";

import {initCellBuilderScene} from "./cellbuilder/initScene";

// Headless controller for the procedural cellbuilder: reconciles the
// cellBuilderStore with tool-local three.js box meshes (blue = cell,
// orange = equipment), per-face hover highlight, click selection
// (cell -> face; border clicks select an edge), magnetic ghost placement in
// the add modes and grid-quantized face dragging. The container tracks the
// viewer's model translation so builder boxes align exactly with loaded
// GLBs (incl. the compiled result). Renders nothing.
// The scene work itself lives in ./cellbuilder — this component only waits for
// the viewer's renderer/scene/camera to exist, stands the builder up once, and
// tears it down on unmount.

const CellBuilderController: React.FC = () => {
    // This viewer instance's handles. The imperative modules have no tree to
    // read a context from and go through `getViewerRuntime()` instead.
    const {scene: sceneRef, camera: cameraRef, renderer: rendererRef} = useViewerRefs();
    React.useEffect(() => {
        let cleanup: (() => void) | null = null;
        let raf = 0;

        const tryInit = () => {
            const renderer = rendererRef.current;
            const scene = sceneRef.current;
            const camera = cameraRef.current;
            if (!renderer || !scene || !camera) {
                raf = requestAnimationFrame(tryInit);
                return;
            }
            cleanup = initCellBuilderScene(renderer, scene, camera);
        };
        tryInit();

        return () => {
            cancelAnimationFrame(raf);
            cleanup?.();
        };
    }, []);

    return null;
};

export default CellBuilderController;
