import React from "react";

import CollapsibleSection from "@/components/common/CollapsibleSection";
import StatsSection from "./StatsSection";
import GroupsSection from "./GroupsSection";

// Container for everything that talks about the currently-loaded scene
// rather than a single selected object. Stats reads the per-model
// metrics baked into the ADA glTF extension; Groups is the existing
// design / simulation group picker (combobox + selection details).
const SceneInfoBox = () => {
    return (
        <div className="bg-gray-400 bg-opacity-50 rounded-sm p-2 min-w-80">
            <h2 className="font-bold mb-1">Scene</h2>
            <CollapsibleSection title="Stats" defaultOpen>
                <StatsSection/>
            </CollapsibleSection>
            <CollapsibleSection title="Groups" defaultOpen>
                <GroupsSection/>
            </CollapsibleSection>
        </div>
    );
};

export default SceneInfoBox;
