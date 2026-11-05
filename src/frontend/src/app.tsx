// src/App.tsx
// NB: the stylesheet is imported by index.tsx (the entry), not here. This module is
// the built-in UI shell and is lazy-loaded, so a CSS import here reaches only builds
// where this shell is the active one.
import React, {Suspense} from 'react'
import CanvasWrapper from './components/viewer/CanvasWrapper';
import Menu from './components/Menu';
import {runtime} from "@/runtime/config";

import ResizableTreeView from './components/tree_view/ResizableTreeView';
import {SceneMenuHost} from './components/tree_view/SceneTreeMenu';
import {useNodeEditorStore} from "./state/useNodeEditorStore";
import {AdaViewerProvider} from "./state/AdaViewerContext";
import NodeEditorComponent from "./components/node_editor/NodeEditorComponent";
import {useUrlParamLoad} from "./hooks/useUrlParamLoad";
import {useSceneRestore} from "./hooks/useSceneRestore";
import {followerParams as simFollowerParams} from "@/utils/simChannel";
import {layoutEngineEnabled} from "@/layout/flag";
import {activeUiShellId, getUiShell, type UiShellSpec} from "@/plugins/uiShells";
import {popoutPanelId} from "@/layout/popout";

// The core panel catalogue loads each panel's classic component and content
// through this module (`import("@/app")`), so they come out of this shell's own
// chunk rather than a chunk of their own. See `layout/classicBodies`.
export {CLASSIC_PANEL_COMPONENTS, CLASSIC_PANEL_CONTENTS, loadClassicPanel} from "@/layout/classicBodies";

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
const AttributeSearchWindow = React.lazy(() => import("./components/asset_browser/AttributeSearchWindow"));
// Canvas-less Simulation follower window (`?simfollow=…`) — mounted in its own
// lazy chunk so a normal viewer tab never pulls it in.
const SimFollowerPage = React.lazy(() => import("./components/simulation/SimFollowerPage"));
const isRestMode = runtime.isRestMode();
const isSimFollower = !!simFollowerParams();
// A panel alone in a browser window of its own (`?popout=<panelId>`), canvas-less.
const popoutId = popoutPanelId();
const PopoutPage = React.lazy(() => import("./layout/PopoutPage"));
const isAuthCallback = isRestMode && window.location.pathname === "/auth/callback";
const isConvertPage = isRestMode && window.location.pathname.startsWith("/convert");
const isAdminPage = isRestMode && window.location.pathname.startsWith("/admin");
// The layout engine (`?layout=engine`, off by default): the same UI rendered from
// the panel registry by the layout host, in the user's preset (classic unless
// they chose docked). Its own chunk, so the default path does not load it.
const useLayoutEngine = layoutEngineEnabled();
const LayoutRoot = React.lazy(() => import("./layout/LayoutRoot"));


/** The active shell's header for canvas-less pages (`UiShellChrome.pageHeader`), loaded once. */
let pageHeader: React.LazyExoticComponent<React.ComponentType<{title: string; backHref: string}>> | null | undefined;
function shellPageHeader() {
    if (pageHeader === undefined) {
        const load = getUiShell(activeUiShellId())?.chrome?.pageHeader;
        pageHeader = load ? React.lazy(load) : null;
    }
    return pageHeader;
}

/** A canvas-less page, the whole window: under the active shell's page header when it has one. */
function CanvasLessPage({title, children}: {title: string; children: React.ReactNode}) {
    const Header = shellPageHeader();
    if (!Header) return <div className="h-[100dvh] w-full overflow-hidden">{children}</div>;
    const back = new URL(window.location.href);
    back.pathname = "/";
    back.hash = "";
    return (
        <div className="flex h-[100dvh] w-full flex-col overflow-hidden">
            <Suspense fallback={null}>
                <Header title={title} backHref={back.toString()}/>
            </Suspense>
            <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
        </div>
    );
}

/**
 * What every shell core mounts shares: the canvas-less pages (`/auth/callback`,
 * `/convert`, `/admin`, a Simulation follower window, a popped-out panel), and around `children`
 * the viewer provider and, in hosted mode, the sign-in gate.
 */
export function AppRoot({children}: {children: React.ReactNode}) {
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
                    <CanvasLessPage title="Convert">
                        <ConvertPage/>
                    </CanvasLessPage>
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
                    <CanvasLessPage title="Admin">
                        <AdminPanel/>
                    </CanvasLessPage>
                </AuthGate>
            </Suspense>
        );
    }

    if (popoutId) {
        // One panel, full-window: under the provider so its stores resolve, behind
        // AuthGate in REST mode, without the canvas or the deep-link loading.
        const page = (
            <Suspense fallback={null}>
                <PopoutPage panelId={popoutId}/>
            </Suspense>
        );
        return (
            <AdaViewerProvider>
                {isRestMode ? (
                    <Suspense fallback={null}>
                        <AuthGate>{page}</AuthGate>
                    </Suspense>
                ) : (
                    page
                )}
            </AdaViewerProvider>
        );
    }

    if (isSimFollower) {
        // A follower window boots controls-only: the Simulation plugin tab
        // full-window, no 3D canvas / tree / websocket. Under the provider so the
        // tab's stores resolve; behind AuthGate in REST mode so plugin API calls
        // (e.g. enqueuing a check) carry the caller's token.
        const page = (
            <Suspense fallback={null}>
                <SimFollowerPage/>
            </Suspense>
        );
        return (
            <AdaViewerProvider>
                {isRestMode ? (
                    <Suspense fallback={null}>
                        <AuthGate>{page}</AuthGate>
                    </Suspense>
                ) : (
                    page
                )}
            </AdaViewerProvider>
        );
    }

    // Everything that touches viewer state lives under the provider: it is
    // what owns this viewer's scene, camera, controls and renderer, and
    // `useViewerRefs()` throws without it. AuthGate is a pass-through when
    // AUTH_ENABLED is false; the real sign-in UI only fires when the deployment
    // turns auth on.
    return (
        <AdaViewerProvider>
            <ViewerBootstrap/>
            {isRestMode ? (
                <Suspense fallback={null}>
                    <AuthGate>{children}</AuthGate>
                </Suspense>
            ) : (
                children
            )}
        </AdaViewerProvider>
    );
}

/** Deep links (`?file=…`) and the restored scene, for every shell; outside the sign-in gate, as before. */
function ViewerBootstrap() {
    useUrlParamLoad();
    useSceneRestore();
    return null;
}

function App() {
    return (
        <AppRoot>
            <AppBody/>
        </AppRoot>
    );
}

/** A shell composed from core's layout (`UiShellSpec` without `load`). */
export function composedShell(spec: UiShellSpec): React.ComponentType {
    const Composed = () => (
        <AppRoot>
            <ComposedShellBody spec={spec}/>
        </AppRoot>
    );
    return Composed;
}

function AppBody() {
    const {isNodeEditorVisible, use_node_editor_only} = useNodeEditorStore();

    const tree = useLayoutEngine ? (
        <Suspense fallback={null}>
            <LayoutRoot/>
        </Suspense>
    ) : (
        <div className={"relative flex flex-row h-full w-full bg-gray-900"}>
            {/* Tree View Section */}
            <div className={"relative h-full"}>
                <ResizableTreeView/>
            </div>
            {/* The Scene's right-click menu, for tree rows and 3D picks alike; portals to the body. */}
            <SceneMenuHost/>

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
            {/* The attribute search's own window: independent of the host above, so it can stay
                open beside Admin or Convert. Renders nothing until opened. */}
            {isRestMode && (
                <Suspense fallback={null}>
                    <AttributeSearchWindow/>
                </Suspense>
            )}

        </div>
    );
    return tree;
}

const ComposedLayout = React.lazy(() => import("./layout/ComposedLayout"));

function ComposedShellBody({spec}: {spec: UiShellSpec}) {
    return (
        <Suspense fallback={null}>
            <ComposedLayout spec={spec}/>
        </Suspense>
    );
}

export default App;