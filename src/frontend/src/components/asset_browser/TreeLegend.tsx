// The asset tree's legend, and the choice of what its row marks show.
//
// The marks are a colour per provider and a shape per relation (here / above / below), or -- in the
// geometry overlay -- one answer to "can this be loaded?". Neither reads without a key: a blue dot
// is a provider only once something says which one.

import React from "react";

import {
    GEOMETRY_MARK_TITLE,
    GEOMETRY_SOURCE_TITLE,
    rollupApplies,
    type GeometryMark,
    type GeometrySource,
} from "@/assets/geometryMarks";
import { kindTile } from "@/assets/kindTile";
import { providerIdTitle } from "@/assets/providerNames";
import { useViewerStores } from "@/state/AdaViewerContext";
import { useProviderName } from "@/state/providerNamesStore";
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
    const pn = useProviderName();
    const marks = useAssetBrowserStore((s) => s.treeMarks);
    const geometryRollup = useAssetBrowserStore((s) => s.geometryRollup);
    const collection = useAssetBrowserStore((s) => s.collection);
    const mode = useAssetBrowserStore((s) => s.mode);
    const geometrySource: GeometrySource = rollupApplies(geometryRollup, collection, mode) ? "server" : "client";

    // Inline, as a section of the tab's Options panel. It was a popover on a toolbar button of its
    // own, and the toolbar beside the collection picker ran out of width.
    return (
        <div>
            {
                <div className="text-xs text-gray-200 space-y-2.5">
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
                                                <span title={providerIdTitle(p)}>{pn(p)}</span>
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
                                <span className="text-gray-200" title={providerIdTitle(geometryProvider)}>
                            {pn(geometryProvider)}
                        </span>
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
            }
        </div>
    );
};

export default TreeLegend;
