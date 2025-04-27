// CanvasComponent.tsx
import React from "react";
import ColorLegend from "./ColorLegend";
import ThreeCanvas from "./ThreeCanvas";

const CanvasWrapper: React.FC = () => {
  return (
    <div className="relative w-full h-full">
      <div className="absolute right-5 top-80 z-10">
        <ColorLegend />
      </div>

      <div id="canvasParent" className="absolute w-full h-full">
        <ThreeCanvas />
      </div>
    </div>
  );
};

export default CanvasWrapper;
