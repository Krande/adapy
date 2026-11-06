import React, {useLayoutEffect, useRef, useState} from "react";

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
import FemConceptsPanel from "@/components/simulation/FemConceptsPanel";
import {activeSceneTab, sceneTabs, shouldStackTabs, TAB_TO_MODE, type SceneTab, type SceneTabOptions} from "./sceneTabs";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useStatsStore} from "@/state/statsStore";
import {useClashCheckStore} from "@/state/clashCheckStore";
import {useIsMobile} from "@/utils/useIsMobile";
import {isMirroredWindow} from "@/layout/sceneActions";

export type SceneArrangement = "tabs" | "stacked" | "auto";

/** Options of the Scene panel's content. Absent, each leaves the panel as it is. */
export interface SceneBodyProps extends SceneTabOptions {
    /** A tab strip (the default), a column of collapsible sections, or whichever the body's
     *  height suits (`shouldStackTabs`). */
    arrangement?: SceneArrangement;
    /** Heading of the pinned list of loaded models. Default "Loaded models". */
    loadedTitle?: string;
    /** Shown in that list while no model is loaded. */
    loadedEmpty?: React.ReactNode;
}

// The Scene panel's content: everything that talks about the loaded scene
// (rather than a single selected object). Its frame -- the menu-column card that
// is a bottom sheet on phones, with the panel's heading -- is `SceneInfoBox`'s.
//
// Its destinations used to hide behind a 6-way mode <select>; now they're a tab
// strip that adapts to what's loaded. Every model is a GLB in the scene, so
// Model / Tools / Clip / Mesh always apply — only Joints is contextual. "Loaded
// models" stays pinned above the tabs, visible in every one. The FE model's
// concepts (masses / BCs / load scenarios) live in the Simulation panel's Model
// tab, and in an FEM tab here only where a host asks for one (`femTab`).
//
// The legacy modes map onto the tabs: Info + Source merge into "Model" (both
// describe what's loaded), Utilities → Tools, Section → Clip, and Mesh keeps its
// own tab. The store still holds a `mode`, so external openers keep working; the
// tab bar is just a nicer projection of it. `fem` stays a mode for other UI
// shells and lands on Model here.
//
// Three regions: the pinned model list, the tab strip, and a body that scrolls on
// its own. A frame lays them out in a flex column (the card has no padding of its
// own, so the strip can span edge to edge).
const SceneBody: React.FC<SceneBodyProps> = ({tabs: tabIds, hiddenTabs, femTab = false, arrangement = "tabs", loadedTitle = "Loaded models", loadedEmpty}) => {
    const mode = useSceneInfoStore((s) => s.mode);
    const setMode = useSceneInfoStore((s) => s.setMode);

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

    // FEM, where offered: the FE model's concepts, or any FEA result session.
    const hasFemConcepts = useFemConceptsStore((s) => s.masses.length > 0 || s.bcs.length > 0 || s.scenarios.length > 0);
    const feaSessionActive = useFeaAnimationStore((s) => s.sessionActive);

    const isMobile = useIsMobile();

    const ctxAvailable: Partial<Record<SceneTab, boolean>> = {joints: hasJoints, fem: hasFemConcepts || feaSessionActive};
    // A pop-out window has the Model tab: the others (overlays, tools, clip, mesh, clashes, joints)
    // read and change the scene itself, which only the main window has.
    const mirrored = isMirroredWindow();
    const tabs = sceneTabs({tabs: tabIds, hiddenTabs, femTab}, ctxAvailable).filter((t) => !mirrored || t.id === "model");
    // Derive the active tab from the store mode, falling back to Model if the
    // stored mode points at a tab that isn't currently available (e.g. Joints
    // after the model carrying them was unloaded).
    const tab = activeSceneTab(mode, tabs, femTab);

    const bodyRef = useRef<HTMLDivElement | null>(null);
    const [autoStacked, setAutoStacked] = useState(false);
    // Measured before paint, then on every resize; only in "auto".
    useLayoutEffect(() => {
        const el = bodyRef.current;
        if (arrangement !== "auto" || !el) return;
        const measure = () => setAutoStacked((was) => shouldStackTabs(tabs.length, el.clientHeight, was));
        measure();
        if (typeof ResizeObserver === "undefined") return;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [arrangement, tabs.length]);
    const stacked = arrangement === "stacked" || (arrangement === "auto" && autoStacked);

    return (
        <>
            {/* Pinned flat list of every loaded model — visible in every tab so
                toggling / unloading never means digging through prefix trees.
                Capped so a long list can't swallow the whole sheet. */}
            <div className="shrink-0 px-2.5 max-h-40 overflow-y-auto">
                <CollapsibleSection title={loadedTitle} defaultOpen>
                    <LoadedModelsSection empty={loadedEmpty} />
                </CollapsibleSection>
            </div>

            {!stacked && (<>
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
            </>)}

            {/* ── scrollable tab body ── */}
            <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
                {stacked ? (
                    <div className="flex flex-col">
                        {tabs.map((t) => (
                            // The tab the store points at opens, so an opener lands on it here too.
                            <CollapsibleSection key={t.id} title={t.label} defaultOpen={t.id === tab}>
                                <TabContent tab={t.id} isMobile={isMobile} />
                            </CollapsibleSection>
                        ))}
                    </div>
                ) : (
                    <TabContent tab={tab} isMobile={isMobile} />
                )}
            </div>
        </>
    );
};

/** One tab's content, the same in either arrangement. */
const TabContent: React.FC<{tab: SceneTab; isMobile: boolean}> = ({tab, isMobile}) =>
                tab === "model" ? (
                    <>
                        {!isMirroredWindow() && <FacePickingToggle />}
                        {!isMirroredWindow() && <FaceSearchSection />}
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
                        {!isMirroredWindow() && (
                            <CollapsibleSection title="Saved groups" defaultOpen={false}>
                                <SavedGroupsSection />
                            </CollapsibleSection>
                        )}
                        {/* Selection boxes beside the groups: both are ways of naming a selection. */}
                        <CollapsibleSection title="Selection boxes" defaultOpen={!isMobile}>
                            <BBoxSelectionSection />
                        </CollapsibleSection>
                        {!isMirroredWindow() && (
                            <CollapsibleSection title="Source & re-convert" defaultOpen={false}>
                                <SourceSection />
                            </CollapsibleSection>
                        )}
                    </>
                ) : tab === "overlays" ? (
                    <OverlaysPanel />
                ) : tab === "tools" ? (
                    <UtilitiesSection />
                ) : tab === "clip" ? (
                    <SectionPlanesPanel />
                ) : tab === "mesh" ? (
                    <MeshDistortionSection />
                ) : tab === "fem" ? (
                    <FemConceptsPanel />
                ) : tab === "clashes" ? (
                    <ClashesPanel />
                ) : (
                    <JointsOverviewPanel />
                );

export default SceneBody;
