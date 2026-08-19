import React from "react";
import ColorLegend from "@/components/viewer/ColorLegend";
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
        </div>
    );
}
