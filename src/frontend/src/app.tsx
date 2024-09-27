// src/App.tsx
import "./app.css";
import React from 'react'
import CanvasComponent from './components/viewer/CanvasComponent';
import Menu from './components/Menu';
import OptionsComponent from './components/OptionsComponent';
import {useOptionsStore} from './state/optionsStore';

import ResizableTreeView from './components/tree_view/ResizableTreeView';
import {useNodeEditorStore} from "./state/nodeEditorStore";
import NodeEditorComponent from "./components/NodeEditorComponent"; // Import the new component


function App() {
    const {isOptionsVisible} = useOptionsStore(); // use the useNavBarStore function
    const {isNodeEditorVisible} = useNodeEditorStore();

    return (
        <div className={"relative flex flex-row h-full w-full bg-gray-900"}>
            {/* Tree View Section */}
            <div className={"relative h-full"}>
                <ResizableTreeView/>
            </div>

            <div className={"relative top-0 left-0"}>
                <Menu/>
            </div>

            <div className={"w-full h-full"}>
                <CanvasComponent/>
            </div>

            {/* Only render NodeEditorComponent if it's visible */}
            {isNodeEditorVisible && <NodeEditorComponent/>}

            {/* Only render NavBar if it's visible */}
            {isOptionsVisible && (
                <OptionsComponent/>
            )}

        </div>
    );
}

export default App;