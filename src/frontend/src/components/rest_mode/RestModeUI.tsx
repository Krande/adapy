import React from "react";
import ConversionProgress from "../conversion/ConversionProgress";
import UploadContextMenu from "../upload/UploadContextMenu";

// Aggregator for all REST-mode-only floating UI. Lazy-loaded by
// app.tsx so the embedded desktop bundle never pulls in the
// conversion / upload code paths.
const RestModeUI: React.FC = () => (
    <>
        <ConversionProgress/>
        <UploadContextMenu/>
    </>
);

export default RestModeUI;
