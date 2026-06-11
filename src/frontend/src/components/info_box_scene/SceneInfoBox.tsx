import {PANEL_CHROME} from "@/state/themeStore";
import React from "react";

import CollapsibleSection from "@/components/common/CollapsibleSection";
import LoadedModelsSection from "./LoadedModelsSection";
import StatsSection from "./StatsSection";
import GroupsSection from "./GroupsSection";
import UtilitiesSection from "./UtilitiesSection";
import SectionPlanesPanel from "./SectionPlanesPanel";
import FemConceptsPanel from "./FemConceptsPanel";
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
        <div className={`${PANEL_CHROME} min-w-80 max-h-[80vh] overflow-y-auto`}>

            <div className="flex items-center justify-between mb-1">
                <h2 className="font-bold">Scene</h2>
                <select
                    className="text-sm rounded-sm px-1 py-0.5 bg-gray-700 text-gray-100 border border-gray-600"
                    value={mode}
                    onChange={(e) => setMode(e.target.value as "info" | "utilities" | "section" | "fem")}
                >
                    <option value="info">Info</option>
                    <option value="utilities">Utilities</option>
                    <option value="section">Section</option>
                    <option value="fem">FEM</option>
                </select>
            </div>
            {/* Flat list of every loaded model, whatever storage folder
                depth it came from — visible in every mode so toggling /
                unloading never requires digging through prefix trees. */}
            <CollapsibleSection title="Loaded models" defaultOpen>
                <LoadedModelsSection/>
            </CollapsibleSection>
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
            ) : mode === "section" ? (
                <SectionPlanesPanel/>
            ) : (
                <FemConceptsPanel/>
            )}
        </div>
    );
};

export default SceneInfoBox;
