// Collapsible "Connections (N)" section for the selection inspector.
//
// Replaces the older WeldsSection's per-weld dump. Reads the reverse
// member→connection index built at GLB-load time
// (connectionGraphStore) and shows one card per ada.Connection Part
// the selected member belongs to. Each card surfaces:
//   * Spec lineage when available (spec name + key inputs).
//   * Per-role member rows (incoming / landing / …) as clickable
//     selection links.
//   * A single "Welds (N)" link that selects every weld in the
//     connection at once — the user asked for parent-level
//     selection rather than per-weld rows.
//
// Renders nothing when the selected member has no connections, so
// the section costs zero vertical real estate for plain members.
//
// Selection is driven through selectInOtherModel — same path the
// cross-model CAD↔FEA buttons use, so it shares the highlight pipeline.

import React, {useState} from "react";

import {selectInOtherModel} from "@/utils/scene/crossModelSelect";
import {useConnectionGraphStore, type ConnectionRef} from "@/state/connectionGraphStore";

const Chevron: React.FC<{open: boolean}> = ({open}) => (
    <svg
        width="10"
        height="10"
        viewBox="0 0 10 10"
        aria-hidden="true"
        className={`transition-transform ${open ? "rotate-90" : ""}`}
    >
        <path d="M3 1l4 4-4 4" stroke="currentColor" strokeWidth="1.4" fill="none" />
    </svg>
);

const fmtAngle = (v: unknown): string | null => {
    if (typeof v !== "number" || !isFinite(v)) return null;
    return `${v.toFixed(0)}°`;
};

const SelectButton: React.FC<{
    fileName: string | null;
    nodeNames: string[];
    label: string;
    title?: string;
}> = ({fileName, nodeNames, label, title}) => (
    <button
        type="button"
        className="text-blue-300 hover:text-blue-200 hover:underline truncate"
        title={title ?? `Select ${label}`}
        onClick={() => {
            if (!fileName || nodeNames.length === 0) return;
            void selectInOtherModel({file: fileName, nodeNames});
        }}
    >
        {label}
    </button>
);

const ConnectionCard: React.FC<{
    connection: ConnectionRef;
    fileName: string | null;
}> = ({connection, fileName}) => {
    const {name, specName, specInputs, memberRoles, weldNames} = connection;
    // Inputs we surface inline: per-role section + angle. Other keys
    // (boolean flags, custom kwargs) just get a small "+N more" hint
    // rather than a full key/value dump — the panel is a summary,
    // not the build form.
    const inputRows: {role: string; section: string | null; angle: string | null}[] = [];
    if (specInputs && typeof specInputs === "object") {
        for (const [role, raw] of Object.entries(specInputs)) {
            const obj = (raw && typeof raw === "object") ? (raw as Record<string, unknown>) : {};
            const section = typeof obj.section === "string" ? obj.section : null;
            const angle = fmtAngle(obj.angle_deg);
            if (section || angle) inputRows.push({role, section, angle});
        }
    }

    return (
        <div className="p-1 border border-gray-700/60 rounded-sm bg-gray-800/30">
            <div className="flex items-center gap-2 text-[11px] text-gray-200">
                <span className="font-mono truncate" title={name}>{name}</span>
                {specName && <span className="text-gray-400">{specName}</span>}
            </div>
            {inputRows.length > 0 && (
                <ul className="ml-2 mt-0.5 text-[11px] text-gray-300 list-none">
                    {inputRows.map((r) => (
                        <li key={r.role} className="flex items-center gap-2">
                            <span className="text-gray-500 w-16 shrink-0">{r.role}</span>
                            {r.section && <span>{r.section}</span>}
                            {r.angle && <span className="text-gray-400">{r.angle}</span>}
                        </li>
                    ))}
                </ul>
            )}
            {Object.keys(memberRoles).length > 0 && (
                <div className="ml-2 mt-1 flex items-center gap-2 flex-wrap text-[11px]">
                    <span className="text-gray-500">members:</span>
                    {Object.entries(memberRoles).map(([role, names]) =>
                        names.length === 0 ? null : (
                            <SelectButton
                                key={role}
                                fileName={fileName}
                                nodeNames={names}
                                label={`${role}${names.length > 1 ? ` (${names.length})` : ""}`}
                                title={`Select ${role}: ${names.join(", ")}`}
                            />
                        ),
                    )}
                </div>
            )}
            {weldNames.length > 0 && (
                <div className="ml-2 mt-1 flex items-center gap-2 text-[11px]">
                    <span className="text-gray-500">welds:</span>
                    <SelectButton
                        fileName={fileName}
                        nodeNames={weldNames}
                        label={`select all (${weldNames.length})`}
                        title={`Select all ${weldNames.length} welds in ${name}`}
                    />
                </div>
            )}
        </div>
    );
};

const ConnectionsSection: React.FC<{
    fileName: string | null;
    objectName: string | null;
}> = ({fileName, objectName}) => {
    const connections = useConnectionGraphStore((s) =>
        objectName ? s.indexByMember.get(objectName) ?? null : null,
    );
    const [expanded, setExpanded] = useState(false);

    if (!connections || connections.length === 0) return null;

    return (
        <div className="mt-2">
            <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="flex items-center gap-1 text-[12px] text-gray-100 hover:text-white"
                aria-expanded={expanded}
                aria-controls="object-connections"
            >
                <Chevron open={expanded} />
                <span className="font-semibold">Connections ({connections.length})</span>
            </button>
            {expanded && (
                <div id="object-connections" className="mt-1 ml-4 flex flex-col gap-1">
                    {connections.map((conn) => (
                        <ConnectionCard key={conn.name} connection={conn} fileName={fileName} />
                    ))}
                </div>
            )}
        </div>
    );
};

export default ConnectionsSection;
