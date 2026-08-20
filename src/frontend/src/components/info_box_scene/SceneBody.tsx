import React from "react";
import {CollapsibleSection, Tabs} from "@/components/ui";
import {useSceneInfoStore, type SceneInfoMode} from "@/state/sceneInfoStore";
import {shouldStackTabs} from "@/shell/tabArrangement";
import LoadedModelsSection from "./LoadedModelsSection";
import SourceSection from "./SourceSection";
import StatsSection from "./StatsSection";
import ModelStatsSection from "./ModelStatsSection";
import GroupsSection from "./GroupsSection";
import UtilitiesSection from "./UtilitiesSection";
import FaceSearchSection from "./FaceSearchSection";
import FacePickingToggle from "./FacePickingToggle";
import SectionPlanesPanel from "./SectionPlanesPanel";
import MeshDistortionSection from "./MeshDistortionSection";
import FemConceptsPanel from "./FemConceptsPanel";
import JointsOverviewPanel from "./JointsOverviewPanel";

// The Scene panel's content, with no chrome of its own.
//
// Extracted from SceneInfoBox for the same reason OptionsBody was extracted from
// OptionsComponent: the shell's dock draws the frame, so a panel that draws its own
// produces a box inside a box with two scrollbars. SceneInfoBox is now the classic UI's
// float / bottom-sheet wrapper around this, and goes at cutover.
//
// It also decides between two arrangements of its six groups — see below.

export type SceneTab = "model" | "tools" | "clip" | "mesh" | "fem" | "joints";

export const TAB_META: {id: SceneTab; label: string; ctx?: boolean}[] = [
    {id: "model", label: "Model"},
    {id: "tools", label: "Tools"},
    {id: "clip", label: "Clip"},
    {id: "mesh", label: "Mesh"},
    {id: "fem", label: "FEM", ctx: true},
    {id: "joints", label: "Joints", ctx: true},
];

export const MODE_TO_TAB: Record<SceneInfoMode, SceneTab> = {
    info: "model",
    source: "model",
    utilities: "tools",
    section: "clip",
    mesh: "mesh",
    fem: "fem",
    joints: "joints",
};

export const TAB_TO_MODE: Record<SceneTab, SceneInfoMode> = {
    model: "info",
    tools: "utilities",
    clip: "section",
    mesh: "mesh",
    fem: "fem",
    joints: "joints",
};

/** One group's content. Identical in both arrangements — only the container changes. */
function TabContent({tab, isMobile}: {tab: SceneTab; isMobile: boolean}) {
    switch (tab) {
        case "model":
            return (
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
            );
        case "tools":
            return <UtilitiesSection />;
        case "clip":
            return <SectionPlanesPanel />;
        case "mesh":
            return <MeshDistortionSection />;
        case "joints":
            return <JointsOverviewPanel />;
        case "fem":
            return <FemConceptsPanel />;
    }
}

export interface SceneBodyProps {
    /** Drives the Groups section's default state on a phone. */
    isMobile?: boolean;
    /** Which context-only tabs have anything to show. */
    ctxAvailable: Partial<Record<SceneTab, boolean>>;
}

export default function SceneBody({isMobile = false, ctxAvailable}: SceneBodyProps) {
    const mode = useSceneInfoStore((s) => s.mode);
    const setMode = useSceneInfoStore((s) => s.setMode);

    const tabs = TAB_META.filter((t) => !t.ctx || ctxAvailable[t.id]);

    // Fall back to Model when the stored mode names a tab that is not currently
    // available — e.g. FEM after the FE model was unloaded.
    let tab = MODE_TO_TAB[mode];
    if (!tabs.some((t) => t.id === tab)) tab = "model";

    // Tabs when short, a column of disclosures when tall.
    //
    // Six labels in a strip admit one group at a time, and in a narrow panel the strip
    // itself scrolls, so some labels are not even visible. Given the height, a column
    // shows every heading at once and lets you open two together — which is the whole
    // point when you are comparing take-off against groups.
    const bodyRef = React.useRef<HTMLDivElement | null>(null);
    const [stacked, setStacked] = React.useState(false);
    // Measure once, synchronously, before paint — then keep the observer for changes.
    //
    // The observer alone is not enough. Its first callback is delivered on the frame
    // pipeline, which the browser suspends for a hidden tab (and throttles under load),
    // so a panel opened in a background tab would sit in the DEFAULT arrangement until
    // something happened to resize it. A layout effect runs regardless of visibility, so
    // the first arrangement is right from the first paint.
    React.useLayoutEffect(() => {
        const el = bodyRef.current;
        if (!el) return;
        const measure = () =>
            setStacked((wasStacked) =>
                shouldStackTabs({tabCount: tabs.length, heightPx: el.clientHeight, wasStacked}),
            );
        measure();
        if (typeof ResizeObserver === "undefined") return;
        const ro = new ResizeObserver(measure);
        ro.observe(el);
        return () => ro.disconnect();
    }, [tabs.length]);

    return (
        <div className="flex min-h-0 flex-1 flex-col">
            {/* Every loaded model, visible in every arrangement — toggling or unloading a
                model should never mean digging through a tab first. Capped so a long list
                cannot swallow the panel. */}
            <div className="max-h-40 shrink-0 overflow-y-auto scrollbar px-2.5">
                <CollapsibleSection title="Loaded models" defaultOpen>
                    <LoadedModelsSection />
                </CollapsibleSection>
            </div>

            {!stacked && (
                <div className="shrink-0 px-1.5">
                    <Tabs
                        label="Scene panel section"
                        value={tab}
                        onChange={(id) => setMode(TAB_TO_MODE[id as SceneTab])}
                        items={tabs.map((t) => ({id: t.id, label: t.label, contextual: t.ctx}))}
                    />
                </div>
            )}

            <div
                ref={bodyRef}
                data-arrangement={stacked ? "stacked" : "tabbed"}
                className="min-h-0 flex-1 overflow-y-auto scrollbar px-2.5 py-2"
            >
                {stacked ? (
                    <div className="flex flex-col">
                        {tabs.map((t) => (
                            <CollapsibleSection
                                key={t.id}
                                title={t.label}
                                // The group the store points at opens; the rest stay shut.
                                // So a deep link or a toolbar action that sets the mode
                                // still lands you on the right group, in both arrangements.
                                defaultOpen={t.id === tab}
                            >
                                <TabContent tab={t.id} isMobile={isMobile} />
                            </CollapsibleSection>
                        ))}
                    </div>
                ) : (
                    <TabContent tab={tab} isMobile={isMobile} />
                )}
            </div>
        </div>
    );
}
