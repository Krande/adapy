// src/App.tsx
import "./app.css";
import React, {useEffect, Suspense} from 'react'
import CanvasWrapper from './components/viewer/CanvasWrapper';
import Menu from './components/Menu';
import {runtime} from "@/runtime/config";

import ResizableTreeView from './components/tree_view/ResizableTreeView';
import {useNodeEditorStore} from "./state/useNodeEditorStore";
import {AdaViewerProvider} from "./state/AdaViewerContext";
import NodeEditorComponent from "./components/node_editor/NodeEditorComponent";
import {useUrlParamLoad} from "./hooks/useUrlParamLoad";

// REST-only UI lives in its own chunk so the embedded desktop bundle
// (the index.zip shipped with ada-py) doesn't pull in the conversion /
// upload / Pyodide / OIDC code. The chunk only loads when
// COMMS_MODE === "rest".
const RestModeUI = React.lazy(() => import("./components/rest_mode/RestModeUI"));
const AuthGate = React.lazy(() => import("./components/auth/AuthGate"));
const AuthCallback = React.lazy(() => import("./components/auth/AuthCallback"));
const ConvertPage = React.lazy(() => import("./components/convert/ConvertPage"));
const AdminPanel = React.lazy(() => import("./components/admin/AdminPanel"));
const InViewerPanelHost = React.lazy(() => import("./components/InViewerPanelHost"));
const isRestMode = runtime.isRestMode();
const isAuthCallback = isRestMode && window.location.pathname === "/auth/callback";
const isConvertPage = isRestMode && window.location.pathname.startsWith("/convert");
const isAdminPage = isRestMode && window.location.pathname.startsWith("/admin");


function App() {
    if (isAuthCallback) {
        // Dedicated landing for OIDC redirect_uri. Doesn't render the
        // viewer at all — it just exchanges the code and bounces back.
        // Stays outside the AdaViewerProvider since it never touches
        // viewer state.
        return (
            <Suspense fallback={null}>
                <AuthCallback/>
            </Suspense>
        );
    }

    if (isConvertPage) {
        // Standalone /convert page. Mounted outside AdaViewerProvider
        // so the 3D scene, websocket plumbing, and tree view never
        // spin up — the page is just upload + pick target + download.
        // AuthGate handles sign-in + populates the scope store from
        // /api/me; the page itself auto-picks the user's own scope.
        return (
            <Suspense fallback={null}>
                <AuthGate>
                    <div className="h-[100dvh] w-full overflow-hidden">
                        <ConvertPage/>
                    </div>
                </AuthGate>
            </Suspense>
        );
    }

    if (isAdminPage) {
        // Path-mounted /admin page. Outside AdaViewerProvider for
        // the same reasons as /convert — no 3D, no websocket, no
        // tree view. AuthGate gives us the user object so the panel
        // can render its admin-only message for non-admins instead
        // of a blank screen. Tab state syncs to URL hash so a
        // refresh stays on /admin#<tab>.
        return (
            <Suspense fallback={null}>
                <AuthGate>
                    <div className="h-[100dvh] w-full overflow-hidden">
                        <AdminPanel/>
                    </div>
                </AuthGate>
            </Suspense>
        );
    }

    // Everything that touches viewer state lives under the provider so
    // Phase-2 migrations can flip consumers off the module-level
    // singletons in state/refs.ts and state/*Store.ts without touching
    // this file.
    return (
        <AdaViewerProvider>
            <AppBody/>
        </AdaViewerProvider>
    );
}

function AppBody() {
    const {isNodeEditorVisible, use_node_editor_only} = useNodeEditorStore();
    useUrlParamLoad();
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

            {isRestMode && (
                <Suspense fallback={null}>
                    <RestModeUI/>
                </Suspense>
            )}

            {/* Rnd-hosted Admin / Convert modal — mounted last so it
                layers above the canvas + tree, but still under the
                toast slot (z-50 vs the host's z-60). Lazy so the
                Admin/Convert chunks don't load until the user opens
                them. */}
            {isRestMode && (
                <Suspense fallback={null}>
                    <InViewerPanelHost/>
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