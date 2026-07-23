// CanvasComponent.tsx
import React from "react";
import ColorLegend from "./ColorLegend";
import ThreeCanvas from "./ThreeCanvas";
import SectionPlanesController from "./SectionPlanesController";
import FemConceptsController from "./FemConceptsController";
import CellBuilderController from "./CellBuilderController";
import GalleryControls from "./GalleryControls";

const CanvasWrapper: React.FC = () => {
  return (
    <div className="relative w-full h-full">
      <div className="absolute right-5 top-80 z-10">
        <ColorLegend />
      </div>

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
    </div>
  );
};

export default CanvasWrapper;
