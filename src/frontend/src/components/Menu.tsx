import React from 'react';
import ObjectInfoBox from "./info_box_selected_object/ObjectInfoBoxComponent";
import {useObjectInfoStore} from "../state/objectInfoStore";
import SimulationControls from "./simulation/SimulationControls";
import {useNodeEditorStore} from "../state/useNodeEditorStore";
import {useAnimationStore} from "../state/animationStore";
import {useOptionsStore} from "../state/optionsStore";
import {useTreeViewStore} from "../state/treeViewStore";
import {request_list_of_nodes} from "../utils/node_editor/comms/request_list_of_nodes";
import {useServerInfoStore} from "../state/serverInfoStore";
import ServerInfoBox from "./server_info/ServerInfoBox";
import GraphIcon from "./icons/GraphIcon";
import InfoIcon from "./icons/InfoIcon";
import TreeViewIcon from "./icons/TreeViewIcon";
import ReloadIcon from "./icons/ReloadIcon";
import ServerIcon from "./icons/ServerIcon";
import ToggleControlsIcon from "./icons/AnimationControlToggle";
import {useGroupInfoStore} from "../state/groupInfoStore";
import GroupIcon from "./icons/GroupIcon";
import GroupInfoBox from "./info_box_groups/GroupInfoBox";


const Menu = () => {
    const {show_info_box} = useObjectInfoStore();
    const {show_group_info_box} = useGroupInfoStore();
    const {isNodeEditorVisible, setIsNodeEditorVisible, use_node_editor_only} = useNodeEditorStore();
    const {isOptionsVisible, setIsOptionsVisible, enableNodeEditor} = useOptionsStore(); // use the useNavBarStore function
    const {isTreeCollapsed, setIsTreeCollapsed} = useTreeViewStore();
    const {showServerInfoBox, setShowServerInfoBox} = useServerInfoStore();
    const {hasAnimation, isControlsVisible, setIsControlsVisible} = useAnimationStore();

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
                        className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"
                        hidden={use_node_editor_only}
                        onClick={() => setIsTreeCollapsed(!isTreeCollapsed)}
                    >
                        <TreeViewIcon/>
                    </button>

                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                        hidden={use_node_editor_only || !enableNodeEditor}
                        onClick={() => setIsNodeEditorVisible(!isNodeEditorVisible)}
                    >
                        <GraphIcon/>
                    </button>
                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 rounded"}
                        // hidden={use_node_editor_only}
                        hidden={true}
                        onClick={() => setShowServerInfoBox(!showServerInfoBox)}
                    >
                        <ServerIcon/>
                    </button>
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

                </div>
                <div className={"px-2 gap-2 flex flex-col"}>
                    {showServerInfoBox && <ServerInfoBox/>}
                    {show_info_box && <ObjectInfoBox/>}
                    {show_group_info_box && <GroupInfoBox/>}
                    {isControlsVisible && <SimulationControls/>}
                </div>
            </div>
        </div>
    );
}

export default Menu;