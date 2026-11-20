import React from "react";

import {useViewerRefs} from "@/state/AdaViewerContext";
import {followActiveViewport} from "@/viewport/api/viewportApi";

import {initCellBuilderScene} from "./cellbuilder/initScene";
import {nativeViewHost} from "./cellbuilder/nativeViewHost";
import {threeViewHost} from "./cellbuilder/viewHost";

// Headless controller for the procedural cellbuilder: reconciles the
// cellBuilderStore with tool-local three.js box meshes (blue = cell,
// orange = equipment), per-face hover highlight, click selection
// (cell -> face; border clicks select an edge), magnetic ghost placement in
// the add modes and grid-quantized face dragging. The container tracks the
// viewer's model translation so builder boxes align exactly with loaded
// GLBs (incl. the compiled result). Renders nothing.
// The scene work itself lives in ./cellbuilder — this component only waits for
// the viewport to exist, stands the builder up once for it, and tears it down on
// unmount. On the three.js viewport the builder's objects are in its scene; on the
// three.js-free one (`native`) they are drawn through that viewport's interface
// (cellbuilder/viewHost.ts says what differs).

const CellBuilderController: React.FC<{native?: boolean}> = ({native = false}) => {
    // This viewer instance's handles. The imperative modules have no tree to
    // read a context from and go through `getViewerRuntime()` instead.
    const {scene: sceneRef, camera: cameraRef, renderer: rendererRef} = useViewerRefs();
    React.useEffect(() => {
        if (native) {
            // Stood up for the three.js-free viewport once it is on screen, and again for each one
            // that replaces it.
            return followActiveViewport((viewport) => (viewport.kind === "native" ? initCellBuilderScene(nativeViewHost(viewport)) : () => {}));
        }
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
            cleanup = initCellBuilderScene(threeViewHost(renderer, scene, camera));
        };
        tryInit();

        return () => {
            cancelAnimationFrame(raf);
            cleanup?.();
        };
    }, [native]);

    return null;
};

export default CellBuilderController;
