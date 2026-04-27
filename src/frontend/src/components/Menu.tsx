import React, {Suspense} from 'react';
import ObjectInfoBox from "./info_box_selected_object/ObjectInfoBoxComponent";
import {useObjectInfoStore} from "../state/objectInfoStore";
import SimulationControls from "./simulation/SimulationControls";
import {useNodeEditorStore} from "../state/useNodeEditorStore";
import {useAnimationStore} from "../state/animationStore";
import {useOptionsStore} from "../state/optionsStore";
import {request_list_of_nodes} from "../utils/node_editor/handlers/request_list_of_nodes";
import {useServerInfoStore} from "../state/serverInfoStore";
import ServerInfoBox from "./server_info/ServerInfoBox";
import {runtime} from "@/runtime/config";
// REST-only — code-split so the embedded desktop zip stays slim.
const StorageBrowser = React.lazy(() => import("./storage/StorageBrowser"));
const UserMenu = React.lazy(() => import("./auth/UserMenu"));
import GraphIcon from "./icons/GraphIcon";
import InfoIcon from "./icons/InfoIcon";
import ReloadIcon from "./icons/ReloadIcon";
import ServerIcon from "./icons/ServerIcon";
import ToggleControlsIcon from "./icons/AnimationControlToggle";
import {useGroupInfoStore} from "../state/groupInfoStore";
import GroupIcon from "./icons/GroupIcon";
import GroupInfoBox from "./info_box_groups/GroupInfoBox";
import {WebsocketStatusMenu, WebsocketStatusBox} from "./WebsocketStatusMenu";
import {useWebsocketStatusStore} from "../state/websocketStatusStore";


const Menu = () => {
    const {show_info_box} = useObjectInfoStore();
    const {show_group_info_box} = useGroupInfoStore();
    const {isNodeEditorVisible, setIsNodeEditorVisible, use_node_editor_only} = useNodeEditorStore();
    const {isOptionsVisible, setIsOptionsVisible, enableNodeEditor} = useOptionsStore(); // use the useNavBarStore function
    const {showServerInfoBox, setShowServerInfoBox} = useServerInfoStore();
    const {hasAnimation, isControlsVisible, setIsControlsVisible} = useAnimationStore();
    const {showInfoBox: showWebsocketInfoBox} = useWebsocketStatusStore();

    return (
        <div className="relative w-full h-full">
            <div className="absolute left-0 top-0 z-10 py-2 gap-2 flex flex-col pointer-events-none">
                <div className={"flex flex-row items-center gap-2 px-2 max-w-full pointer-events-auto"}>

                    {use_node_editor_only && (
                        <button
                            className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 rounded"}
                            onClick={() => request_list_of_nodes()}
                        >
                            <ReloadIcon/>
                        </button>
                    )}

                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                        hidden={use_node_editor_only}
                        onClick={() => setIsOptionsVisible(!isOptionsVisible)}
                    >☰
                    </button>

                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                        hidden={use_node_editor_only || !enableNodeEditor}
                        onClick={() => setIsNodeEditorVisible(!isNodeEditorVisible)}
                    >
                        <GraphIcon/>
                    </button>
                    {runtime.isRestMode() && (
                        <button
                            className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                            onClick={() => setShowServerInfoBox(!showServerInfoBox)}
                            title="Storage"
                        >
                            <ServerIcon/>
                        </button>
                    )}
                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                        hidden={use_node_editor_only}
                        onClick={useObjectInfoStore.getState().toggle}
                    ><InfoIcon/></button>
                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                        hidden={use_node_editor_only}
                        onClick={useGroupInfoStore.getState().toggle}
                    ><GroupIcon/></button>

                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                        hidden={!hasAnimation}
                        onClick={() => setIsControlsVisible(!isControlsVisible)}
                    ><ToggleControlsIcon/></button>
                    {!runtime.isRestMode() && (
                        <div
                            className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}>
                            <WebsocketStatusMenu/>
                        </div>
                    )}
                    {runtime.isRestMode() && (
                        <Suspense fallback={null}>
                            <UserMenu/>
                        </Suspense>
                    )}

                </div>
                <div className={"px-2 gap-2 flex flex-col pointer-events-auto max-w-[100vw]"}>
                    {showServerInfoBox && (
                        runtime.isRestMode()
                            ? <Suspense fallback={null}><StorageBrowser/></Suspense>
                            : <ServerInfoBox/>
                    )}
                    {show_info_box && <ObjectInfoBox/>}
                    {show_group_info_box && <GroupInfoBox/>}
                    {showWebsocketInfoBox && <WebsocketStatusBox/>}
                    {isControlsVisible && <SimulationControls/>}
                </div>
            </div>
        </div>
    );
}

export default Menu;