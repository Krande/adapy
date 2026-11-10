// CanvasComponent.tsx
import React from "react";
import ColorLegend from "./ColorLegend";
import ThreeCanvas from "./ThreeCanvas";
import SectionPlanesController from "./SectionPlanesController";
import FemConceptsController from "./FemConceptsController";
import CellBuilderController from "./CellBuilderController";
import TypeIconController from "./TypeIconController";
import ProceduralFollowerController from "./ProceduralFollowerController";
import GalleryControls from "./GalleryControls";
import BBoxSelectionController from "./BBoxSelectionController";
import SpaceMouseConnectChip from "./SpaceMouseConnectChip";
import RenderRecordingHud from "./RenderRecordingHud";
import {useAdacppRenderStatus} from "@/viewport/adacpp/adacppViewport";
import {usePerfStore} from "@/state/perfStore";
import {useViewerRefs} from "@/state/AdaViewerContext";
import {useNativeViewport} from "@/viewport/native/nativeModel";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {installViewportPanelBridge} from "./sceneHelpers/viewportPanelBridge";

// Its own chunk, which imports no three.js.
const NativeViewport = React.lazy(() => import("@/viewport/native/NativeViewport"));
// The panels listen for what a viewport without a three.js scene announces (its model, its clicks).
installViewportPanelBridge();

/** The native viewport failed after a model was handed to it: once the three.js viewport is up, load
 *  that model there, so the fallback shows the model and not an empty scene. A result it was showing
 *  is opened again the same way, from its source. */
function useNativeFallbackReload(nativeFailed: boolean): void {
  const {scene} = useViewerRefs();
  React.useEffect(() => {
    if (!nativeFailed) return;
    const model = useNativeViewport.getState().model;
    const fea = useFeaAnimationStore.getState();
    const result = !model && fea.resultLayer ? fea.sourceName : null;
    if (!model && !result) return;
    let cancelled = false;
    const tryLoad = () => {
      if (cancelled) return;
      if (!scene.current) return void setTimeout(tryLoad, 100);
      if (result) {
        void import("@/utils/scene/handlers/load_fea_streaming").then((m) => {
          // The session that drew through the failed viewport's layers ends; the load starts over.
          m.clearActiveFeaStreaming();
          return m.load_fea_with_defaults(result);
        }).catch((err) => console.error("[adacpp-native] opening the result in the three.js viewport failed:", err));
        return;
      }
      if (!model) return;
      void import("./sceneHelpers/loadModel").then(({loadModel}) =>
        loadModel({sourceName: model.sourceName, bytes: {from: "bytes", bytes: model.bytes}}),
      ).catch((err) => console.error("[adacpp-native] loading the model into the three.js viewport failed:", err));
    };
    tryLoad();
    return () => {
      cancelled = true;
    };
  }, [nativeFailed, scene]);
}

export interface CanvasWrapperProps {
  /**
   * Render the built-in colour legend.
   *
   * A host that places the legend itself passes `false`. Without it both mount
   * one, and since they read the same store you get two identical legends
   * overlapping in the corner -- which is what the docked UI shell did: it
   * mounts this wrapper for the canvas AND anchors its own legend in the
   * viewport overlay. Defaults to `true`, so the stock UI is unchanged.
   */
  legend?: boolean;
}

const CanvasWrapper: React.FC<CanvasWrapperProps> = ({legend = true}) => {
  // WebGPU-only mode fell back to WebGL: remount the canvas and the controllers that hold its renderer.
  const webglFallback = useAdacppRenderStatus((s) => s.webglFallback);
  // The three.js-free viewport instead of everything below, while it is chosen and has not failed.
  const renderBackend = usePerfStore((s) => s.renderBackend);
  const nativeFailed = useNativeViewport((s) => s.state === "failed");
  useNativeFallbackReload(nativeFailed);
  if (renderBackend === "adacpp-native" && !nativeFailed) {
    return (
      <div className="relative w-full h-full">
        {legend && (
          // The colour legend of a shown result, where the three.js viewport has it.
          <div className="absolute right-2 top-2 z-10 sm:right-5 sm:top-80">
            <ColorLegend />
          </div>
        )}
        <React.Suspense fallback={null}>
          <NativeViewport />
        </React.Suspense>
      </div>
    );
  }
  return (
    <div key={webglFallback ? "webgl-fallback" : "viewport"} className="relative w-full h-full">
      {legend && (
        // Phones: top-right corner, clear of the left-hand control column and the
        // bottom-right orientation gizmo. Desktop keeps its old place.
        <div className="absolute right-2 top-2 z-10 sm:right-5 sm:top-80">
          <ColorLegend />
        </div>
      )}

      <div id="canvasParent" className="absolute w-full h-full">
        <ThreeCanvas />
      </div>
      {/* Gallery HUD (opt-in via Theme options): prev/next over the scope's files. */}
      <GalleryControls />
      {/* With the 3D mouse option on and nothing connected: the click the browser's chooser needs. */}
      <SpaceMouseConnectChip />
      {/* Admin render profiling: recording badge and live frame stats, top right of the screen. */}
      <RenderRecordingHud />
      {/* Headless: reconciles section-plane clipping/caps/gizmo with the scene. */}
      <SectionPlanesController />
      {/* Headless: selection boxes -- their gizmo, face handles and the selection they make. */}
      <BBoxSelectionController />
      {/* Headless: draws the FEM-concept glyph overlay (masses / BCs / loads). */}
      <FemConceptsController />
      {/* Headless: procedural cellbuilder box meshes + snapping/face-drag. */}
      <CellBuilderController />
      {/* Headless: Factorio-style type-icon overlay (equipment / media / missing inputs). */}
      <TypeIconController />
      {/* Cross-tab: when opened with ?pfollow=<modelId>, live-loads that model's
          compiled results as the editing tab produces them. Inert otherwise. */}
      <ProceduralFollowerController />
    </div>
  );
};

export default CanvasWrapper;
