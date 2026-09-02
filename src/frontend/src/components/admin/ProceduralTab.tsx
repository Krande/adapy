import React, {useState} from "react";

import AdminSubTabs from "./AdminSubTabs";
import {PROCEDURAL_SUB_TABS} from "./adminTabs";
import type {ProceduralSubTab} from "./adminTabs";
import EquipmentAdminPanel from "./EquipmentAdminPanel";
import ProceduralEngineAdminPanel from "./ProceduralEngineAdminPanel";
import SystemAdminPanel from "./SystemAdminPanel";

// The Procedural Engine tab.
//
// Engines, Systems and Equipment were three top-level tabs, and they are three
// per-scope catalogues feeding the same consumer: the cellbuilder. Which engine
// compiles a model, which service systems it may route, and which equipment it
// may place are the same setup task, done in one sitting, and they were spread
// across a strip where nothing said they were related.
//
// Each panel keeps its own scope selector and its own state; this only groups
// them. They are ordered by what an operator sets up first: the engine that
// compiles, then the systems it routes, then the equipment it places.


const ProceduralTab: React.FC<{initialSubTab?: ProceduralSubTab}> = ({initialSubTab}) => {
    const [sub, setSub] = useState<ProceduralSubTab>(initialSubTab ?? "engines");

    return (
        <div className="h-full flex flex-col">
            <AdminSubTabs tabs={PROCEDURAL_SUB_TABS} active={sub} onSelect={setSub}/>
            {/* These three panels are page-shaped rather than table-shaped, so
                they scroll as a whole — matching how they were mounted when
                each had a tab to itself. */}
            <div className="flex-1 min-h-0 overflow-y-auto p-3 sm:p-4">
                {sub === "engines" && <ProceduralEngineAdminPanel embedded/>}
                {sub === "systems" && <SystemAdminPanel embedded/>}
                {sub === "equipment" && <EquipmentAdminPanel embedded/>}
            </div>
        </div>
    );
};

export default ProceduralTab;
