import React from 'react';
import ObjectInfoBox from "./object_info_box/ObjectInfoBoxComponent";
import {useObjectInfoStore} from "../state/objectInfoStore";
import AnimationControls from "./viewer/AnimationControls";
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


const Menu = () => {
    const {show_info_box} = useObjectInfoStore();
    const {isNodeEditorVisible, setIsNodeEditorVisible, use_node_editor_only} = useNodeEditorStore();
    const {isOptionsVisible, setIsOptionsVisible} = useOptionsStore(); // use the useNavBarStore function
    const {isTreeCollapsed, setIsTreeCollapsed} = useTreeViewStore();
    const {showServerInfoBox, setShowServerInfoBox} = useServerInfoStore();
    const {hasAnimation} = useAnimationStore();

    return (
        <div className="relative w-full h-full">
            <div className="absolute left-0 top-0 z-10 py-2 flex flex-col">
                <div className={"flex flex-row items-center gap-2 px-2 max-w-full"}>
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
                        hidden={use_node_editor_only}
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

                    <div className="relative">
                        {hasAnimation && <AnimationControls/>}
                    </div>
                </div>
                {showServerInfoBox && <ServerInfoBox/>}
                {show_info_box && <ObjectInfoBox/>}

            </div>
        </div>
    );
}

export default Menu;