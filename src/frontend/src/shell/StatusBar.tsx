import React from "react";
import {StatusDot, cn} from "@/components/ui";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {useWebsocketStatusStore} from "@/state/websocketStatusStore";
import {runtime} from "@/runtime/config";
import {useModeStore} from "./modeStore";
import {Z} from "./zIndex";

// A persistent, low-attention strip for the things that were previously either invisible
// or shouting from a floating box.
//
// Connection state lived in a toggleable overlay panel — so the one fact you want
// continuously (am I still connected?) was hidden by default, while things you glance at
// occasionally sat permanently over the model. This inverts that: ambient status is
// always visible and costs 22px; detail stays one click away.

export default function StatusBar() {
    const mode = useModeStore((s) => s.mode);
    const connected = useWebsocketStatusStore((s) => s.connected);

    // Selection is a Map<Object3D, Set<rangeId>>; the useful number is the total of the
    // inner sets, not the number of meshes.
    const selectionCount = useSelectedObjectStore((s) => {
        let n = 0;
        for (const ranges of s.selectedObjects.values()) n += ranges.size;
        return n;
    });

    const rest = runtime.isRestMode();

    return (
        <footer
            style={{gridArea: "statusbar", zIndex: Z.dock}}
            className="flex items-center gap-3 shrink-0 px-2 h-[22px] bg-surface-0 border-t border-edge text-xs text-content-muted select-none"
        >
            <span className="flex items-center gap-1.5 shrink-0">
                {rest ? (
                    <>
                        <StatusDot tone="info" label="REST mode" />
                        <span>REST</span>
                    </>
                ) : (
                    <>
                        <StatusDot
                            tone={connected ? "pass" : "fail"}
                            label={connected ? "Websocket connected" : "Websocket disconnected"}
                        />
                        <span className={cn(!connected && "text-fail")}>
                            {connected ? "Connected" : "Disconnected"}
                        </span>
                    </>
                )}
            </span>

            <Sep />

            <span className="shrink-0 tabular-nums">
                {selectionCount === 0
                    ? "No selection"
                    : `${selectionCount} selected`}
            </span>

            <span className="flex-1 min-w-0" />

            <span className="shrink-0 capitalize">{mode}</span>

            {runtime.adapyVersion() && (
                <>
                    <Sep />
                    <span className="shrink-0 font-mono text-content-subtle">{runtime.adapyVersion()}</span>
                </>
            )}
        </footer>
    );
}

function Sep() {
    return <span aria-hidden="true" className="shrink-0 w-px h-3 bg-edge" />;
}
