// "Request design geometry" for a file a mesh export wrote (`meta.geometry.kind === "mesh"`).
//
// The server runs a one-hit design export of the node the GLB came from -- asking the design system
// for it first when it has not published it, and the user may -- and writes `<stem>.ifc` beside the
// GLB (`POST /files/request-geometry`). The run is watched in the global toast like any export; it
// lives in the file's SOURCE scope, which is where the export runs list shows it.
//
// Once the IFC is written the two files share a sidecar, which is the IFC's from then on: the GLB's
// record then names the IFC (`meta.file`, `derived.mesh`) instead of offering this button --
// `designSiblingOf` reads that.

import React, {useEffect, useState} from "react";

import {searchExportApi, type FileMeta} from "@/services/api/searchExport";
import {trackExport} from "@/services/sweepTracking";
import {ApiError} from "@/services/viewerApi";

/** The record is a mesh export's whose design geometry has not been written yet. A merged file
 *  holds a whole collection's nodes, and design geometry is requested one node at a time. */
export function wantsDesignGeometry(meta: FileMeta | null | undefined): boolean {
    return meta?.geometry?.kind === "mesh" && !meta.geometry.merged;
}

/** The design file written for the mesh file `key`, when its record says there is one. */
export function designSiblingOf(meta: FileMeta | null | undefined, key: string | null | undefined): string | null {
    if (!meta || !key || meta.format !== "ifc" || !meta.file || meta.file === key) return null;
    return meta.derived?.mesh === key ? meta.file : null;
}

export const RequestDesignGeometryButton: React.FC<{scope: string; fileKey: string; meta: FileMeta; className?: string}> = ({
    scope,
    fileKey,
    meta,
    className,
}) => {
    const [busy, setBusy] = useState(false);
    const [note, setNote] = useState<string | null>(null);
    useEffect(() => setNote(null), [fileKey]);
    if (!wantsDesignGeometry(meta)) return null;
    const name = meta.source?.full_name ?? meta.source?.node_id ?? fileKey;
    const onClick = async () => {
        setBusy(true);
        setNote(null);
        try {
            const run = await searchExportApi.requestDesignGeometry(scope, fileKey);
            trackExport(run.scope, run.id, `${run.requesting ? "Request geometry + export" : "Design export"} ${name}`);
            setNote(`queued -- ${fileKey.replace(/\.[^./]+$/, ".ifc")} is written beside this file when it is done`);
        } catch (e) {
            setNote(e instanceof ApiError || e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };
    return (
        <span className="inline-flex min-w-0 items-center gap-2">
            <button
                type="button"
                disabled={busy}
                onClick={() => void onClick()}
                title={`Ask the design system for ${name}'s own geometry (when it has not exported it yet) and write it as IFC beside this mesh`}
                className={className ?? "rounded-sm border border-gray-600 px-1.5 py-0.5 hover:bg-gray-700 disabled:opacity-40"}
            >
                {busy ? "Requesting…" : "Request design geometry"}
            </button>
            {note && (
                <span className="truncate text-amber-300" title={note}>
                    {note}
                </span>
            )}
        </span>
    );
};
