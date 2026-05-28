// Collapsible "Welds (N)" section for the selection inspector.
//
// Reads the reverse member→weld index built at GLB-load time
// (weldGraphStore) and renders one row per weld touching the
// currently-selected member. Each row's partner names are clickable —
// click navigates selection to that partner via selectInOtherModel,
// the same path the CAD↔FEA link buttons use.
//
// Renders nothing when the selected member has no welds, so the
// section costs zero vertical real estate for non-welded selections.

import React, {useState} from "react";

import {selectInOtherModel} from "@/utils/scene/crossModelSelect";
import {useWeldGraphStore, type WeldRef} from "@/state/weldGraphStore";

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

const fmtThroatMm = (throat: number | null): string => {
    if (throat === null || !isFinite(throat)) return "";
    // GLB stores SI metres; show mm to one decimal for legibility.
    return `${(throat * 1000).toFixed(1)} mm`;
};

const WeldRow: React.FC<{
    weld: WeldRef;
    fileName: string | null;
}> = ({weld, fileName}) => {
    const throat = fmtThroatMm(weld.throat);
    return (
        <li className="py-0.5">
            <div className="flex items-center gap-2 text-[11px] text-gray-200">
                <span className="font-mono truncate" title={weld.weldName}>
                    {weld.weldName}
                </span>
                {weld.weldType && (
                    <span className="text-gray-400">{weld.weldType}</span>
                )}
                {throat && <span className="text-gray-400">throat {throat}</span>}
            </div>
            {weld.partners.length > 0 && (
                <div className="flex items-center gap-1 flex-wrap ml-2 text-[11px]">
                    <span className="text-gray-500">joins:</span>
                    {weld.partners.map((partner) => (
                        <button
                            key={partner}
                            type="button"
                            className="text-blue-300 hover:text-blue-200 hover:underline truncate"
                            title={`Select ${partner}`}
                            onClick={() => {
                                if (!fileName) return;
                                void selectInOtherModel({
                                    file: fileName,
                                    nodeNames: [partner],
                                });
                            }}
                        >
                            {partner}
                        </button>
                    ))}
                </div>
            )}
        </li>
    );
};

const WeldsSection: React.FC<{
    fileName: string | null;
    objectName: string | null;
}> = ({fileName, objectName}) => {
    const welds = useWeldGraphStore((s) =>
        objectName ? s.indexByMember.get(objectName) ?? null : null,
    );
    const [expanded, setExpanded] = useState(false);

    if (!welds || welds.length === 0) return null;

    return (
        <div className="mt-2">
            <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="flex items-center gap-1 text-[12px] text-gray-100 hover:text-white"
                aria-expanded={expanded}
                aria-controls="object-welds"
            >
                <Chevron open={expanded} />
                <span className="font-semibold">Welds ({welds.length})</span>
            </button>
            {expanded && (
                <ul id="object-welds" className="mt-1 ml-4 list-none">
                    {welds.map((weld) => (
                        <WeldRow key={weld.weldName} weld={weld} fileName={fileName} />
                    ))}
                </ul>
            )}
        </div>
    );
};

export default WeldsSection;
