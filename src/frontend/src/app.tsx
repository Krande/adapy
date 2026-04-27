// src/App.tsx
import "./app.css";
import React, {useEffect, Suspense} from 'react'
import CanvasWrapper from './components/viewer/CanvasWrapper';
import Menu from './components/Menu';
import OptionsComponent from './components/OptionsComponent';
import {useOptionsStore} from './state/optionsStore';
import {runtime} from "@/runtime/config";

import ResizableTreeView from './components/tree_view/ResizableTreeView';
import {useNodeEditorStore} from "./state/useNodeEditorStore";
import NodeEditorComponent from "./components/node_editor/NodeEditorComponent";

// REST-only UI lives in its own chunk so the embedded desktop bundle
// (the index.zip shipped with ada-py) doesn't pull in the conversion /
// upload / Pyodide / OIDC code. The chunk only loads when
// COMMS_MODE === "rest".
const RestModeUI = React.lazy(() => import("./components/rest_mode/RestModeUI"));
const AuthGate = React.lazy(() => import("./components/auth/AuthGate"));
const AuthCallback = React.lazy(() => import("./components/auth/AuthCallback"));
const isRestMode = runtime.isRestMode();
const isAuthCallback = isRestMode && window.location.pathname === "/auth/callback";


function App() {
    const {isOptionsVisible} = useOptionsStore(); // use the useNavBarStore function
    const {isNodeEditorVisible, use_node_editor_only} = useNodeEditorStore();
    useEffect(() => {
        // Check if running inside a Jupyter Notebook
        if (runtime.inJupyter()) {
            const widgetManager = runtime.jupyter().notebook.kernel.comm_manager;

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
    if (isAuthCallback) {
        // Dedicated landing for OIDC redirect_uri. Doesn't render the
        // viewer at all — it just exchanges the code and bounces back.
        return (
            <Suspense fallback={null}>
                <AuthCallback/>
            </Suspense>
        );
    }

    const tree = (
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

            {isRestMode && (
                <Suspense fallback={null}>
                    <RestModeUI/>
                </Suspense>
            )}

        </div>
    );

    if (!isRestMode) return tree;
    // AuthGate is a pass-through when AUTH_ENABLED is false; the real
    // sign-in UI only fires when the deployment turns auth on.
    return (
        <Suspense fallback={null}>
            <AuthGate>{tree}</AuthGate>
        </Suspense>
    );
}

export default App;