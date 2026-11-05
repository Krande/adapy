import React from "react";

import {PanelCard, PanelHeaderActions} from "@/components/ui/PanelFrame";
import {SheetHandle} from "@/components/ui/SheetHandle";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {useBottomSheet} from "@/utils/useBottomSheet";
import SceneBody from "./SceneBody";

// The Scene panel's classic frame around its content (`SceneBody`): the
// menu-column card on desktop, a bottom sheet on phones, with the panel's heading
// pinned above the content. The card is the panel surface only -- the content's
// pinned regions manage their own padding so the tab strip can span edge-to-edge
// and the body scrolls independently.
const SceneInfoBox = () => {
    const setShow = useSceneInfoStore((s) => s.setShowSceneInfoBox);
    const {panelRef, sheetStyle, grab} = useBottomSheet(() => setShow(false));

    return (
        <PanelCard
            ref={panelRef}
            style={sheetStyle}
            variant="surface"
            className={
                "text-sm flex flex-col overflow-hidden " +
                // mobile: dock as a bottom sheet; desktop: float in the menu column.
                "fixed inset-x-0 bottom-0 z-30 w-full max-h-[82vh] rounded-t-2xl " +
                "sm:static sm:z-auto sm:w-auto sm:min-w-80 sm:max-w-[420px] sm:max-h-[80vh] sm:rounded-md"
            }
        >
            {/* mobile grab handle — drag to resize the sheet, flick down to dismiss */}
            <SheetHandle grab={grab} className="sm:hidden" />

            {/* ── pinned header ── */}
            <div className="shrink-0 flex items-center justify-between px-2.5 pt-1.5 pb-1">
                <h2 className="font-bold">Scene</h2>
                <PanelHeaderActions />
            </div>

            <SceneBody />
        </PanelCard>
    );
};

export default SceneInfoBox;
