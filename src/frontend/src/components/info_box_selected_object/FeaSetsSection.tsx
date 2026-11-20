// The Selected Object panel's FEA section: what the loaded result's sets and
// model properties say about the picked element -- its property values and the
// element sets naming it -- each with a button that isolates it. Read from the
// Scene tree's FEA rows (`@/utils/scene/fea/feaTree`), in a pop-out window the
// main window's; the buttons are scene actions.

import React, {useSyncExternalStore} from "react";

import {useSceneActions} from "@/layout/sceneActions";
import {feaTreeView, subscribeFeaTreeView} from "@/utils/scene/fea/feaTree";
import {Field, PropertyGroup} from "./cellBuilderSelection/PropertyItemLayout";

const SHOWN_SETS = 12;

const IsolateButton: React.FC<{onClick: () => void; title: string; compact?: boolean}> = ({onClick, title, compact}) => (
    <button
        type="button"
        onClick={onClick}
        title={title}
        className={
            compact
                ? "ml-auto shrink-0 rounded-sm px-1 text-[10px] text-gray-300 hover:bg-gray-600 hover:text-white"
                : "ml-2 shrink-0 rounded-sm bg-gray-700 px-1.5 py-0.5 text-[11px] text-gray-100 hover:bg-gray-600"
        }
    >
        Isolate
    </button>
);

type Picked = NonNullable<ReturnType<typeof feaTreeView>["picked"]>;

/** The flat layout's version: one group as the item above it draws its groups, a Field per property
 *  and per set, a compact Isolate at the row's right. */
const FlatFeaSets: React.FC<{picked: Picked; isolate: (rows: string[]) => void}> = ({picked, isolate}) => {
    const [allSets, setAllSets] = React.useState(false);
    const sets = allSets ? picked.sets : picked.sets.slice(0, SHOWN_SETS);
    const fields = (
        <>
            {picked.properties.map((p) => (
                <Field key={p.field} label={p.label}>
                    <span className="min-w-0 flex-1 truncate text-gray-100" title={p.valueLabel}>{p.valueLabel}</span>
                    <IsolateButton compact onClick={() => isolate([p.rowId])} title={`Isolate every element whose ${p.label.toLowerCase()} is ${p.valueLabel}`}/>
                </Field>
            ))}
            {picked.pending > 0 && <div className="italic text-gray-400">Reading the element properties…</div>}
            {sets.map((s, i) => (
                <Field key={s.rowId} label={i === 0 ? (picked.sets.length === 1 ? "Set" : `Sets (${picked.sets.length})`) : ""}>
                    <span className="min-w-0 flex-1 truncate text-gray-100" title={s.name}>{s.name}</span>
                    <IsolateButton compact onClick={() => isolate([s.rowId])} title={`Isolate the set ${s.name}`}/>
                </Field>
            ))}
            {picked.sets.length > 1 && (
                <div className="flex gap-2 text-[10px]">
                    {picked.sets.length > SHOWN_SETS && (
                        <button type="button" className="text-sky-300 hover:underline" onClick={() => setAllSets(!allSets)}>
                            {allSets ? "Show fewer" : `Show all ${picked.sets.length}`}
                        </button>
                    )}
                    <button
                        type="button"
                        className="ml-auto text-sky-300 hover:underline"
                        onClick={() => isolate(picked.sets.map((s) => s.rowId))}
                        title="Isolate every set listed here, together"
                    >
                        Isolate all sets
                    </button>
                </div>
            )}
        </>
    );
    return (
        <div className="pt-2 text-xs text-white" data-testid="fea-sets-section" data-fea-sets="flat">
            <PropertyGroup group={{title: "FEA sets and properties", fields}}/>
        </div>
    );
};

/** Renders nothing for a pick that is not an element of the loaded result, or one no set or property
 *  names. `flat`: drawn as a group of the flat layout (`ObjectInfoBodyProps.flat`). */
export const FeaSetsSection: React.FC<{flat?: boolean}> = ({flat = false}) => {
    const view = useSyncExternalStore(subscribeFeaTreeView, feaTreeView, feaTreeView);
    const actions = useSceneActions();
    const [allSets, setAllSets] = React.useState(false);
    const picked = view.picked;
    if (!picked) return null;
    if (flat) return <FlatFeaSets picked={picked} isolate={(rows) => actions.feaIsolate(rows, "replace")}/>;
    const sets = allSets ? picked.sets : picked.sets.slice(0, SHOWN_SETS);
    return (
        <div className="mt-2 text-xs" data-testid="fea-sets-section">
            <div className="mb-1 font-semibold text-gray-300">FEA sets and properties</div>
            {picked.properties.map((p) => (
                <div key={p.field} className="flex items-center py-0.5">
                    <span className="w-24 shrink-0 text-gray-400">{p.label}:</span>
                    <span className="min-w-0 flex-1 truncate" title={p.valueLabel}>{p.valueLabel}</span>
                    <IsolateButton
                        onClick={() => actions.feaIsolate([p.rowId], "replace")}
                        title={`Isolate every element whose ${p.label.toLowerCase()} is ${p.valueLabel}`}
                    />
                </div>
            ))}
            {picked.pending > 0 && <div className="py-0.5 italic text-gray-400">Reading the element properties…</div>}
            {picked.sets.length > 0 && (
                <div className="py-0.5">
                    <div className="text-gray-400">In {picked.sets.length === 1 ? "1 set" : `${picked.sets.length} sets`}:</div>
                    {sets.map((s) => (
                        <div key={s.rowId} className="flex items-center py-0.5 pl-2">
                            <span className="min-w-0 flex-1 truncate" title={s.name}>{s.name}</span>
                            <IsolateButton onClick={() => actions.feaIsolate([s.rowId], "replace")} title={`Isolate the set ${s.name}`}/>
                        </div>
                    ))}
                    {picked.sets.length > SHOWN_SETS && (
                        <button type="button" className="pl-2 text-[11px] text-sky-300 hover:underline" onClick={() => setAllSets(!allSets)}>
                            {allSets ? "Show fewer" : `Show all ${picked.sets.length}`}
                        </button>
                    )}
                    {picked.sets.length > 1 && (
                        <button
                            type="button"
                            className="ml-2 text-[11px] text-sky-300 hover:underline"
                            onClick={() => actions.feaIsolate(picked.sets.map((s) => s.rowId), "replace")}
                            title="Isolate every set listed here, together"
                        >
                            Isolate all of them
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};

export default FeaSetsSection;
