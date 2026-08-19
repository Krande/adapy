import React, {Suspense} from "react";

// The viewport's mount point.
//
// ==========================================================================
// THIS COMPONENT MUST NEVER BE CONDITIONALLY RENDERED.
//
// ThreeCanvas appends the WebGL canvas to this container imperatively
// (ThreeCanvas.tsx:101) and its removeChild cleanup is commented out (:261).
// Unmounting it — on a mode switch, a layout change, a re-render that swaps
// branches — orphans the GL context and loses the scene. `visible` toggles
// CSS only; the subtree stays mounted.
//
// This is also what keeps the non-modality contract honest: because the
// viewport never unmounts, neither do the five headless controllers inside
// CanvasWrapper, so a cellbuilder edit survives a trip to Results and back.
// ==========================================================================
//
// Reflow, not overlay: this sits in a CSS grid track. When a splitter changes a dock's
// width the track resizes, ThreeCanvas's existing ResizeObserver (ThreeCanvas.tsx:43)
// fires, and three.js resizes itself. No renderer changes were needed — the observer
// was already there, waiting for a container that actually changed size.

import OverlayLayer from "./OverlayLayer";

const CanvasWrapper = React.lazy(() => import("@/components/viewer/CanvasWrapper"));

export interface ViewportHostProps {
    /** False hides the viewport visually WITHOUT unmounting it. */
    visible?: boolean;
    /** Replaces the 3D canvas — the graph profile puts ReactFlow here. */
    children?: React.ReactNode;
}

export default function ViewportHost({visible = true, children}: ViewportHostProps) {
    return (
        <div
            // grid-area is set by AppShell's template; min-w/h-0 is what lets a grid
            // item actually shrink instead of forcing the track to its content size.
            style={{gridArea: "viewport"}}
            className={[
                "relative min-w-0 min-h-0 overflow-hidden bg-surface-0",
                visible ? "" : "invisible pointer-events-none",
            ].join(" ")}
            data-testid="viewport-host"
        >
            {children ?? (
                <Suspense fallback={null}>
                    {/* CanvasWrapper carries ThreeCanvas plus all five headless
                        controllers (section planes, FEM concepts, cellbuilder, type
                        icons, procedural follower) and the canvas-anchored HUDs.
                        Reused whole rather than re-wired: it already mounts them
                        correctly, and re-deriving that wiring is exactly the kind of
                        change that silently drops a feature. */}
                    <CanvasWrapper />
                </Suspense>
            )}
            {/* Canvas-anchored HUDs, inside the viewport track so they cannot drift over
                a dock when the layout changes. */}
            <OverlayLayer />
        </div>
    );
}
