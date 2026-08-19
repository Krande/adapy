import React from "react";
import {PANEL_CHROME} from "@/state/themeStore";
import PropertiesPanel from "@/components/properties/PropertiesPanel";

// Classic-UI wrapper around the Properties panel.
//
// The 436 lines that used to live here were split, without behaviour change, into three
// providers in the Properties registry:
//
//   selection-summary  → components/properties/SelectionSummary.tsx (this file's body,
//                        verbatim, minus the chrome below)
//   object-metadata    → ObjectMetadataPanel (unchanged)
//   cellbuilder-cell   → CellBuilderSelectionInfo (unchanged)
//
// This wrapper keeps the classic UI byte-identical to what it rendered before: same
// panel chrome, same heading, same scrolling. The new shell mounts PropertiesPanel
// directly instead, because there the dock owns the box.
//
// Deleted at cutover, along with the rest of the classic chrome.

const ObjectInfoBox = () => (
    <div className={`${PANEL_CHROME} min-w-80 max-h-[80svh] overflow-y-auto`}>
        <h2 className="font-bold">Selected Object Info</h2>
        <PropertiesPanel />
    </div>
);

export default ObjectInfoBox;
