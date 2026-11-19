// A picked weld's Properties: what the GLB's object metadata says of it (`_weld_metadata` in
// ada.visit.scene_handling.scene_from_part) -- its type as its ISO 2553 symbol and the symbol's
// description, its throat, legs, sides and intermittency, how long it is, the members it joins --
// and the joint it is a weld of, from the model's connections (`connectionGraphStore`), with a link
// to that joint in Connections > Review, where it can be drawn with overlays of its own.
//
// It said "No metadata available" before: the metadata was there (the panel read `type: "weld"`
// from it), but the panel knew only beams and plates.

import React from "react";

import {openConnectionsTab} from "@/layout/connectionsTabActions";
import {connectionOfWeld, useConnectionGraphStore, type ConnectionRef} from "@/state/connectionGraphStore";
import {JOINTS_REVIEW_TAB} from "@/state/connectionBuilderTabStore";
import {useJointFocusStore} from "@/state/jointFocusStore";
import {selectInOtherModel} from "@/utils/scene/crossModelSelect";
import {weldSymbol, weldSymbolSvg} from "@/utils/weldSymbols";
import {weldTypeName} from "@/utils/weldTypeNames";

/** A weld's entry in the GLB's object metadata. Every field but `type` may be missing from an
 *  older GLB (`length` and `at` came later). */
export interface WeldMeta {
    type: "weld";
    weld_type?: string | null;
    throat?: number | null;
    leg1?: number | null;
    leg2?: number | null;
    sided?: string | null;
    intermittent?: {pitch?: number | null; length_on?: number | null; length_off?: number | null} | null;
    members?: string[] | null;
    /** Metres along its line. */
    length?: number | null;
    /** Halfway along its line, in the model's coordinates. */
    at?: number[] | null;
    /** Its symbol as an SVG icon in `currentColor`, where a server sent one. */
    symbol_svg?: string | null;
}

export const isWeldMeta = (meta: unknown): meta is WeldMeta =>
    !!meta && typeof meta === "object" && String((meta as {type?: unknown}).type).toLowerCase() === "weld";

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** Metres as millimetres, to a tenth. */
export const fmtWeldMm = (m: number | null | undefined): string => (num(m) ? `${Math.round(m * 10000) / 10} mm` : "—");

/** A length: millimetres under a metre, metres above. */
export const fmtWeldLength = (m: number): string => (m < 1 ? fmtWeldMm(m) : `${m.toFixed(3)} m`);

const Row: React.FC<{label: string; children: React.ReactNode}> = ({label, children}) => (
    <div className="table-row">
        <div className="table-cell w-24 align-top text-gray-200">{label}</div>
        <div className="table-cell w-48 break-words">{children}</div>
    </div>
);

const LinkButton: React.FC<{onClick: () => void; title: string; children: React.ReactNode}> = ({onClick, title, children}) => (
    <button type="button" className="text-blue-300 hover:text-blue-200 hover:underline text-left" title={title} onClick={onClick}>
        {children}
    </button>
);

/** The role `name` has in `conn`, where the connection names its members' roles. */
function roleOf(conn: ConnectionRef | null, name: string): string | null {
    if (!conn) return null;
    for (const [role, names] of Object.entries(conn.memberRoles)) if (names.includes(name)) return role;
    return null;
}

/** Show the joint `conn` of `file` in Connections > Review, drawn with overlays of its own. */
export function showJoint(file: string, conn: ConnectionRef): void {
    useJointFocusStore.getState().focus(file, conn.name);
    openConnectionsTab(JOINTS_REVIEW_TAB);
}

const WeldRows: React.FC<{meta: WeldMeta; fileName: string | null; weldName: string | null}> = ({meta, fileName, weldName}) => {
    const index = useConnectionGraphStore((s) => s.indexByMember);
    const conn = weldName ? connectionOfWeld(index, fileName, weldName) : null;
    const type = meta.weld_type ?? null;
    // The server's symbol where it sent one; the viewer draws the same one otherwise.
    const svg = meta.symbol_svg ?? weldSymbolSvg(type, {size: 18});
    const description = weldSymbol(type)?.description ?? null;
    const shortName = weldTypeName(type);
    const members = (meta.members ?? []).filter((m): m is string => typeof m === "string" && m.length > 0);
    const file = conn?.file ?? fileName;
    const intermittent = meta.intermittent;
    return (
        <>
            <Row label="Type:">
                <span className="inline-flex items-center gap-1.5" title={type ?? undefined}>
                    {svg && (
                        // The ISO 2553 symbol (ada.api.weld_symbols), inline so it takes the text colour.
                        <span className="inline-flex shrink-0" aria-hidden="true" dangerouslySetInnerHTML={{__html: svg}} />
                    )}
                    {/* The type's short name, as the Connections panel's weld rows name it. */}
                    <span>Weld · {shortName}</span>
                </span>
                {description && <div className="text-gray-400 text-[11px]">{description}</div>}
            </Row>
            <Row label="Throat:">{num(meta.throat) ? `a = ${fmtWeldMm(meta.throat)}` : "—"}</Row>
            {(num(meta.leg1) || num(meta.leg2)) && (
                <Row label="Legs:">
                    {fmtWeldMm(meta.leg1)} / {fmtWeldMm(meta.leg2)}
                </Row>
            )}
            {meta.sided === "two" && <Row label="Sides:">both sides</Row>}
            {intermittent && (
                <Row label="Intermittent:">
                    {fmtWeldMm(intermittent.length_on)} on / {fmtWeldMm(intermittent.length_off)} off
                    {num(intermittent.pitch) ? `, pitch ${fmtWeldMm(intermittent.pitch)}` : ""}
                </Row>
            )}
            {num(meta.length) && <Row label="Length:">{fmtWeldLength(meta.length)}</Row>}
            <Row label="Joins:">
                {members.length === 0 ? (
                    "—"
                ) : (
                    <span className="inline-flex flex-wrap gap-x-2">
                        {members.map((m) => {
                            const role = roleOf(conn, m);
                            return (
                                <LinkButton
                                    key={m}
                                    title={`Select ${m}`}
                                    onClick={() => {
                                        if (fileName) void selectInOtherModel({file: fileName, nodeNames: [m]});
                                    }}
                                >
                                    {m}
                                    {role && <span className="text-gray-400"> ({role})</span>}
                                </LinkButton>
                            );
                        })}
                    </span>
                )}
            </Row>
            {conn && (
                <Row label="Joint:">
                    <span className="font-mono break-all" title={conn.name}>
                        {conn.name}
                    </span>
                    {conn.specName && <span className="text-gray-400"> · {conn.specName}</span>}
                    {file && (
                        <div>
                            <LinkButton
                                title="Open this joint in Connections > Review, where its overlays (member axes, weld labels, weld colour) can be drawn for it alone"
                                onClick={() => showJoint(file, conn)}
                            >
                                Show joint ▸
                            </LinkButton>
                        </div>
                    )}
                </Row>
            )}
        </>
    );
};

export default WeldRows;
