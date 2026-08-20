import React from "react";
import {Splitter} from "@/components/ui";
import {DOCK_LIMITS, type DockedId} from "./regions";
import {useLayoutStore} from "./layoutStore";
import {useModeStore} from "./modeStore";
import {profileDef, type ProfileId} from "./profiles";
import DockHost from "./DockHost";
import FloatLayer from "./FloatLayer";
import StatusBar from "./StatusBar";
import TitleBar from "./TitleBar";
import CommandPalette from "./CommandPalette";
import MarkingMenu from "./MarkingMenu";
import ToastHost from "./ToastHost";
import {ConfirmHost} from "@/components/ui";
import HelpDialogs from "./HelpDialogs";
import ToolRail from "./ToolRail";
import ViewportHost from "./ViewportHost";
import {useLegacyFlagSync} from "./useLegacyFlagSync";

// The shell.
//
// A CSS grid with named areas, whose track sizes come from layoutStore. That is the
// whole mechanism for the headline fix: dragging a splitter changes one number, the
// grid reflows, ThreeCanvas's existing ResizeObserver fires, and three.js resizes. The
// viewport is a TRACK, not a backdrop — so a panel can never cover the model, which was
// the single most-cited complaint about the old UI.
//
//   titlebar   titlebar   titlebar   titlebar   titlebar
//   rail       leftdock   split-l    viewport   rightdock
//   rail       bottomdock bottomdock bottomdock bottomdock
//   statusbar  statusbar  statusbar  statusbar  statusbar
//
// The bottom dock spans the full width on purpose: the FEA data table and the
// conversion log are wide-and-short, and putting them across the bottom is what stops
// them being floated over the geometry.

export interface AppShellProps {
    profile?: ProfileId;
    /** Replaces the 3D canvas in the viewport track (the graph profile). */
    viewportOverride?: React.ReactNode;
}

export default function AppShell({profile = "viewer", viewportOverride}: AppShellProps) {
    const p = profileDef(profile);
    const mode = useModeStore((s) => s.mode);
    const layout = useLayoutStore((s) => s.perMode[mode]);
    const setDockSize = useLayoutStore((s) => s.setDockSize);

    // Keep the legacy per-panel visibility booleans in step with the docks; several
    // panels still gate themselves on those flags. See useLegacyFlagSync.
    useLegacyFlagSync();

    // A dock occupies a track only when it has something to show. An empty or collapsed
    // dock collapses to zero width rather than leaving a stripe of chrome.
    const visible = (d: DockedId) =>
        p.docks && Boolean(layout?.docks[d]?.tabs.length) && !layout?.docks[d]?.collapsed;

    const size = (d: DockedId) => (visible(d) ? (layout?.docks[d]?.size ?? DOCK_LIMITS[d].default) : 0);
    const SPLIT = 4;

    const leftW = size("left");
    const rightW = size("right");
    const bottomH = size("bottom");

    return (
        <div
            className="grid w-full h-full min-w-0 min-h-0 overflow-hidden bg-surface-0 text-content font-ui text-base"
            style={{
                gridTemplateAreas: [
                    '"titlebar titlebar titlebar titlebar titlebar"',
                    '"rail leftdock split-l viewport rightdock"',
                    '"rail bottomdock bottomdock bottomdock bottomdock"',
                    '"statusbar statusbar statusbar statusbar statusbar"',
                ].join(" "),
                gridTemplateColumns: [
                    p.toolRail ? "auto" : "0",
                    `${leftW}px`,
                    visible("left") ? `${SPLIT}px` : "0",
                    "minmax(0, 1fr)",
                    `${rightW}px`,
                ].join(" "),
                gridTemplateRows: [
                    "auto",
                    "minmax(0, 1fr)",
                    `${bottomH}px`,
                    p.statusBar ? "auto" : "0",
                ].join(" "),
            }}
        >
            <TitleBar showModeSwitcher={p.modeSwitcher} />

            {p.toolRail && <ToolRail />}

            {visible("left") && (
                <div style={{gridArea: "leftdock"}} className="flex min-w-0 min-h-0">
                    <DockHost dock="left" />
                </div>
            )}

            {visible("left") && (
                <div style={{gridArea: "split-l"}} className="flex items-stretch">
                    <Splitter
                        orientation="vertical"
                        label="Resize left dock"
                        value={leftW}
                        min={DOCK_LIMITS.left.min}
                        max={DOCK_LIMITS.left.max}
                        onChange={(n) => setDockSize(mode, "left", n)}
                    />
                </div>
            )}

            {/*
              ViewportHost is rendered UNCONDITIONALLY for canvas profiles and only
              hidden via CSS. Unmounting it would orphan the imperatively-appended WebGL
              canvas and, with it, the five headless controllers — which is what would
              break the non-modality contract.
            */}
            {p.canvas || viewportOverride ? (
                <ViewportHost visible>{viewportOverride}</ViewportHost>
            ) : (
                <div style={{gridArea: "viewport"}} className="min-w-0 min-h-0" />
            )}

            {/* The right splitter rides on the dock's own left border rather than
                occupying a track, so the grid template stays four columns wide. */}
            {visible("right") && (
                <div style={{gridArea: "rightdock"}} className="relative flex min-w-0">
                    <div className="absolute inset-y-0 left-0 -ml-0.5 flex items-stretch">
                        <Splitter
                            orientation="vertical"
                            label="Resize right dock"
                            side="after"
                            value={rightW}
                            min={DOCK_LIMITS.right.min}
                            max={DOCK_LIMITS.right.max}
                            onChange={(n) => setDockSize(mode, "right", n)}
                        />
                    </div>
                    <DockHost dock="right" />
                </div>
            )}

            {visible("bottom") && (
                <div style={{gridArea: "bottomdock"}} className="relative flex flex-col min-h-0">
                    <div className="absolute inset-x-0 top-0 -mt-0.5 flex justify-stretch">
                        <Splitter
                            orientation="horizontal"
                            label="Resize bottom dock"
                            side="after"
                            value={bottomH}
                            min={DOCK_LIMITS.bottom.min}
                            max={DOCK_LIMITS.bottom.max}
                            onChange={(n) => setDockSize(mode, "bottom", n)}
                            className="w-full"
                        />
                    </div>
                    <DockHost dock="bottom" />
                </div>
            )}

            {p.statusBar && <StatusBar />}

            {p.docks && <FloatLayer />}

            {/* Ambient job/upload notifications. Outside the grid: they are transient
                overlays, not a region, and must not reflow the layout when they appear. */}
            <ToastHost />

            {/* Whatever `confirm()` currently has pending. One host per shell; callers
                await a promise rather than rendering their own dialog. */}
            <ConfirmHost />

            {/* Help ▸ Keyboard shortcuts / About. Rendered from shortcuts.ts rather
                than a hand-kept list, so a bound key is a documented key. */}
            <HelpDialogs />

            {/* Ctrl+K. Outside the grid — a modal overlay, not a region. */}
            <CommandPalette />

            {/* Right-click in the viewport. Yields to the cellbuilder's own cell/port
                menus, which claim the event by calling preventDefault. */}
            {p.canvas && <MarkingMenu />}
        </div>
    );
}
