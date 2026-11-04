import React from "react";

import CollapsibleSection from "@/components/common/CollapsibleSection";
import LoadedModelsSection from "./LoadedModelsSection";
import SourceSection from "./SourceSection";
import StatsSection from "./StatsSection";
import ModelStatsSection from "./ModelStatsSection";
import GroupsSection from "./GroupsSection";
import SavedGroupsSection from "./SavedGroupsSection";
import UtilitiesSection from "./UtilitiesSection";
import BBoxSelectionSection from "./BBoxSelectionSection";
import FacePickingToggle from "./FacePickingToggle";
import FaceSearchSection from "./FaceSearchSection";
import SectionPlanesPanel from "./SectionPlanesPanel";
import JointsOverviewPanel from "./JointsOverviewPanel";
import ClashesPanel from "./ClashesPanel";
import MeshDistortionSection from "./MeshDistortionSection";
import OverlaysPanel from "./OverlaysPanel";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import {MODE_TO_TAB, TAB_META, TAB_TO_MODE} from "./sceneTabs";
import {useStatsStore} from "@/state/statsStore";
import {useClashCheckStore} from "@/state/clashCheckStore";
import {useBottomSheet} from "@/utils/useBottomSheet";

// The Scene panel groups everything that talks about the loaded scene (rather
// than a single selected object). Its destinations used to hide behind a 6-way
// mode <select>; now they're a tab strip that adapts to what's loaded. Every
// model is a GLB in the scene, so Model / Tools / Clip / Mesh always apply —
// only Joints is contextual. "Loaded models" stays pinned above the tabs,
// visible in every one. The FE model's concepts (masses / BCs / load scenarios)
// live in the Simulation panel's Model tab.
//
// The legacy modes map onto the tabs: Info + Source merge into "Model" (both
// describe what's loaded), Utilities → Tools, Section → Clip, and Mesh keeps its
// own tab. The store still holds a `mode`, so external openers keep working; the
// tab bar is just a nicer projection of it. `fem` stays a mode for other UI
// shells and lands on Model here.

// Colour tokens used by this panel — same CSS vars as PANEL_CHROME, but the
// pinned regions manage their own padding so the tab strip can span edge-to-edge
// and the body scrolls independently.
const CHROME =
    "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
    "text-[var(--ada-panel-text)] shadow-lg";

const SceneInfoBox = () => {
    const mode = useSceneInfoStore((s) => s.mode);
    const setMode = useSceneInfoStore((s) => s.setMode);
    const setShow = useSceneInfoStore((s) => s.setShowSceneInfoBox);

    // Joints is the contextual tab. It used to appear only when the loaded model's
    // take-off carried fabrication-detail joints (a model compiled with a detailing engine) --
    // "PRODUCED" joints. It now ALSO appears when a `Clashes` run has IDENTIFIED joints in the
    // loaded source, even before anything has been detailed: identification and review are two
    // ends of one workflow (§Decision 10), so the tab that reviews joints must exist as soon as
    // either half has something to show, not only after the second half runs.
    const hasProducedJoints = useStatsStore((s) => (s.stats?.joints?.count ?? 0) > 0);
    // Joints generated in THIS session count as produced too: they arrive as an overlay, which has
    // no stats of its own, so the compiled model's take-off above never sees them.
    const hasGeneratedJoints = useClashCheckStore((s) => (s.producedJoints?.count ?? 0) > 0);
    const hasIdentifiedJoints = useClashCheckStore((s) => (s.result?.joints.length ?? 0) > 0);
    const hasJoints = hasProducedJoints || hasGeneratedJoints || hasIdentifiedJoints;

    const {panelRef, isMobile, sheetStyle, grab} = useBottomSheet(() => setShow(false));

    const ctxAvailable: Record<string, boolean> = {joints: hasJoints};
    const tabs = TAB_META.filter((t) => !t.ctx || ctxAvailable[t.id]);
    // Derive the active tab from the store mode, falling back to Model if the
    // stored mode points at a tab that isn't currently available (e.g. Joints
    // after the model carrying them was unloaded).
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
                `overflow-y-hidden` is not decoration: CSS computes the OTHER axis to `auto` as
                soon as one is set, so `overflow-x-auto` alone gave the strip a vertical
                scrollbar of its own -- the tabs' `-mb-px` makes their content one pixel taller
                than the strip, which is all it takes. */}
            <div
                className="shrink-0 flex gap-0.5 px-1.5 border-b border-white/15 overflow-x-auto overflow-y-hidden"
                role="tablist"
                aria-label="Scene panel section"
            >
                {tabs.map((t) => (
                    <button
                        key={t.id}
                        type="button"
                        role="tab"
                        aria-selected={tab === t.id}
                        onClick={() => setMode(TAB_TO_MODE[t.id])}
                        className={
                            "px-2.5 py-1.5 font-semibold whitespace-nowrap border-b-2 -mb-px flex items-center gap-1.5 " +
                            (tab === t.id
                                ? "border-blue-400 text-[var(--ada-panel-text)]"
                                : "border-transparent text-gray-400 hover:text-[var(--ada-panel-text)]")
                        }
                    >
                        {t.ctx && (
                            <span
                                className="w-1.5 h-1.5 rounded-full bg-violet-400"
                                aria-hidden="true"
                            />
                        )}
                        {t.label}
                    </button>
                ))}
            </div>

            {/* ── scrollable tab body ── */}
            <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
                {tab === "model" ? (
                    <>
                        <FacePickingToggle />
                        <FaceSearchSection />
                        {/* ONE section, not two. `Stats` and `Take-off` were answering the same
                            question from different places -- what a GLB carried baked in, and the
                            take-off the conversion wrote beside it -- and for an ordinary
                            converted model that is the same document twice. The take-off leads;
                            baked per-model stats (an FEA simulation's element counts, a CAD
                            model's volume COG) follow underneath when a GLB has them, because
                            those say something the roll-up above has no shape for. */}
                        <CollapsibleSection title="Take-off" defaultOpen>
                            <ModelStatsSection />
                            <StatsSection />
                        </CollapsibleSection>
                        <CollapsibleSection title="Groups" defaultOpen={!isMobile}>
                            <GroupsSection />
                        </CollapsibleSection>
                        {/* The viewer's OWN groups, named from a selection across models and
                            shared in the scope -- beside the groups a model carries, not mixed
                            into that list, which is read-only and per model. */}
                        <CollapsibleSection title="Saved groups" defaultOpen={false}>
                            <SavedGroupsSection />
                        </CollapsibleSection>
                        {/* Selection boxes beside the groups: both are ways of naming a selection. */}
                        <CollapsibleSection title="Selection boxes" defaultOpen={!isMobile}>
                            <BBoxSelectionSection />
                        </CollapsibleSection>
                        <CollapsibleSection title="Source & re-convert" defaultOpen={false}>
                            <SourceSection />
                        </CollapsibleSection>
                    </>
                ) : tab === "overlays" ? (
                    <OverlaysPanel />
                ) : tab === "tools" ? (
                    <UtilitiesSection />
                ) : tab === "clip" ? (
                    <SectionPlanesPanel />
                ) : tab === "mesh" ? (
                    <MeshDistortionSection />
                ) : tab === "clashes" ? (
                    <ClashesPanel />
                ) : (
                    <JointsOverviewPanel />
                )}
            </div>
        </div>
    );
};

export default SceneInfoBox;
