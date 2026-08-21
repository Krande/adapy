import React from "react";
import ColorLegend from "@/components/viewer/ColorLegend";
import {useCellBuilderStore} from "@/state/cellBuilderStore";

// The cellbuilder's viewport overlays: the right-click context menu, the port menu, the
// insert-equipment menu and the gizmo HUD's numeric entry.
//
// These were mounted by the classic Menu.tsx and were deleted with it at cutover — so
// Build mode lost its right-click menus and its numeric entry silently. Nothing threw:
// the controller kept opening menus into a store nobody was rendering.
//
// That is the fifth time this rewrite has found rendering or bootstrap work living in a
// component the shell does not render, after the plugin top-bar regions, the legacy
// visibility flags, AuthGate, and useUrlParamLoad + RestModeUI. The lesson has a shape
// now: before deleting a layout component, list everything it RENDERS as well as
// everything it imports — a mounted-but-unreferenced child leaves no other trace.
const CellBuilderContextMenu = React.lazy(() => import("@/components/viewer/CellBuilderContextMenu"));
const CellBuilderPortMenu = React.lazy(() => import("@/components/viewer/CellBuilderPortMenu"));
const CellBuilderInsertMenu = React.lazy(() => import("@/components/viewer/CellBuilderInsertMenu"));
const CellBuilderGizmoHud = React.lazy(() => import("@/components/viewer/CellBuilderGizmoHud"));
import {Z} from "./zIndex";

// Canvas-anchored HUDs.
//
// These are not panels: they annotate the 3D rather than sit beside it, so they anchor
// to viewport corners and never take a grid track. The distinction matters — a colour
// legend docked in a side panel is a legend you have to look away from the model to
// read, which defeats it.
//
// Positioned inside the viewport track (not over the whole window), so a legend cannot
// drift over a dock when the layout changes — the failure mode of the old
// `absolute right-5 top-80` placement, which was measured against the window and
// wandered as soon as anything else moved.
//
// pointer-events-none on the container with `auto` on children: the HUD must never
// swallow an orbit drag that happens to start on an empty part of the overlay.

export default function OverlayLayer() {
    // Only while a procedural model is open — the same gate the classic UI used. Each
    // component also self-gates on its own store state, so this is about not paying for
    // four lazy chunks in a session that never opens the builder.
    const builderActive = useCellBuilderStore((s) => s.active !== null);

    return (
        <div
            aria-hidden={false}
            style={{zIndex: Z.overlayHud}}
            className="pointer-events-none absolute inset-0 overflow-hidden"
        >
            {/* Colour legend. Self-gating: renders zero-width when the store says it is
                off, so no visibility flag is duplicated here. */}
            <div className="pointer-events-auto absolute right-3 top-1/2 -translate-y-1/2">
                <ColorLegend />
            </div>

            {builderActive && (
                <React.Suspense fallback={null}>
                    <CellBuilderContextMenu />
                    <CellBuilderPortMenu />
                    <CellBuilderInsertMenu />
                    <CellBuilderGizmoHud />
                </React.Suspense>
            )}
        </div>
    );
}
