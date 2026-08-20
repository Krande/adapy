import React from "react";

import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useStatsStore} from "@/state/statsStore";
import {useBottomSheet} from "@/utils/useBottomSheet";
import SceneBody, {type SceneTab} from "./SceneBody";

// Classic-UI wrapper around the Scene panel.
//
// The content moved to SceneBody so the shell's dock can host it without this bordered,
// separately-scrolling frame — which inside a dock produced a box in a box with two
// scrollbars, the same fault OptionsComponent had. This wrapper keeps the classic UI's
// two layouts: a floating panel in the menu column on desktop, a draggable bottom sheet
// on a phone. Both go at cutover.
//
// The contextual-tab logic stays here rather than in the body, because it is a question
// about the loaded model, not about presentation, and the shell needs the same answer.

const CHROME =
    "bg-surface-1 border border-edge text-content shadow-float";

/** Which contextual tabs currently have anything to show. Shared with the shell. */
export function useSceneContextTabs(): Partial<Record<SceneTab, boolean>> {
    // FEM appears when the loaded model carries FE concepts (masses / boundary
    // conditions / load cases) OR whenever an FEA result session is active — any FEA
    // result file (e.g. a Sesam SIN) streams through the FEA path and enables the mesh
    // tools even when the result carries no baked concepts, so the tab must not be
    // limited to concept-carrying models.
    const femHasConcepts = useFemConceptsStore(
        (s) => s.masses.length > 0 || s.bcs.length > 0 || s.scenarios.length > 0,
    );
    const feaSessionActive = useFeaAnimationStore((s) => s.sessionActive);

    // Joints appears only when the take-off carries fabrication-detail joints — a model
    // compiled with a detailing engine.
    const hasJoints = useStatsStore((s) => (s.stats?.joints?.count ?? 0) > 0);

    return {fem: femHasConcepts || feaSessionActive, joints: hasJoints};
}

const SceneInfoBox = () => {
    const setShow = useSceneInfoStore((s) => s.setShowSceneInfoBox);
    const ctxAvailable = useSceneContextTabs();
    const {panelRef, isMobile, sheetStyle, grab} = useBottomSheet(() => setShow(false));

    return (
        <div
            ref={panelRef}
            style={sheetStyle}
            className={
                CHROME +
                " text-sm flex flex-col overflow-hidden " +
                // mobile: dock as a bottom sheet; desktop: float in the menu column.
                "fixed inset-x-0 bottom-0 z-30 w-full max-h-[82vh] rounded-t-2xl " +
                "sm:static sm:z-auto sm:w-auto sm:min-w-80 sm:max-w-[420px] sm:max-h-[80vh] sm:rounded-md"
            }
        >
            {/* mobile grab handle — drag to resize the sheet, flick down to dismiss */}
            <div
                className="sm:hidden shrink-0 flex justify-center items-center py-2 cursor-grab active:cursor-grabbing touch-none"
                role="separator"
                aria-label="Drag to resize the panel"
                {...grab}
            >
                <span className="block w-10 h-1.5 rounded-full bg-surface-3" aria-hidden="true" />
            </div>

            <div className="shrink-0 flex items-center justify-between px-2.5 pt-1.5 pb-1">
                <h2 className="font-bold">Scene</h2>
            </div>

            <SceneBody isMobile={isMobile} ctxAvailable={ctxAvailable} />
        </div>
    );
};

export default SceneInfoBox;
