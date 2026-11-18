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
import MeshDistortionSection from "./MeshDistortionSection";
import OverlaysPanel from "./OverlaysPanel";
import MeasurePanel from "./MeasurePanel";
import {useSceneInfoStore} from "@/state/sceneInfoStore";
import FemConceptsPanel from "@/components/simulation/FemConceptsPanel";
import {
    activeSceneTab,
    groupOf,
    sceneGroups,
    sceneTabs,
    shouldStackTabs,
    TAB_TO_MODE,
    type OfferedGroup,
    type SceneGroup,
    type SceneTab,
    type SceneTabOptions,
} from "./sceneTabs";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
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
// Model / Tools / Clip / Mesh always apply. "Loaded models" stays pinned above
// the tabs, visible in every one. The FE model's concepts (masses / BCs / load
// scenarios) live in the Simulation panel's Model tab, and in an FEM tab here
// only where a host asks for one (`femTab`).
//
// The legacy modes map onto the tabs: Info + Source merge into "Model" (both
// describe what's loaded), Utilities → Tools, Section → Clip, and Mesh keeps its
// own tab. The store still holds a `mode`, so external openers keep working; the
// tab bar is just a nicer projection of it. `fem` stays a mode for other UI
// shells and lands on Model here.
//
// The strip shows GROUPS of related tabs (`sceneTabs`' `SCENE_GROUPS`): Model, View
// (Overlays, Clip, Mesh), Measure, Tools -- and a group of more than one offered tab a second
// strip of its tabs. Measure (`MeasurePanel`) is the main window's, as the others but Model are. The tabs are still what the store's mode and the `tabs` /
// `hiddenTabs` options name.
//
// The Joints group (the connection builder, Clashes, the joints review and the joint
// overlays) moved to the Connections panel (`ConnectionBuilderPanel`); a mode that
// named one of its tabs opens that panel there (`layout/connectionsTabActions`).
//
// Three regions: the pinned model list, the tab strip(s), and a body that scrolls on
// its own. A frame lays them out in a flex column (the card has no padding of its
// own, so the strip can span edge to edge).
const SceneBody: React.FC<SceneBodyProps> = ({tabs: tabIds, hiddenTabs, femTab = false, arrangement = "tabs", loadedTitle = "Loaded models", loadedEmpty}) => {
    const mode = useSceneInfoStore((s) => s.mode);
    const setMode = useSceneInfoStore((s) => s.setMode);

    // FEM, where offered: the FE model's concepts, or any FEA result session. The only contextual tab.
    const hasFemConcepts = useFemConceptsStore((s) => s.masses.length > 0 || s.bcs.length > 0 || s.scenarios.length > 0);
    const feaSessionActive = useFeaAnimationStore((s) => s.sessionActive);

    const isMobile = useIsMobile();

    const ctxAvailable: Partial<Record<SceneTab, boolean>> = {
        fem: hasFemConcepts || feaSessionActive,
    };
    // A pop-out window has the Model tab: the others (overlays, tools, clip, mesh, measure) read and change
    // the scene itself, which only the main window has.
    const mirrored = isMirroredWindow();
    const tabs = sceneTabs({tabs: tabIds, hiddenTabs, femTab}, ctxAvailable).filter((t) => !mirrored || t.id === "model");
    // Derive the active tab from the store mode, falling back to Model if the
    // stored mode points at a tab that isn't currently available (e.g. Joints
    // after the model carrying them was unloaded).
    const tab = activeSceneTab(mode, tabs, femTab);
    const groups = sceneGroups(tabs);
    const group = groups.find((g) => g.tabs.some((t) => t.id === tab)) ?? groups[0];
    // A group reopens on the tab last shown in it, where that is still offered.
    const [lastInGroup, setLastInGroup] = useState<Partial<Record<SceneGroup, SceneTab>>>({});
    const openTab = (id: SceneTab) => {
        setLastInGroup((was) => ({...was, [groupOf(id)]: id}));
        setMode(TAB_TO_MODE[id]);
    };
    const openGroup = (g: OfferedGroup) => {
        const last = lastInGroup[g.id];
        openTab(last && g.tabs.some((t) => t.id === last) ? last : g.tabs[0].id);
    };

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
            {/* ── adaptive group strip ──
                `overflow-y-hidden` is not decoration: CSS computes the OTHER axis to `auto` as
                soon as one is set, so `overflow-x-auto` alone gave the strip a vertical
                scrollbar of its own -- the tabs' `-mb-px` makes their content one pixel taller
                than the strip, which is all it takes. */}
            <div
                className="shrink-0 flex gap-0.5 px-1.5 border-b border-white/15 overflow-x-auto overflow-y-hidden"
                role="tablist"
                aria-label="Scene panel section"
            >
                {groups.map((g) => (
                    <button
                        key={g.id}
                        type="button"
                        role="tab"
                        aria-selected={group?.id === g.id}
                        onClick={() => openGroup(g)}
                        className={
                            "px-2.5 py-1.5 font-semibold whitespace-nowrap border-b-2 -mb-px flex items-center gap-1.5 " +
                            (group?.id === g.id
                                ? "border-blue-400 text-[var(--ada-panel-text)]"
                                : "border-transparent text-gray-400 hover:text-[var(--ada-panel-text)]")
                        }
                    >
                        {/* Contextual only when everything in it is: a group of contextual tabs
                            beside an always-offered one marks those tabs, below, instead. */}
                        {g.tabs.every((t) => t.ctx) && (
                            <span
                                className="w-1.5 h-1.5 rounded-full bg-violet-400"
                                aria-hidden="true"
                            />
                        )}
                        {g.label}
                    </button>
                ))}
            </div>
            {/* ── the group's own tabs, where it has more than one ── */}
            {group && group.tabs.length > 1 && (
                <div
                    className="shrink-0 flex gap-0.5 px-1.5 border-b border-white/10 overflow-x-auto overflow-y-hidden text-xs"
                    role="tablist"
                    aria-label={`${group.label} section`}
                >
                    {group.tabs.map((t) => (
                        <button
                            key={t.id}
                            type="button"
                            role="tab"
                            aria-selected={tab === t.id}
                            onClick={() => openTab(t.id)}
                            className={
                                "px-2 py-1 whitespace-nowrap border-b-2 -mb-px flex items-center gap-1.5 " +
                                (tab === t.id
                                    ? "border-blue-300 text-[var(--ada-panel-text)]"
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
            )}
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
                        {/* ONE section for both kinds of group, each under its own heading: the
                            groups a MODEL carries (its own design/FE sets, read-only and per
                            model), and the viewer's OWN groups, named from a selection across
                            models and saved in the scope (it was a section of its own, "Saved
                            groups"). Not mixed into one list: the two are made and kept apart. */}
                        <CollapsibleSection title="Groups" defaultOpen={!isMobile}>
                            <div className="flex flex-col gap-3">
                                <div className="flex flex-col gap-1">
                                    <div className={GROUP_KIND_HEADING}>In the models</div>
                                    <GroupsSection />
                                </div>
                                {!isMirroredWindow() && (
                                    <div className="flex flex-col gap-1">
                                        <div className={GROUP_KIND_HEADING}>Saved in this scope</div>
                                        <SavedGroupsSection />
                                    </div>
                                )}
                            </div>
                        </CollapsibleSection>
                        {/* Selection boxes beside the groups: both are ways of naming a selection.
                            Folded until opened: most sessions have none. */}
                        <CollapsibleSection title="Selection boxes" defaultOpen={false}>
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
                ) : tab === "measure" ? (
                    <MeasurePanel />
                ) : (
                    // "fem"; the Joints group's tabs are the Connections panel's (`MOVED_SCENE_TABS`).
                    <FemConceptsPanel />
                );

/** The heading of one kind of group inside the Groups section. */
const GROUP_KIND_HEADING = "text-[10px] uppercase tracking-wide text-gray-500";

export default SceneBody;
