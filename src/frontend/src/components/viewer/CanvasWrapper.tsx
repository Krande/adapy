// CanvasComponent.tsx
import React from "react";
import ColorLegend from "./ColorLegend";
import ThreeCanvas from "./ThreeCanvas";
import SectionPlanesController from "./SectionPlanesController";
import FemConceptsController from "./FemConceptsController";
import CellBuilderController from "./CellBuilderController";
import TypeIconController from "./TypeIconController";
import NameLabelController from "./NameLabelController";
import ProceduralFollowerController from "./ProceduralFollowerController";
import GalleryControls from "./GalleryControls";
import BBoxSelectionController from "./BBoxSelectionController";
import SpaceMouseConnectChip from "./SpaceMouseConnectChip";
import RenderRecordingHud from "./RenderRecordingHud";
import PlaybackTransport from "@/components/playback/PlaybackTransport";
import {useAdacppRenderStatus} from "@/viewport/adacpp/adacppViewport";
import {usePerfStore} from "@/state/perfStore";
import {useViewerRefs} from "@/state/AdaViewerContext";
import {isNativeViewportActive, useNativeViewport} from "@/viewport/native/nativeModel";
import {loadedModelJournal, replayModelJournal} from "./sceneHelpers/loadedModelJournal";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {installViewportPanelBridge} from "./sceneHelpers/viewportPanelBridge";
import {installSpaceMousePoseDriver} from "./sceneHelpers/spaceMousePoseDriver";
import {installCameraHomeOnEmpty} from "./sceneHelpers/cameraHomeOnEmpty";
import {installAnnouncedGroups} from "@/utils/groups/extensionGroups";
import {bindViewerHistoryReset} from "@/utils/scene/viewerHistoryBindings";

// Its own chunk, which imports no three.js.
const NativeViewport = React.lazy(() => import("@/viewport/native/NativeViewport"));
// The panels listen for what a viewport without a three.js scene announces (its model, its clicks).
installViewportPanelBridge();
// The groups of a model such a viewport loads go to the Scene panel's picker.
installAnnouncedGroups();
// A 3D mouse moves that viewport's camera through its pose.
installSpaceMousePoseDriver();
// An emptied scene puts the camera of the viewport on screen back where the viewer started.
installCameraHomeOnEmpty();

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

/** The user switched between the three.js viewport and the three.js-free one: the viewport that comes
 *  up loads the models the other was showing (sceneHelpers/loadedModelJournal.ts). The three.js scene
 *  outlives its mount and may still hold what it showed before, but the panels (Scene tree, object
 *  list) were told about the native viewport's models since: it is loaded again all the same, the
 *  first load replacing what the scene holds. A failure of the native viewport is not a switch:
 *  `useNativeFallbackReload` handles that. */
function useModelHandOver(native: boolean): void {
  const {scene} = useViewerRefs();
  const shown = React.useRef<boolean | null>(null);
  React.useEffect(() => {
    const before = shown.current;
    shown.current = native;
    if (before === null || before === native) return;
    if (!native && useNativeViewport.getState().state === "failed") return;
    // The native viewport's last model must not come back with it on the next switch, ahead of the hand-over.
    if (!native) useNativeViewport.setState({model: null});
    const {entries} = loadedModelJournal();
    if (!entries.length) return;
    const snapshot = [...entries];
    let cancelled = false;
    let unsubscribe: (() => void) | null = null;
    const ready = () => (native ? useNativeViewport.getState().state === "active" : !!scene.current && !isNativeViewportActive());
    const start = () => {
      unsubscribe?.();
      unsubscribe = null;
      void replayModelJournal(snapshot, () => !cancelled);
    };
    if (ready()) start();
    else if (native) {
      // The native viewport's chunk and WebGPU start up first; a failed start is the fallback's.
      unsubscribe = useNativeViewport.subscribe((s) => {
        if (s.state === "failed") {
          unsubscribe?.();
          unsubscribe = null;
        } else if (ready() && !cancelled) start();
      });
    } else {
      const tryLater = () => {
        if (cancelled) return;
        if (ready()) start();
        else setTimeout(tryLater, 100);
      };
      setTimeout(tryLater, 100);
    }
    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, [native, scene]);
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
  const native = renderBackend === "adacpp-native" && !nativeFailed;
  useModelHandOver(native);
  // Models loaded and removed are undo steps on that viewport too; the three.js viewport binds
  // the same history itself while it is up.
  React.useEffect(() => (native ? bindViewerHistoryReset() : undefined), [native]);
  if (native) {
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
        {/* The playback clock's transport: nothing while no track is registered. */}
        <PlaybackTransport />
        {/* Headless, as below: they draw through whichever viewport is on screen. */}
        <TypeIconController />
        <NameLabelController />
        <FemConceptsController />
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
      {/* The playback clock's transport: nothing while no track is registered. */}
      <PlaybackTransport />
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
      {/* Headless: the names of the selected objects, while that switch is on. */}
      <NameLabelController />
      {/* Cross-tab: when opened with ?pfollow=<modelId>, live-loads that model's
          compiled results as the editing tab produces them. Inert otherwise. */}
      <ProceduralFollowerController />
    </div>
  );
};

export default CanvasWrapper;
