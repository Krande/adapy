// src/App.tsx
import "./app.css";
import React, { useEffect } from 'react'
import CanvasWrapper from './components/viewer/CanvasWrapper';
import Menu from './components/Menu';
import OptionsComponent from './components/OptionsComponent';
import {useOptionsStore} from './state/optionsStore';

import ResizableTreeView from './components/tree_view/ResizableTreeView';
import {useNodeEditorStore} from "./state/useNodeEditorStore";
import NodeEditorComponent from "./components/node_editor/NodeEditorComponent";


function App() {
    const {isOptionsVisible} = useOptionsStore(); // use the useNavBarStore function
    const {isNodeEditorVisible, use_node_editor_only} = useNodeEditorStore();
    useEffect(() => {
        // Check if running inside a Jupyter Notebook
        if ((window as any).Jupyter) {
            const widgetManager = (window as any).Jupyter.notebook.kernel.comm_manager;

            // Find the Jupyter widget
            widgetManager.register_target("ReactViewerWidget", function (comm: any) {
                comm.on_msg((msg: any) => {
                    console.log("Message from Python:", msg.content.data);
                    // Handle incoming messages
                });

                // Example: Send a message to Python
                comm.send({data: "Hello from React!"});
            });
        }
    }, []);
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
                {use_node_editor_only ? <NodeEditorComponent/> : <CanvasWrapper/>}
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