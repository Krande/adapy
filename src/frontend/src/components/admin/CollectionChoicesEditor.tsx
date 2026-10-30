import React from "react";

import {
    affectedChoices,
    defaultChoiceLabel,
    pickOf,
    withPick,
    type CollectionChoiceDecl,
    type CollectionPicks,
} from "@/assets/collectionChoices";

// A "Share" section (or whatever the provider calls its per-collection choice): one dropdown per
// affected collection with two or more choices the worker found, the empty option the worker's
// own choice. Picking the default clears the override. Used by the Scheduled jobs form, the
// run-once form, a schedule's edit, and the Sources tab's request.

const CollectionChoicesEditor: React.FC<{
    decls: CollectionChoiceDecl[];
    /** The collections the job touches; null: every collection the provider lists a choice for. */
    collections: string[] | null;
    picks: CollectionPicks;
    onChange: (picks: CollectionPicks) => void;
    disabled?: boolean;
}> = ({decls, collections, picks, onChange, disabled}) => {
    const rows = affectedChoices(decls, collections);
    if (!rows.length) return null;
    const keyOf = (v: unknown) => JSON.stringify(v);
    const byLabel = new Map<string, typeof rows>();
    for (const r of rows) byLabel.set(r.decl.label, [...(byLabel.get(r.decl.label) ?? []), r]);
    return (
        <div className="space-y-1" data-testid="collection-choices">
            {[...byLabel.entries()].map(([label, list]) => (
                <fieldset key={label} className="text-[11px] text-gray-400">
                    <legend className="mb-0.5" title="The worker picks one itself; choose another here for this job">
                        {label}
                    </legend>
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                        {list.map(({decl, collection, entry}) => {
                            const picked = pickOf(picks, decl.option, collection);
                            return (
                                <label key={`${decl.option}/${collection}`} className="inline-flex items-center gap-1">
                                    <span className="font-mono text-gray-300">{collection}</span>
                                    <select
                                        className="bg-gray-800 border border-gray-700 rounded-sm px-1 py-0.5 text-xs text-gray-100 max-w-[22rem]"
                                        value={picked === undefined ? "" : keyOf(picked)}
                                        disabled={disabled}
                                        onChange={(e) => {
                                            const choice = entry.choices.find((c) => keyOf(c.value) === e.target.value);
                                            onChange(withPick(picks, decl.option, collection, choice?.value, entry));
                                        }}
                                    >
                                        <option value="">{defaultChoiceLabel(entry)}</option>
                                        {entry.choices.map((c) => (
                                            <option key={keyOf(c.value)} value={keyOf(c.value)}>
                                                {c.label}
                                            </option>
                                        ))}
                                    </select>
                                </label>
                            );
                        })}
                    </div>
                </fieldset>
            ))}
        </div>
    );
};

export default CollectionChoicesEditor;
