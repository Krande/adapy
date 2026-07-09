import React, {Suspense, useEffect, useState} from 'react';
import ObjectInfoBox from "./info_box_selected_object/ObjectInfoBoxComponent";
import SimulationControls from "./simulation/SimulationControls";
import ComponentControls from "./component_view/ComponentControls";
import {useComponentControlsStore} from "@/state/componentControlsStore";
import {useComponentSpecsStore} from "@/state/componentSpecsStore";
import {request_list_of_nodes} from "../utils/node_editor/handlers/request_list_of_nodes";
import ServerInfoBox from "./server_info/ServerInfoBox";
import {runtime} from "@/runtime/config";
import {useViewerStores} from "../state/AdaViewerContext";
// REST-only — code-split so the embedded desktop zip stays slim.
// Scope / user / admin controls now live inside the options drawer
// (RestSection); the menu bar is kept tight so it stays usable on
// phones.
const StorageBrowser = React.lazy(() => import("./storage/StorageBrowser"));
import OptionsComponent from "./OptionsComponent";
import GraphIcon from "./icons/GraphIcon";
import InfoIcon from "./icons/InfoIcon";
import ReloadIcon from "./icons/ReloadIcon";
import ServerIcon from "./icons/ServerIcon";
import ToggleControlsIcon from "./icons/AnimationControlToggle";
import TreeViewIcon from "./icons/TreeViewIcon";
import SceneIcon from "./icons/SceneIcon";
import ComponentIcon from "./icons/ComponentIcon";
import SceneInfoBox from "./info_box_scene/SceneInfoBox";
import {WebsocketStatusMenu, WebsocketStatusBox} from "./WebsocketStatusMenu";


// `md:` Tailwind breakpoint. Match it with matchMedia so the menu can
// react to viewport changes (rotating a tablet, dragging the window
// across breakpoints) without re-architecting around CSS-only rules.
const DESKTOP_QUERY = "(min-width: 768px)";

// Top-bar button styling. Inactive uses a translucent hover (lighter
// than the base) so the button "lifts" on hover. Active uses a darker
// blue + inset shadow so the button reads as "pressed" — distinct from
// hover (lighter) and from the base (mid-tone). Applied to every
// toggle in the bar so the whole row uses one visual vocabulary.
//
// Mobile (<md): fixed 40x40 inline-flex box so every entry takes the
// same horizontal slot — keeps the row inside a 360px viewport even
// at the busiest combo (options + tree + node-editor + storage + info
// + scene + animation + ws status). The 24px SVG icons centre via
// flex; the lone ☰ text glyph keeps its size with font-bold.
//
// Desktop (md+): drop the fixed box in favour of padding-based sizing
// (``py-2 px-4``). Icon buttons end up ~40 % wider than the narrow ☰
// glyph, which reads as a clearer visual rhythm when the row isn't
// space-constrained.
//
// The base class includes ``inline-flex``, which is what gives the
// uniform box on mobile but also outranks the UA stylesheet's
// ``[hidden] { display: none }`` rule in the cascade. ``navBtnClass``
// takes an explicit ``hidden`` boolean and folds in Tailwind's
// ``!hidden`` (display: none !important) when set — that way the
// HTML ``hidden`` attribute doesn't have to fight ``inline-flex``
// and the simulation-controls / node-editor toggles stay properly
// gated on their ``hasAnimation``/``feaSessionActive``/``enableNodeEditor``
// state.
//
// Hover styles are gated with ``pointer-fine:`` (mouse-only) instead
// of plain ``hover:``. ``hover:`` resolves to ``@media (hover: hover)``
// which Android Chrome and many hybrid tablets report TRUE on touch-
// primary devices because the hardware *could* accept a stylus or
// paired mouse — leaving :hover sticky on touch. After tapping a
// button to toggle the menu OFF, the now-inactive button rendered
// the translucent ``bg-blue-700/50`` hover style and looked
// half-pressed. ``pointer: coarse`` is the touch canonical, so
// ``pointer-fine`` is the safe mouse-only gate.
const NAV_BTN_BASE =
    "inline-flex items-center justify-center w-10 h-10 shrink-0 rounded-sm " +
    "md:w-auto md:h-auto md:py-2 md:px-4 md:rounded " +
    "text-white font-bold transition-colors";
const NAV_BTN_INACTIVE = "bg-blue-700 pointer-fine:hover:bg-blue-700/50";
const NAV_BTN_ACTIVE = "bg-blue-900 pointer-fine:hover:bg-blue-800 shadow-inner";

function navBtnClass(active: boolean, extra: string = "", hidden: boolean = false): string {
    const hiddenClass = hidden ? "hidden!" : "";
    return `${NAV_BTN_BASE} ${active ? NAV_BTN_ACTIVE : NAV_BTN_INACTIVE} ${hiddenClass} ${extra}`
        .replace(/\s+/g, " ")
        .trim();
}

function useIsDesktop(): boolean {
    const [isDesktop, setIsDesktop] = useState(
        () => typeof window !== "undefined" && window.matchMedia(DESKTOP_QUERY).matches,
    );
    useEffect(() => {
        const mq = window.matchMedia(DESKTOP_QUERY);
        const onChange = (e: MediaQueryListEvent) => setIsDesktop(e.matches);
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, []);
    return isDesktop;
}

const Menu = () => {
    const stores = useViewerStores();
    const {show_info_box} = stores.useObjectInfoStore();
    const {show_scene_info_box} = stores.useSceneInfoStore();
    const {isNodeEditorVisible, setIsNodeEditorVisible, use_node_editor_only} = stores.useNodeEditorStore();
    const {isOptionsVisible, setIsOptionsVisible, enableNodeEditor} = stores.useOptionsStore(); // use the useNavBarStore function
    const {showServerInfoBox, setShowServerInfoBox} = stores.useServerInfoStore();
    const {hasAnimation, isControlsVisible, setIsControlsVisible} = stores.useAnimationStore();
    const feaSessionActive = stores.useFeaAnimationStore((s) => s.sessionActive);
    const componentControlsVisible = useComponentControlsStore((s) => s.isVisible);
    const toggleComponentControls = useComponentControlsStore((s) => s.toggleVisible);
    // Button only renders when the current scope actually has baked
    // component specs. Auto-refetches on scope change via
    // subscribeSpecsToScope (mounted in AuthGate).
    const componentSpecsAvailable = useComponentSpecsStore((s) => s.hasSpecs);
    const {showInfoBox: showWebsocketInfoBox} = stores.useWebsocketStatusStore();
    const {isTreeCollapsed, setIsTreeCollapsed, treeViewWidth} = stores.useTreeViewStore();
    const isDesktop = useIsDesktop();

    // On desktop the tree panel pushes the menu bar to its right so
    // the buttons aren't hidden behind it. Mobile keeps overlay
    // behaviour — there's no horizontal room to give up, and the user
    // closes the tree to reach the menu anyway.
    const menuShiftPx = !isTreeCollapsed && isDesktop ? treeViewWidth : 0;

    return (
        <div className="relative w-full h-full">
            <div
                className="absolute left-0 top-0 z-10 py-2 gap-2 flex flex-col pointer-events-none transition-[padding] duration-150"
                style={{paddingLeft: `${menuShiftPx}px`}}
            >
                <div className={"flex flex-row items-center gap-2 px-2 max-w-full pointer-events-auto"}>

                    {use_node_editor_only && (
                        <button
                            className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 rounded-sm transition-colors"}
                            onClick={() => request_list_of_nodes()}
                            title="Reload nodes"
                        >
                            <ReloadIcon/>
                        </button>
                    )}

                    <button
                        className={navBtnClass(isOptionsVisible, "", use_node_editor_only)}
                        hidden={use_node_editor_only}
                        onClick={() => setIsOptionsVisible(!isOptionsVisible)}
                        title="Toggle options drawer"
                        aria-pressed={isOptionsVisible}
                    >☰
                    </button>

                    <button
                        className={navBtnClass(!isTreeCollapsed, "", use_node_editor_only)}
                        hidden={use_node_editor_only}
                        onClick={() => setIsTreeCollapsed(!isTreeCollapsed)}
                        title={isTreeCollapsed ? "Show selection tree (Shift+T)" : "Hide selection tree (Shift+T)"}
                        aria-label={isTreeCollapsed ? "Show selection tree" : "Hide selection tree"}
                        aria-pressed={!isTreeCollapsed}
                    >
                        <TreeViewIcon/>
                    </button>

                    <button
                        className={navBtnClass(isNodeEditorVisible, "", use_node_editor_only || !enableNodeEditor)}
                        hidden={use_node_editor_only || !enableNodeEditor}
                        onClick={() => setIsNodeEditorVisible(!isNodeEditorVisible)}
                        title="Toggle node editor"
                        aria-pressed={isNodeEditorVisible}
                    >
                        <GraphIcon/>
                    </button>
                    {runtime.isRestMode() && (
                        <button
                            className={navBtnClass(showServerInfoBox)}
                            onClick={() => setShowServerInfoBox(!showServerInfoBox)}
                            title="Storage"
                            aria-pressed={showServerInfoBox}
                        >
                            <ServerIcon/>
                        </button>
                    )}
                    <button
                        className={navBtnClass(show_info_box, "", use_node_editor_only)}
                        hidden={use_node_editor_only}
                        onClick={stores.useObjectInfoStore.getState().toggle}
                        title="Toggle object info"
                        aria-pressed={show_info_box}
                    ><InfoIcon/></button>
                    <button
                        className={navBtnClass(show_scene_info_box, "", use_node_editor_only)}
                        hidden={use_node_editor_only}
                        onClick={stores.useSceneInfoStore.getState().toggle}
                        title="Toggle scene info"
                        aria-label="Toggle scene info"
                        aria-pressed={show_scene_info_box}
                    ><SceneIcon/></button>

                    <button
                        className={navBtnClass(isControlsVisible, "", !hasAnimation && !feaSessionActive)}
                        hidden={!hasAnimation && !feaSessionActive}
                        onClick={() => setIsControlsVisible(!isControlsVisible)}
                        title="Toggle animation controls"
                        aria-pressed={isControlsVisible}
                    ><ToggleControlsIcon/></button>
                    <button
                        className={navBtnClass(
                            componentControlsVisible,
                            "",
                            use_node_editor_only || !componentSpecsAvailable,
                        )}
                        hidden={use_node_editor_only || !componentSpecsAvailable}
                        onClick={toggleComponentControls}
                        title="Toggle connection-component panel"
                        aria-label="Toggle connection-component panel"
                        aria-pressed={componentControlsVisible}
                    ><ComponentIcon/></button>
                    {!runtime.isRestMode() && (
                        <div
                            className={navBtnClass(showWebsocketInfoBox)}>
                            <WebsocketStatusMenu/>
                        </div>
                    )}
                </div>
                <div className={"px-2 gap-2 flex flex-col pointer-events-auto max-w-[100vw]"}>
                    {isOptionsVisible && <OptionsComponent/>}
                    {showServerInfoBox && (
                        runtime.isRestMode()
                            ? <Suspense fallback={null}><StorageBrowser/></Suspense>
                            : <ServerInfoBox/>
                    )}
                    {show_info_box && <ObjectInfoBox/>}
                    {show_scene_info_box && <SceneInfoBox/>}
                    {showWebsocketInfoBox && <WebsocketStatusBox/>}
                    {isControlsVisible && <SimulationControls/>}
                    {componentControlsVisible && <ComponentControls/>}
                </div>
            </div>
        </div>
    );
}

export default Menu;