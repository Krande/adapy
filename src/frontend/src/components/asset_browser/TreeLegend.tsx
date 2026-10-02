// The asset tree's legend, and the choice of what its row marks show.
//
// The marks are a colour per provider and a shape per relation (here / above / below), or -- in the
// geometry overlay -- one answer to "can this be loaded?". Neither reads without a key: a blue dot
// is a provider only once something says which one.

import React, { useEffect, useRef, useState } from "react";

import {
    GEOMETRY_MARK_TITLE,
    GEOMETRY_SOURCE_TITLE,
    rollupApplies,
    type GeometryMark,
    type GeometrySource,
} from "@/assets/geometryMarks";
import { kindTile } from "@/assets/kindTile";
import { useViewerStores } from "@/state/AdaViewerContext";
import type { AssetTreeMarks } from "@/state/assetBrowserStore";

import { GeometryDot } from "./AssetTree";

const MODES: readonly { id: AssetTreeMarks; label: string; title: string }[] = [
    { id: "providers", label: "Providers", title: "Which providers published at, above or below each row" },
    { id: "geometry", label: "Geometry", title: "Whether each row has geometry to load -- here, above or below -- or only tree" },
    { id: "off", label: "Off", title: "No marks" },
];

const Dot: React.FC<{ color: string; shape: "solid" | "ghost" | "below" }> = ({ color, shape }) => (
    <span
        className="w-2 h-2 rounded-full inline-block shrink-0"
        style={
            shape === "solid"
                ? { background: color }
                : { boxShadow: `inset 0 0 0 1.5px ${color}`, opacity: shape === "below" ? 0.8 : 0.6 }
        }
    />
);

const TreeLegend: React.FC<{ providers: readonly string[]; geometryProvider?: string | null }> = ({
    providers,
    geometryProvider,
}) => {
    const { useAssetBrowserStore } = useViewerStores();
    const marks = useAssetBrowserStore((s) => s.treeMarks);
    const geometryRollup = useAssetBrowserStore((s) => s.geometryRollup);
    const collection = useAssetBrowserStore((s) => s.collection);
    const mode = useAssetBrowserStore((s) => s.mode);
    const geometrySource: GeometrySource = rollupApplies(geometryRollup, collection, mode) ? "server" : "client";
    const [open, setOpen] = useState(false);
    const ref = useRef<HTMLDivElement | null>(null);

    useEffect(() => {
        if (!open) return;
        const onDown = (e: MouseEvent) => {
            if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
        };
        const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
        document.addEventListener("mousedown", onDown);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", onDown);
            document.removeEventListener("keydown", onKey);
        };
    }, [open]);

    return (
        <div ref={ref} className="relative shrink-0">
            <button
                type="button"
                aria-label="Legend and row marks"
                aria-expanded={open}
                title="Legend — what the marks mean, and what they show"
                onClick={() => setOpen((o) => !o)}
                className={`h-7 w-7 grid place-items-center rounded-md border text-xs ${
                    open ? "bg-gray-600 border-gray-500 text-white" : "border-gray-700 bg-gray-800 text-gray-300 hover:text-white hover:bg-gray-700"
                }`}
            >
                ●
            </button>
            {open && (
                <div className="absolute right-0 top-8 z-30 w-72 rounded-md border border-gray-700 bg-gray-900 p-2.5 text-xs text-gray-200 shadow-lg space-y-2.5">
                    <div>
                        <div className="text-gray-400 mb-1">Row marks show</div>
                        <div className="flex gap-1">
                            {MODES.map((m) => (
                                <button
                                    key={m.id}
                                    type="button"
                                    title={m.title}
                                    onClick={() => useAssetBrowserStore.getState().setTreeMarks(m.id)}
                                    className={`px-2 py-0.5 rounded-sm border ${
                                        marks === m.id ? "bg-blue-500/30 border-blue-400 text-white" : "border-gray-700 hover:bg-gray-800"
                                    }`}
                                >
                                    {m.label}
                                </button>
                            ))}
                        </div>
                    </div>

                    {marks === "providers" && (
                        <>
                            <div>
                                <div className="text-gray-400 mb-1">Colour = provider</div>
                                {providers.length === 0 ? (
                                    <div className="text-gray-500">No provider has published into this collection.</div>
                                ) : (
                                    <ul className="space-y-0.5">
                                        {providers.map((p) => (
                                            <li key={p} className="flex items-center gap-2">
                                                <Dot color={kindTile(p).bg} shape="solid" />
                                                <span className="font-mono">{p}</span>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                            <div>
                                <div className="text-gray-400 mb-1">Shape = where the publish is</div>
                                <ul className="space-y-0.5">
                                    <li className="flex items-center gap-2">
                                        <Dot color="#d1d5db" shape="solid" /> published at this node
                                    </li>
                                    <li className="flex items-center gap-2">
                                        <Dot color="#d1d5db" shape="ghost" /> covered by a publish above
                                    </li>
                                    <li className="flex items-center gap-2">
                                        <Dot color="#d1d5db" shape="below" /> something published below
                                    </li>
                                </ul>
                                <div className="text-gray-500 mt-1">
                                    A publish may be a tree only. Switch to Geometry to see what can be loaded.
                                </div>
                            </div>
                        </>
                    )}

                    {marks === "geometry" && (
                        <div className="text-gray-400">
                            Geometry from{" "}
                            {geometryProvider ? (
                                <span className="font-mono text-gray-200">{geometryProvider}</span>
                            ) : (
                                "any provider"
                            )}
                            {geometryProvider ? " only (the provider filter)" : " — set the provider filter to narrow it"}
                        </div>
                    )}
                    {marks === "geometry" && (
                        <div
                            data-testid="geometry-source"
                            title={GEOMETRY_SOURCE_TITLE[geometrySource]}
                            className={`rounded-sm border px-1.5 py-1 ${
                                geometrySource === "server"
                                    ? "border-emerald-700/60 text-emerald-200"
                                    : "border-amber-700/60 text-amber-200"
                            }`}
                        >
                            {geometrySource === "server"
                                ? "Server roll-up: the whole collection tree, opened or not"
                                : "Client only: unopened branches stay unknown until expanded"}
                        </div>
                    )}
                    {marks === "geometry" && (
                        <ul className="space-y-1">
                            {(["here", "covered", "below", "tree", "unknown"] as GeometryMark[]).map((m) => (
                                <li key={m} className="flex items-start gap-2">
                                    <span className="mt-1 w-2 grid place-items-center">
                                        <GeometryDot mark={m} />
                                    </span>
                                    <span>{GEOMETRY_MARK_TITLE[m]}</span>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </div>
    );
};

export default TreeLegend;
