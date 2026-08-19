import React, {Suspense} from "react";
import {Rnd} from "react-rnd";
import {Icon, IconButton} from "@/components/ui";
import {ErrorBoundary} from "@/components/common/ErrorBoundary";
import {useLayoutStore} from "./layoutStore";
import {useModeStore} from "./modeStore";
import {resolvePanel} from "./panelRegistry";
import {Z} from "./zIndex";

// Undocked panels.
//
// react-rnd is already a dependency and already drives two floating surfaces
// (InViewerPanelHost, NodeEditorComponent), so this adds no bundle weight. It is used
// ONLY for the float case: the docked regions are CSS grid, because a library that
// re-parents DOM nodes on drag would orphan the WebGL canvas.
//
// Floating is the escape hatch, not the default. Blender's non-blocking rule says
// persistent docked areas beat floating windows that cover your work — so panels dock
// by default and float only when the user asks.

const MIN_W = 240;
const MIN_H = 160;

export default function FloatLayer() {
    const mode = useModeStore((s) => s.mode);
    const floats = useLayoutStore((s) => s.perMode[mode]?.floats ?? {});
    const setFloat = useLayoutStore((s) => s.setFloat);
    const closePanel = useLayoutStore((s) => s.closePanel);
    const dockPanel = useLayoutStore((s) => s.dockPanel);

    const entries = Object.entries(floats).flatMap(([id, rect]) => {
        const def = resolvePanel(id);
        return def && rect ? [{def, rect}] : [];
    });

    if (entries.length === 0) return null;

    return (
        <>
            {entries.map(({def, rect}) => (
                <Rnd
                    key={def.id}
                    size={{width: rect.w, height: rect.h}}
                    position={{x: rect.x, y: rect.y}}
                    minWidth={MIN_W}
                    minHeight={MIN_H}
                    bounds="window"
                    // Drag by the header only: dragging from anywhere would make the
                    // panel body's own controls unusable.
                    dragHandleClassName="ada-float-handle"
                    style={{zIndex: Z.float}}
                    onDragStop={(_e, d) => setFloat(mode, def.id, {...rect, x: d.x, y: d.y})}
                    onResizeStop={(_e, _dir, ref, _delta, pos) =>
                        setFloat(mode, def.id, {
                            x: pos.x,
                            y: pos.y,
                            w: ref.offsetWidth,
                            h: ref.offsetHeight,
                        })
                    }
                    className="flex"
                >
                    <div className="flex flex-col w-full h-full min-h-0 bg-surface-1 border border-edge rounded-md shadow-float overflow-hidden">
                        <div className="ada-float-handle flex items-center gap-1.5 shrink-0 px-2 h-8 border-b border-edge cursor-move select-none">
                            <Icon name={def.icon} size="sm" />
                            <span className="text-xs font-semibold truncate">{def.title}</span>
                            <span className="flex-1 min-w-0" />
                            <IconButton
                                size="sm"
                                tooltip="Dock panel to the right"
                                icon={<Icon name="dock-right" size="sm" />}
                                onClick={() => dockPanel(mode, def.id, "right")}
                            />
                            <IconButton
                                size="sm"
                                tooltip={`Close ${def.title}`}
                                icon={<Icon name="close" size="sm" />}
                                onClick={() => closePanel(mode, def.id)}
                            />
                        </div>
                        <div className="flex-1 min-h-0 overflow-auto scrollbar">
                            <ErrorBoundary label={def.title}>
                                <Suspense fallback={null}>
                                    <def.component />
                                </Suspense>
                            </ErrorBoundary>
                        </div>
                    </div>
                </Rnd>
            ))}
        </>
    );
}
