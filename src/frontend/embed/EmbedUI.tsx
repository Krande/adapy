// Slim viewer UI for the `mountViewer({showControls:true})` path. Lifts
// the three panels paradoc actually wants — selection tree, object
// info, group info — without dragging in adapy's full Menu.tsx (REST
// storage browser, websocket status, server info, options drawer all
// gated by runtime flags that don't exist in the embed).
//
// The three toggle buttons mirror Menu.tsx's styling so the embed
// looks like a stripped-down adapy and not a separate UI. Buttons
// flip the same zustand stores Menu does, so a click in the tree
// here populates the same object-info store the full app uses.

import React from "react"
import ResizableTreeView from "@/components/tree_view/ResizableTreeView"
import ObjectInfoBox from "@/components/info_box_selected_object/ObjectInfoBoxComponent"
import SceneInfoBox from "@/components/info_box_scene/SceneInfoBox"
import SimulationControls from "@/components/simulation/SimulationControls"
import TreeViewIcon from "@/components/icons/TreeViewIcon"
import InfoIcon from "@/components/icons/InfoIcon"
import SceneIcon from "@/components/icons/SceneIcon"
import ToggleControlsIcon from "@/components/icons/AnimationControlToggle"
import { useViewerStores } from "@/state/AdaViewerContext"

// ``pointer-fine:`` gate on hover styles (same as Menu.tsx): plain
// ``hover:`` resolves to ``@media (hover: hover)`` which Android
// Chrome reports TRUE on touch devices, leaving :hover sticky after a
// tap. The sticky translucent ``bg-blue-700/50`` made an
// already-deactivated toolbar button look half-pressed.
// ``pointer-fine`` matches mouse / trackpad only.
const BTN_BASE =
    "inline-flex items-center justify-center w-10 h-10 shrink-0 " +
    "text-white font-bold rounded transition-colors"
const BTN_INACTIVE = "bg-blue-700 pointer-fine:hover:bg-blue-700/50"
const BTN_ACTIVE = "bg-blue-900 pointer-fine:hover:bg-blue-800 shadow-inner"

function btnClass(active: boolean): string {
    return `${BTN_BASE} ${active ? BTN_ACTIVE : BTN_INACTIVE}`
}

export const EmbedUI: React.FC = () => {
    const {
        useObjectInfoStore,
        useSceneInfoStore,
        useTreeViewStore,
        useAnimationStore,
        useFeaAnimationStore,
    } = useViewerStores()
    const showInfo = useObjectInfoStore((s: any) => s.show_info_box)
    const toggleInfo = useObjectInfoStore.getState().toggle
    const showScene = useSceneInfoStore((s: any) => s.show_scene_info_box)
    const toggleScene = useSceneInfoStore.getState().toggle
    const { isTreeCollapsed, setIsTreeCollapsed, treeViewWidth } = useTreeViewStore()

    // Simulation controls (mode-shape selector, deformation scale,
    // play/pause). The button + panel only surface when there's
    // actually animation data — for paradoc-embed that's a FEA mode
    // shape baked into the GLB (clip animation path) or a live
    // streaming FEA session if/when the embed grows that capability.
    // Mirrors Menu.tsx's `!hasAnimation && !feaSessionActive` hide.
    const hasAnimation = useAnimationStore((s: any) => s.hasAnimation)
    const isControlsVisible = useAnimationStore((s: any) => s.isControlsVisible)
    const setIsControlsVisible = useAnimationStore.getState().setIsControlsVisible
    const feaSessionActive = useFeaAnimationStore((s: any) => s.sessionActive)
    const animPanelAvailable = hasAnimation || feaSessionActive

    // Push the toolbar right when the drawer is open so the buttons
    // stay visible. Matches Menu.tsx's behavior on desktop; mobile
    // overlays the tree anyway so the shift is a no-op there in
    // practice (the user closes the tree to reach the toolbar).
    const shiftPx = !isTreeCollapsed ? treeViewWidth : 0

    // The panels below the toolbar get the viewer's height, not the
    // window's: a 3D figure in a document is a few hundred pixels tall, and
    // a panel capped at 70% of the window ran off its bottom. Handed to the
    // simulation panel as ``--ada-sim-max-h`` (its scroll area's cap).
    const rootRef = React.useRef<HTMLDivElement | null>(null)
    const [panelMaxH, setPanelMaxH] = React.useState<number | null>(null)
    React.useEffect(() => {
        const el = rootRef.current
        if (!el) return
        const ro = new ResizeObserver(() => {
            // Toolbar row (40 px + 8 px padding top and gap) and the panel's
            // own header (~32 px) come off the top; 8 px of air at the bottom.
            setPanelMaxH(Math.max(80, el.clientHeight - 56 - 32 - 8))
        })
        ro.observe(el)
        return () => ro.disconnect()
    }, [])

    return (
        <div ref={rootRef} className="absolute inset-0 pointer-events-none">
            {/* The tree drawer is the only child here that *isn't* in
                the toolbar column; it needs its own pointer-events-auto
                opt-in or every click in it (close ×, node selection,
                scroll) is swallowed by the inert backdrop above.
                Standalone (app.tsx) renders it under a normal parent
                so no opt-in is needed there. */}
            <div className="pointer-events-auto">
                <ResizableTreeView />
            </div>
            {/* Spans the viewer (inset-x-0) so the panels' ``100%`` caps mean the
                viewer's width. Only the controls themselves take pointer events;
                the empty stretch around them leaves the model draggable. */}
            <div
                className="absolute inset-x-0 top-0 z-10 py-2 flex flex-col gap-2 pointer-events-none transition-[padding] duration-150"
                style={{ paddingLeft: `${shiftPx}px` }}
            >
                <div className="self-start flex flex-row items-center gap-2 px-2 pointer-events-auto">
                    <button
                        type="button"
                        className={btnClass(!isTreeCollapsed)}
                        onClick={() => setIsTreeCollapsed(!isTreeCollapsed)}
                        title={isTreeCollapsed ? "Show selection tree" : "Hide selection tree"}
                        aria-pressed={!isTreeCollapsed}
                    >
                        <TreeViewIcon />
                    </button>
                    <button
                        type="button"
                        className={btnClass(showInfo)}
                        onClick={toggleInfo}
                        title="Toggle object info"
                        aria-pressed={showInfo}
                    >
                        <InfoIcon />
                    </button>
                    <button
                        type="button"
                        className={btnClass(showScene)}
                        onClick={toggleScene}
                        title="Toggle scene info"
                        aria-pressed={showScene}
                    >
                        <SceneIcon />
                    </button>
                    {animPanelAvailable && (
                        <button
                            type="button"
                            className={btnClass(isControlsVisible)}
                            onClick={() => setIsControlsVisible(!isControlsVisible)}
                            title="Toggle simulation controls"
                            aria-pressed={isControlsVisible}
                        >
                            <ToggleControlsIcon />
                        </button>
                    )}
                </div>
                <div
                    className="px-2 flex flex-col items-start gap-2 pointer-events-none min-w-0 [&>*]:pointer-events-auto [&>*]:max-w-full"
                    style={panelMaxH ? ({ "--ada-sim-max-h": `${panelMaxH}px` } as React.CSSProperties) : undefined}
                >
                    {showInfo && <ObjectInfoBox />}
                    {showScene && <SceneInfoBox />}
                    {animPanelAvailable && isControlsVisible && <SimulationControls />}
                </div>
            </div>
        </div>
    )
}
