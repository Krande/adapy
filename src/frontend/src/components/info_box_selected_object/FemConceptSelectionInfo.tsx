import React from "react";

import {useFemConceptsStore} from "@/state/femConceptsStore";
import {writeToClipboard} from "@/utils/clipboard/copySelectionNames";
import {
    describeRef,
    fmtVec,
    loadsGeneratedBy,
    refKey,
    summarizeSelection,
    type ConceptDetail,
    type FemConceptData,
    type FemConceptRef,
} from "@/utils/fem/femSelection";

// The selected FEM concepts in the Selected Object Info panel: one concept's details (a mass,
// a support, a load, a constraint node, an equipment box), or for several what they add up to --
// mass and centre of gravity, resultant force -- and the list, any one of which can be narrowed
// to. Shows only while a concept is selected (a click on a glyph, or a panel's Select).

/** How many of a multi-selection the list shows before it says how many more there are. */
const LIST_LIMIT = 200;

const btn = "bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded-sm px-2 py-1";

function asText(details: ConceptDetail[]): string {
    return details
        .map((d) => [`${d.kind}\t${d.name}`, ...d.rows.map(([k, v]) => `\t${k}\t${v}`)].join("\n"))
        .join("\n");
}

const Rows: React.FC<{rows: [string, string][]}> = ({rows}) => (
    <table className="w-full text-xs mt-1">
        <tbody>
            {rows.map(([label, value]) => (
                <tr key={label}>
                    <td className="opacity-70 pr-2 align-top whitespace-nowrap">{label}</td>
                    <td className="font-mono tabular-nums break-all">{value}</td>
                </tr>
            ))}
        </tbody>
    </table>
);

const FemConceptSelectionInfo: React.FC = () => {
    const selection = useFemConceptsStore((s) => s.selection);
    const masses = useFemConceptsStore((s) => s.masses);
    const bcs = useFemConceptsStore((s) => s.bcs);
    const constraints = useFemConceptsStore((s) => s.constraints);
    const scenarios = useFemConceptsStore((s) => s.scenarios);
    const equipment = useFemConceptsStore((s) => s.equipment);
    const selectedScenario = useFemConceptsStore((s) => s.selectedScenario);
    const selectConcept = useFemConceptsStore((s) => s.selectConcept);
    const selectConcepts = useFemConceptsStore((s) => s.selectConcepts);
    const clear = useFemConceptsStore((s) => s.clearConceptSelection);
    const [copied, setCopied] = React.useState(false);

    const data: FemConceptData = React.useMemo(
        () => ({masses, bcs, constraints, scenarios, equipment}),
        [masses, bcs, constraints, scenarios, equipment],
    );
    const items = React.useMemo(
        () =>
            selection
                .map((ref) => ({ref, detail: describeRef(data, ref)}))
                .filter((x): x is {ref: FemConceptRef; detail: ConceptDetail} => x.detail !== null),
        [selection, data],
    );
    if (items.length === 0) return null;

    const onCopy = async () => {
        if (await writeToClipboard(asText(items.map((x) => x.detail)))) {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
        }
    };

    const actions = (
        <div className="flex flex-wrap gap-2 mt-2">
            <button type="button" className={btn} onClick={() => void onCopy()} title="Copy the details as tab-separated text">
                {copied ? "Copied ✓" : "Copy"}
            </button>
            <button type="button" className={btn} onClick={clear}>
                Clear
            </button>
        </div>
    );

    if (items.length === 1) {
        const {ref, detail} = items[0];
        // Cross-links between an equipment and the loads it was resolved into, in the case shown.
        const generated = ref.kind === "equipment" && selectedScenario >= 0
            ? loadsGeneratedBy(data, selectedScenario, equipment[ref.index].name ?? "")
            : [];
        const load = ref.kind === "load" ? scenarios[ref.scenario]?.loads?.[ref.index] : undefined;
        const source = load?.generated_by ? equipment.findIndex((e) => e.name === load.generated_by) : -1;
        return (
            <div className="mt-2 border-t border-gray-600 pt-2">
                <div className="text-[10px] uppercase tracking-wider opacity-60">{detail.kind}</div>
                <div className="font-semibold break-all">{detail.name}</div>
                <Rows rows={detail.rows}/>
                {(generated.length > 0 || source >= 0) && (
                    <div className="flex flex-wrap gap-2 mt-2">
                        {generated.length > 0 && (
                            <button
                                type="button"
                                className={btn}
                                onClick={() => selectConcepts([ref, ...generated])}
                                title={`Select the ${generated.length} load(s) ${detail.name} became in ${scenarios[selectedScenario]?.name}`}
                            >
                                Select its loads ({generated.length})
                            </button>
                        )}
                        {source >= 0 && (
                            <button
                                type="button"
                                className={btn}
                                onClick={() => selectConcept({kind: "equipment", index: source})}
                                title="Select the equipment this load was generated from"
                            >
                                Select equipment
                            </button>
                        )}
                    </div>
                )}
                {actions}
            </div>
        );
    }

    const summary = summarizeSelection(data, selection);
    return (
        <div className="mt-2 border-t border-gray-600 pt-2">
            <div className="font-semibold">FEM concepts ({items.length})</div>
            <Rows
                rows={[
                    ...summary.counts.map(({kind, count}): [string, string] => [kind, String(count)]),
                    ...(summary.mass > 0
                        ? ([
                            ["Mass (t)", summary.mass.toLocaleString("en-US", {maximumFractionDigits: 3})],
                            ["COG (m)", fmtVec(summary.cog)],
                        ] as [string, string][])
                        : []),
                    ...(summary.force ? ([["Resultant (kN)", fmtVec(summary.force.map((f) => f / 1000))]] as [string, string][]) : []),
                ]}
            />
            <ul className="mt-2 max-h-48 overflow-y-auto text-xs space-y-0.5">
                {items.slice(0, LIST_LIMIT).map(({ref, detail}) => (
                    <li key={refKey(ref)} className="flex items-center gap-1">
                        <button
                            type="button"
                            className="flex-1 text-left truncate hover:underline"
                            onClick={() => selectConcept(ref)}
                            title="Select only this one"
                        >
                            <span className="opacity-60">{detail.kind} · </span>
                            {detail.name}
                        </button>
                        <button
                            type="button"
                            className="opacity-60 hover:opacity-100 px-1"
                            onClick={() => selectConcept(ref, true)}
                            aria-label={`Remove ${detail.name} from the selection`}
                            title="Remove from the selection"
                        >
                            ×
                        </button>
                    </li>
                ))}
                {items.length > LIST_LIMIT && <li className="opacity-60">…and {items.length - LIST_LIMIT} more</li>}
            </ul>
            {actions}
        </div>
    );
};

export default FemConceptSelectionInfo;
