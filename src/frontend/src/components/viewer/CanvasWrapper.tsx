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
  return (
    <div className="relative w-full h-full">
      {legend && (
        <div className="absolute right-5 top-80 z-10">
          <ColorLegend />
        </div>
      )}

      <div id="canvasParent" className="absolute w-full h-full">
        <ThreeCanvas />
      </div>
      {/* Gallery HUD (opt-in via Theme options): prev/next over the scope's files. */}
      <GalleryControls />
      {/* Headless: reconciles section-plane clipping/caps/gizmo with the scene. */}
      <SectionPlanesController />
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
