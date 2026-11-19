// Connections > Review, "Joints shown": the joints of a loaded model opened from a picked weld
// ("Show joint" in the Selected Object panel) or from a produced joint's row. Each is a card: its
// connection and spec, its members by role and its welds (each selects). The joints are
// `jointFocusStore`'s.

import React from "react";

import {findConnection, useConnectionGraphStore, type ConnectionRef} from "@/state/connectionGraphStore";
import {useJointFocusStore, type FocusedJoint} from "@/state/jointFocusStore";
import {useLineageStore} from "@/state/lineageStore";
import {selectInOtherModel} from "@/utils/scene/crossModelSelect";
import {weldSymbolSvg} from "@/utils/weldSymbols";
import {weldTypeName} from "@/utils/weldTypeNames";
import {fmtWeldLength, fmtWeldMm, isWeldMeta} from "@/components/info_box_selected_object/WeldRows";

const select = (file: string, names: string[]) => {
    if (names.length) void selectInOtherModel({file, nodeNames: names});
};

const NameButton: React.FC<{file: string; names: string[]; title?: string; children: React.ReactNode}> = ({file, names, title, children}) => (
    <button
        type="button"
        className="text-blue-300 hover:text-blue-200 hover:underline text-left truncate"
        title={title ?? `Select ${names.join(", ")}`}
        onClick={() => select(file, names)}
    >
        {children}
    </button>
);

/** The members of `conn`, by role where it names roles; the rest as "member". */
function membersByRole(conn: ConnectionRef): [string, string[]][] {
    const roled = new Set(Object.values(conn.memberRoles).flat());
    const rest = [...conn.beamNames, ...conn.plateNames].filter((n) => !roled.has(n));
    const out = Object.entries(conn.memberRoles).filter(([, names]) => names.length > 0);
    if (rest.length) out.push(["member", rest]);
    return out;
}

const WeldLine: React.FC<{file: string; name: string}> = ({file, name}) => {
    const meta = useLineageStore((s) => s.getMetadata(file, name));
    const weld = isWeldMeta(meta) ? meta : null;
    const svg = weld ? (weld.symbol_svg ?? weldSymbolSvg(weld.weld_type, {size: 16})) : null;
    return (
        <li className="flex items-center gap-1.5 min-w-0">
            {svg && <span className="inline-flex shrink-0" aria-hidden="true" dangerouslySetInnerHTML={{__html: svg}} />}
            <NameButton file={file} names={[name]}>
                {name}
            </NameButton>
            {weld && (
                <span className="text-gray-400 truncate" title={weld.weld_type ?? undefined}>
                    · {weldTypeName(weld.weld_type)} a={fmtWeldMm(weld.throat)}
                    {typeof weld.length === "number" ? ` · ${fmtWeldLength(weld.length)}` : ""}
                </span>
            )}
        </li>
    );
};

const JointCard: React.FC<{joint: FocusedJoint}> = ({joint}) => {
    // Re-read when the model's connections change (a model loaded, unloaded).
    useConnectionGraphStore((s) => s.byFile);
    const conn = findConnection(joint.file, joint.name);
    const remove = useJointFocusStore((s) => s.remove);
    return (
        <div className="flex flex-col gap-1 p-1.5 border border-gray-700/60 rounded-sm bg-gray-800/30 text-[11px]" data-focused-joint={joint.name}>
            <div className="flex items-center gap-2 min-w-0">
                <span className="font-mono text-gray-100 truncate flex-1" title={`${joint.name} in ${joint.file}`}>
                    {joint.name}
                </span>
                {conn?.specName && <span className="text-gray-400 truncate">{conn.specName}</span>}
                <button
                    type="button"
                    className="text-gray-400 hover:text-white px-1"
                    title="Stop drawing this joint's overlays and take it off the list"
                    aria-label={`Close ${joint.name}`}
                    onClick={() => remove(joint.file, joint.name)}
                >
                    ×
                </button>
            </div>
            {!conn ? (
                <p className="italic text-gray-400">This joint is not in the models on screen.</p>
            ) : (
                <>
                    {membersByRole(conn).map(([role, names]) => (
                        <div key={role} className="flex items-center gap-2 min-w-0">
                            <span className="text-gray-500 w-16 shrink-0">{role}</span>
                            <span className="flex flex-wrap gap-x-2 min-w-0">
                                {names.map((n) => (
                                    <NameButton key={n} file={joint.file} names={[n]}>
                                        {n}
                                    </NameButton>
                                ))}
                            </span>
                        </div>
                    ))}
                    {conn.weldNames.length > 0 && (
                        <div className="flex flex-col gap-0.5">
                            <div className="flex items-center gap-2">
                                <span className="text-gray-500 w-16 shrink-0">welds</span>
                                <NameButton file={joint.file} names={conn.weldNames} title={`Select all ${conn.weldNames.length} welds of ${conn.name}`}>
                                    select all ({conn.weldNames.length})
                                </NameButton>
                            </div>
                            <ul className="ml-4 flex flex-col gap-0.5 list-none">
                                {conn.weldNames.map((w) => (
                                    <WeldLine key={w} file={joint.file} name={w} />
                                ))}
                            </ul>
                        </div>
                    )}
                </>
            )}
        </div>
    );
};

const FocusedJoints: React.FC = () => {
    const joints = useJointFocusStore((s) => s.joints);
    const clear = useJointFocusStore((s) => s.clear);
    if (joints.length === 0) return null;
    return (
        <div className="flex flex-col gap-2" data-review="focused-joints">
            <div className="flex items-center gap-2 text-gray-300">
                <span>
                    <span className="font-semibold">{joints.length}</span> joint{joints.length === 1 ? "" : "s"} shown
                    <span className="text-gray-500"> — overlays for these joints only</span>
                </span>
                {joints.length > 1 && (
                    <button type="button" className="ml-auto text-xs text-gray-400 hover:text-white" onClick={clear}>
                        close all
                    </button>
                )}
            </div>
            {joints.map((j) => (
                <JointCard key={`${j.file}\n${j.name}`} joint={j} />
            ))}
        </div>
    );
};

export default FocusedJoints;
