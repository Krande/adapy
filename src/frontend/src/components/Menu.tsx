import React from 'react';
import {toggle_info_panel} from "../utils/info_panel_utils";
import ObjectInfoBox from "./object_info_box/ObjectInfoBoxComponent";
import {useObjectInfoStore} from "../state/objectInfoStore";
import AnimationControls from "./viewer/AnimationControls";
import {useNodeEditorStore} from "../state/useNodeEditorStore";
import {useAnimationStore} from "../state/animationStore";
import {useOptionsStore} from "../state/optionsStore";
import {useTreeViewStore} from "../state/treeViewStore";

const graph_btn_svg = <svg width="24px" height="24px" strokeWidth="1.5" viewBox="0 0 24 24" fill="none"
                           xmlns="http://www.w3.org/2000/svg" color="currentColor">
    <rect width="7" height="5" rx="0.6" transform="matrix(0 -1 -1 0 22 21)" stroke="currentColor" strokeWidth="1.5"></rect>
    <rect width="7" height="5" rx="0.6" transform="matrix(0 -1 -1 0 7 15.5)" stroke="currentColor" strokeWidth="1.5"></rect>
    <rect width="7" height="5" rx="0.6" transform="matrix(0 -1 -1 0 22 10)" stroke="currentColor" strokeWidth="1.5"></rect>
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

const Menu = () => {
    const {show_info_box} = useObjectInfoStore();
    const {isNodeEditorVisible, setIsNodeEditorVisible} = useNodeEditorStore();
    const {hasAnimation} = useAnimationStore();
    const {isOptionsVisible, setIsOptionsVisible} = useOptionsStore(); // use the useNavBarStore function
    const {isCollapsed, setIsCollapsed} = useTreeViewStore();

    return (
        <div className="relative w-full h-full">
            <div className="absolute left-0 top-0 z-10 py-2 flex flex-col">
                <div className={"w-full h-full flex flex-row"}>

                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                        onClick={() => setIsOptionsVisible(!isOptionsVisible)}
                    >☰
                    </button>
                    <button
                        className="bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"
                        onClick={() => setIsCollapsed(!isCollapsed)}
                    >
                        {tree_view}
                    </button>

                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                        onClick={() => setIsNodeEditorVisible(!isNodeEditorVisible)}
                    >
                        {graph_btn_svg}
                    </button>
                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 px-4 ml-2 rounded"}
                        onClick={toggle_info_panel}
                    >{info_svg}</button>
                    <div className="relative">
                        {hasAnimation && <AnimationControls/>}
                    </div>

                </div>
                {show_info_box && <ObjectInfoBox/>}
            </div>
        </div>
    );
}

export default Menu;