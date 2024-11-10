import React from 'react';
import ObjectInfoBox from "./object_info_box/ObjectInfoBoxComponent";
import {useObjectInfoStore} from "../state/objectInfoStore";
import AnimationControls from "./viewer/AnimationControls";
import {useNodeEditorStore} from "../state/useNodeEditorStore";
import {useAnimationStore} from "../state/animationStore";
import {useOptionsStore} from "../state/optionsStore";
import {useTreeViewStore} from "../state/treeViewStore";
import {request_list_of_nodes} from "../utils/node_editor/request_list_of_nodes";
import {useServerInfoStore} from "../state/serverInfoStore";
import ServerInfoBox from "./server_info/ServerInfoBox";

const graph_btn_svg = <svg width="24px" height="24px" strokeWidth="1.5" viewBox="0 0 24 24" fill="none"
                           xmlns="http://www.w3.org/2000/svg" color="currentColor">
    <rect width="7" height="5" rx="0.6" transform="matrix(0 -1 -1 0 22 21)" stroke="currentColor"
          strokeWidth="1.5"></rect>
    <rect width="7" height="5" rx="0.6" transform="matrix(0 -1 -1 0 7 15.5)" stroke="currentColor"
          strokeWidth="1.5"></rect>
    <rect width="7" height="5" rx="0.6" transform="matrix(0 -1 -1 0 22 10)" stroke="currentColor"
          strokeWidth="1.5"></rect>
    <path d="M17 17.5H13.5C12.3954 17.5 11.5 16.6046 11.5 15.5V8.5C11.5 7.39543 12.3954 6.5 13.5 6.5H17"
          stroke="currentColor" strokeWidth="1.5"></path>
    <path d="M11.5 12H7" stroke="currentColor" strokeWidth="1.5"></path>
</svg>
const info_svg = <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5}
                      stroke="currentColor" className="w-6 h-6">
    <path strokeLinecap="round" strokeLinejoin="round"
          d="m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"/>
</svg>
const tree_view = <svg fill="currentColor" width="24px" height="24px" viewBox="0 0 36 36" version="1.1"
                       preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"
>
    <title>tree-view-line</title>
    <path d="M15,32H11a1,1,0,0,1-1-1V27a1,1,0,0,1,1-1h4a1,1,0,0,1,1,1v4A1,1,0,0,1,15,32Zm-3-2h2V28H12Z"
          className="clr-i-outline clr-i-outline-path-1"></path>
    <path
        d="M15,16H11a1,1,0,0,0-1,1v1.2H5.8V12H7a1,1,0,0,0,1-1V7A1,1,0,0,0,7,6H3A1,1,0,0,0,2,7v4a1,1,0,0,0,1,1H4.2V29.8h6.36a.8.8,0,0,0,0-1.6H5.8V19.8H10V21a1,1,0,0,0,1,1h4a1,1,0,0,0,1-1V17A1,1,0,0,0,15,16ZM4,8H6v2H4ZM14,20H12V18h2Z"
        className="clr-i-outline clr-i-outline-path-2"></path>
    <path d="M34,9a1,1,0,0,0-1-1H10v2H33A1,1,0,0,0,34,9Z" className="clr-i-outline clr-i-outline-path-3"></path>
    <path d="M33,18H18v2H33a1,1,0,0,0,0-2Z" className="clr-i-outline clr-i-outline-path-4"></path>
    <path d="M33,28H18v2H33a1,1,0,0,0,0-2Z" className="clr-i-outline clr-i-outline-path-5"></path>
    <rect x="0" y="0" width="36" height="36" fillOpacity="0"/>
</svg>

const update_icon = <svg width="24px" height="24px" viewBox="0 0 15 15" fill="none"
                         xmlns="http://www.w3.org/2000/svg">
    <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M1.90321 7.29677C1.90321 10.341 4.11041 12.4147 6.58893 12.8439C6.87255 12.893 7.06266 13.1627 7.01355 13.4464C6.96444 13.73 6.69471 13.9201 6.41109 13.871C3.49942 13.3668 0.86084 10.9127 0.86084 7.29677C0.860839 5.76009 1.55996 4.55245 2.37639 3.63377C2.96124 2.97568 3.63034 2.44135 4.16846 2.03202L2.53205 2.03202C2.25591 2.03202 2.03205 1.80816 2.03205 1.53202C2.03205 1.25588 2.25591 1.03202 2.53205 1.03202L5.53205 1.03202C5.80819 1.03202 6.03205 1.25588 6.03205 1.53202L6.03205 4.53202C6.03205 4.80816 5.80819 5.03202 5.53205 5.03202C5.25591 5.03202 5.03205 4.80816 5.03205 4.53202L5.03205 2.68645L5.03054 2.68759L5.03045 2.68766L5.03044 2.68767L5.03043 2.68767C4.45896 3.11868 3.76059 3.64538 3.15554 4.3262C2.44102 5.13021 1.90321 6.10154 1.90321 7.29677ZM13.0109 7.70321C13.0109 4.69115 10.8505 2.6296 8.40384 2.17029C8.12093 2.11718 7.93465 1.84479 7.98776 1.56188C8.04087 1.27898 8.31326 1.0927 8.59616 1.14581C11.4704 1.68541 14.0532 4.12605 14.0532 7.70321C14.0532 9.23988 13.3541 10.4475 12.5377 11.3662C11.9528 12.0243 11.2837 12.5586 10.7456 12.968L12.3821 12.968C12.6582 12.968 12.8821 13.1918 12.8821 13.468C12.8821 13.7441 12.6582 13.968 12.3821 13.968L9.38205 13.968C9.10591 13.968 8.88205 13.7441 8.88205 13.468L8.88205 10.468C8.88205 10.1918 9.10591 9.96796 9.38205 9.96796C9.65819 9.96796 9.88205 10.1918 9.88205 10.468L9.88205 12.3135L9.88362 12.3123C10.4551 11.8813 11.1535 11.3546 11.7585 10.6738C12.4731 9.86976 13.0109 8.89844 13.0109 7.70321Z"
        fill="#ffffff"
    />
</svg>
const server_icon = <svg width="24px" height="24px" strokeWidth="1.5" viewBox="0 0 24 24" fill="none"
                         xmlns="http://www.w3.org/2000/svg" color="currentColor">
    <path d="M3 19H12M21 19H12M12 19V13M12 13H18V5H6V13H12Z" stroke="currentColor" strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"></path>
    <path d="M9 9.01L9.01 8.99889" stroke="#000000" strokeWidth="1.5" strokeLinecap="round"
          strokeLinejoin="round"></path>
    <path d="M12 9.01L12.01 8.99889" stroke="#000000" strokeWidth="1.5" strokeLinecap="round"
          strokeLinejoin="round"></path>
</svg>

const Menu = () => {
    const {show_info_box} = useObjectInfoStore();
    const {isNodeEditorVisible, setIsNodeEditorVisible, use_node_editor_only} = useNodeEditorStore();
    const {hasAnimation} = useAnimationStore();
    const {isOptionsVisible, setIsOptionsVisible} = useOptionsStore(); // use the useNavBarStore function
    const {isCollapsed, setIsCollapsed} = useTreeViewStore();
    const {showServerInfoBox, setShowServerInfoBox} = useServerInfoStore();

    return (
        <div className="relative w-full h-full">
            <div className="absolute left-0 top-0 z-10 py-2 flex flex-col">
                <div className={"w-full h-full flex flex-row"}>
                    {use_node_editor_only && (
                        <button
                            className={"flex relative bg-blue-700 hover:bg-blue-700/50 text-white p-1 ml-2 rounded"}
                            onClick={() => request_list_of_nodes()}
                        >
                            {update_icon}
                        </button>
                    )}

                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                        hidden={use_node_editor_only}
                        onClick={() => setIsOptionsVisible(!isOptionsVisible)}
                    >☰
                    </button>
                    <button
                        className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"
                        hidden={use_node_editor_only}
                        onClick={() => setIsCollapsed(!isCollapsed)}
                    >
                        {tree_view}
                    </button>

                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                        hidden={use_node_editor_only}
                        onClick={() => setIsNodeEditorVisible(!isNodeEditorVisible)}
                    >
                        {graph_btn_svg}
                    </button>
                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                        hidden={use_node_editor_only}
                        onClick={() => setShowServerInfoBox(!showServerInfoBox)}
                    >
                        {server_icon}
                    </button>
                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                        hidden={use_node_editor_only}
                        onClick={useObjectInfoStore.getState().toggle}
                    >{info_svg}</button>

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