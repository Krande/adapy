// The Selected Object panel's FEA section: what the loaded result's sets and
// model properties say about the picked element -- its property values and the
// element sets naming it -- each with a button that isolates it. Read from the
// Scene tree's FEA rows (`@/utils/scene/fea/feaTree`), in a pop-out window the
// main window's; the buttons are scene actions.

import React, {useSyncExternalStore} from "react";

import {useSceneActions} from "@/layout/sceneActions";
import {feaTreeView, subscribeFeaTreeView} from "@/utils/scene/fea/feaTree";

const SHOWN_SETS = 12;

const IsolateButton: React.FC<{onClick: () => void; title: string}> = ({onClick, title}) => (
    <button
        type="button"
        onClick={onClick}
        title={title}
        className="ml-2 shrink-0 rounded-sm bg-gray-700 px-1.5 py-0.5 text-[11px] text-gray-100 hover:bg-gray-600"
    >
        Isolate
    </button>
);

/** Renders nothing for a pick that is not an element of the loaded result, or one no set or property names. */
export const FeaSetsSection: React.FC = () => {
    const view = useSyncExternalStore(subscribeFeaTreeView, feaTreeView, feaTreeView);
    const actions = useSceneActions();
    const [allSets, setAllSets] = React.useState(false);
    const picked = view.picked;
    if (!picked) return null;
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
