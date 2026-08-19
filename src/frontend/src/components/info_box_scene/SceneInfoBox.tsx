import React from "react";

import CollapsibleSection from "@/components/common/CollapsibleSection";
import LoadedModelsSection from "./LoadedModelsSection";
import SourceSection from "./SourceSection";
import StatsSection from "./StatsSection";
import ModelStatsSection from "./ModelStatsSection";
import GroupsSection from "./GroupsSection";
import UtilitiesSection from "./UtilitiesSection";
import FacePickingToggle from "./FacePickingToggle";
import FaceSearchSection from "./FaceSearchSection";
import SectionPlanesPanel from "./SectionPlanesPanel";
import FemConceptsPanel from "./FemConceptsPanel";
import JointsOverviewPanel from "./JointsOverviewPanel";
import MeshDistortionSection from "./MeshDistortionSection";
import {useSceneInfoStore, type SceneInfoMode} from "@/state/sceneInfoStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useStatsStore} from "@/state/statsStore";
import {useBottomSheet} from "@/utils/useBottomSheet";
import {Tabs} from "@/components/ui";

// The Scene panel groups everything that talks about the loaded scene (rather
// than a single selected object). Its destinations used to hide behind a 6-way
// mode <select>; now they're a tab strip that adapts to what's loaded. Every
// model is a GLB in the scene, so Model / Tools / Clip / Mesh always apply —
// only FEM is contextual (it needs baked FE concepts: masses / BCs / load
// scenarios). "Loaded models" stays pinned above the tabs, visible in every one.
//
// The 6 legacy modes map onto 5 tabs: Info + Source merge into "Model" (both
// describe what's loaded), Utilities → Tools, Section → Clip, and Mesh / FEM
// keep their own tabs. The store still holds a `mode`, so external openers keep
// working; the tab bar is just a nicer projection of it.

// Colour tokens used by this panel — same CSS vars as PANEL_CHROME, but the
// pinned regions manage their own padding so the tab strip can span edge-to-edge
// and the body scrolls independently.
const CHROME =
    "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
    "text-[var(--ada-panel-text)] shadow-lg";

type SceneTab = "model" | "tools" | "clip" | "mesh" | "fem" | "joints";

const TAB_META: {id: SceneTab; label: string; ctx?: boolean}[] = [
    {id: "model", label: "Model"},
    {id: "tools", label: "Tools"},
    {id: "clip", label: "Clip"},
    {id: "mesh", label: "Mesh"},
    {id: "fem", label: "FEM", ctx: true},
    {id: "joints", label: "Joints", ctx: true},
];

const MODE_TO_TAB: Record<SceneInfoMode, SceneTab> = {
    info: "model",
    source: "model",
    utilities: "tools",
    section: "clip",
    mesh: "mesh",
    fem: "fem",
    joints: "joints",
};

const TAB_TO_MODE: Record<SceneTab, SceneInfoMode> = {
    model: "info",
    tools: "utilities",
    clip: "section",
    mesh: "mesh",
    fem: "fem",
    joints: "joints",
};

const SceneInfoBox = () => {
    const mode = useSceneInfoStore((s) => s.mode);
    const setMode = useSceneInfoStore((s) => s.setMode);
    const setShow = useSceneInfoStore((s) => s.setShowSceneInfoBox);

    // FEM is a contextual tab. It appears when the loaded model carries FE
    // concepts (masses / boundary conditions / load cases) OR whenever an FEA
    // result session is active — any FEA result file (e.g. a Sesam SIN) streams
    // through the FEA path and enables the panel's mesh tools ("Beams as solid",
    // scenario selector) even when the result carries no baked concepts, so the
    // tab shouldn't be limited to concept-carrying models.
    const femHasConcepts = useFemConceptsStore(
        (s) => s.masses.length > 0 || s.bcs.length > 0 || s.scenarios.length > 0,
    );
    const feaSessionActive = useFeaAnimationStore((s) => s.sessionActive);
    const femTabAvailable = femHasConcepts || feaSessionActive;
    // Joints is the other contextual tab: it appears only when the loaded model's
    // take-off carries fabrication-detail joints (a model compiled with a
    // detailing engine).
    const hasJoints = useStatsStore((s) => (s.stats?.joints?.count ?? 0) > 0);

    const {panelRef, isMobile, sheetStyle, grab} = useBottomSheet(() => setShow(false));

    const ctxAvailable: Record<string, boolean> = {fem: femTabAvailable, joints: hasJoints};
    const tabs = TAB_META.filter((t) => !t.ctx || ctxAvailable[t.id]);
    // Derive the active tab from the store mode, falling back to Model if the
    // stored mode points at a tab that isn't currently available (e.g. FEM after
    // the FE model was unloaded).
    let tab = MODE_TO_TAB[mode];
    if (!tabs.some((t) => t.id === tab)) tab = "model";

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
                <span className="block w-10 h-1.5 rounded-full bg-gray-400/70" aria-hidden="true" />
            </div>

            {/* ── pinned header ── */}
            <div className="shrink-0 flex items-center justify-between px-2.5 pt-1.5 pb-1">
                <h2 className="font-bold">Scene</h2>
            </div>

            {/* Pinned flat list of every loaded model — visible in every tab so
                toggling / unloading never means digging through prefix trees.
                Capped so a long list can't swallow the whole sheet. */}
            <div className="shrink-0 px-2.5 max-h-40 overflow-y-auto">
                <CollapsibleSection title="Loaded models" defaultOpen>
                    <LoadedModelsSection />
                </CollapsibleSection>
            </div>

            {/* ── adaptive tab strip ──
                Re-chromed onto the design-system Tabs primitive. The store still owns
                which tab is active (deep links and cross-panel actions set it from
                outside), and the contextual dot on FEM/Joints is now the primitive's
                `contextual` flag rather than a hand-placed span. Gained for free:
                roving-tabindex arrow-key navigation, which the hand-rolled strip
                never had. */}
            <div className="shrink-0 px-1.5">
                <Tabs
                    label="Scene panel section"
                    value={tab}
                    onChange={(id) => setMode(TAB_TO_MODE[id as SceneTab])}
                    items={tabs.map((t) => ({id: t.id, label: t.label, contextual: t.ctx}))}
                />
            </div>

            {/* ── scrollable tab body ── */}
            <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
                {tab === "model" ? (
                    <>
                        <FacePickingToggle />
                        <FaceSearchSection />
                        <CollapsibleSection title="Stats" defaultOpen>
                            <StatsSection />
                        </CollapsibleSection>
                        <CollapsibleSection title="Take-off" defaultOpen>
                            <ModelStatsSection />
                        </CollapsibleSection>
                        <CollapsibleSection title="Groups" defaultOpen={!isMobile}>
                            <GroupsSection />
                        </CollapsibleSection>
                        <CollapsibleSection title="Source & re-convert" defaultOpen={false}>
                            <SourceSection />
                        </CollapsibleSection>
                    </>
                ) : tab === "tools" ? (
                    <UtilitiesSection />
                ) : tab === "clip" ? (
                    <SectionPlanesPanel />
                ) : tab === "mesh" ? (
                    <MeshDistortionSection />
                ) : tab === "joints" ? (
                    <JointsOverviewPanel />
                ) : (
                    <FemConceptsPanel />
                )}
            </div>
        </div>
    );
};

export default SceneInfoBox;
