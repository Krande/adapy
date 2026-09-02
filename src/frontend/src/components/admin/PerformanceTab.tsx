import React, {useState} from "react";

import AdminSubTabs from "./AdminSubTabs";
import {PERFORMANCE_SUB_TABS} from "./adminTabs";
import type {PerformanceSubTab} from "./adminTabs";
import FrontendLoadsTab from "./FrontendLoadsTab";
import WorkerPerformanceTab from "./WorkerPerformanceTab";

// The Performance tab.
//
// "Performance" and "Frontend Loads" were separate top-level tabs. They answer
// one question — why is it slow — split by which side is slow, which is
// precisely the thing the operator is trying to find out. Choosing the right
// tab required already knowing the answer.
//
// Server-side conversion cost is "Workers"; browser load/render cost is
// "Frontend". Both are now one click apart, so comparing them is a comparison
// rather than a navigation.


const PerformanceTab: React.FC<{initialSubTab?: PerformanceSubTab}> = ({initialSubTab}) => {
    const [sub, setSub] = useState<PerformanceSubTab>(initialSubTab ?? "workers");

    return (
        <div className="h-full flex flex-col">
            <AdminSubTabs tabs={PERFORMANCE_SUB_TABS} active={sub} onSelect={setSub}/>
            <div className="flex-1 min-h-0 overflow-hidden">
                {sub === "workers" && <WorkerPerformanceTab/>}
                {sub === "frontend" && <FrontendLoadsTab/>}
            </div>
        </div>
    );
};

export default PerformanceTab;
