import React from "react";

import CollapsibleSection from "@/components/common/CollapsibleSection";
import StatsSection from "./StatsSection";
import GroupsSection from "./GroupsSection";
import UtilitiesSection from "./UtilitiesSection";
import SectionPlanesPanel from "./SectionPlanesPanel";
import {useSceneInfoStore} from "@/state/sceneInfoStore";

// Container for everything that talks about the currently-loaded scene
// rather than a single selected object. A mode dropdown switches between
// "Info" (Stats + Groups — the per-model metrics baked into the ADA glTF
// extension + the design/simulation group picker) and "Utilities" (worker-
// defined operations such as the branch/SHA diff that recolours the scene).
const SceneInfoBox = () => {
    const mode = useSceneInfoStore((s) => s.mode);
    const setMode = useSceneInfoStore((s) => s.setMode);
    return (
        <div className="bg-gray-400 bg-opacity-50 rounded-sm p-2 min-w-80 max-h-[80vh] overflow-y-auto">

            <div className="flex items-center justify-between mb-1">
                <h2 className="font-bold">Scene</h2>
                <select
                    className="text-sm rounded-sm px-1 py-0.5 bg-white text-black"
                    value={mode}
                    onChange={(e) => setMode(e.target.value as "info" | "utilities" | "section")}
                >
                    <option value="info">Info</option>
                    <option value="utilities">Utilities</option>
                    <option value="section">Section</option>
                </select>
            </div>
            {mode === "info" ? (
                <>
                    <CollapsibleSection title="Stats" defaultOpen>
                        <StatsSection/>
                    </CollapsibleSection>
                    <CollapsibleSection title="Groups" defaultOpen>
                        <GroupsSection/>
                    </CollapsibleSection>
                </>
            ) : mode === "utilities" ? (
                <UtilitiesSection/>
            ) : (
                <SectionPlanesPanel/>
            )}
        </div>
    );
};

export default SceneInfoBox;
