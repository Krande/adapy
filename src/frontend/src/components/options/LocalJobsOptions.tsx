import React from "react";

import { budgetCoresText, budgetText, queueSummary, settingsFromDrafts } from "./localJobs";
import { useLocalJobs } from "./useLocalJobs";

// The local job queue's limits, on a server with no job queue of its own (it runs conversions,
// bakes and checks in its own environment). How many jobs run at once, and the CPU they share:
// each running job is given its share of the budget as its thread count. A change applies to the
// next job that starts. Loading and saving are in useLocalJobs.ts, the text in localJobs.ts.

const LocalJobsOptions: React.FC = () => {
    const {view, error, isSaving, save} = useLocalJobs();
    const [maxDraft, setMaxDraft] = React.useState("");
    const [budgetDraft, setBudgetDraft] = React.useState("");

    // The fields show what is in force after every load and save.
    React.useEffect(() => {
        if (!view) return;
        setMaxDraft(String(view.max_parallel));
        setBudgetDraft(budgetText(view));
    }, [view]);

    const handleApply = () => {
        void save(settingsFromDrafts(maxDraft, budgetDraft));
    };

    if (!view) {
        return <div className="text-xs text-gray-400">{error ?? "Loading…"}</div>;
    }
    return (
        <div className="space-y-2 text-xs" data-local-jobs-options="">
            <p className="text-gray-400">
                This viewer runs its jobs itself. Jobs beyond the limit wait in order. The CPU budget is divided over the running jobs.
                Changes apply to the next job that starts.
            </p>
            <label className="flex items-center gap-2" title="How many jobs run at once; the rest wait in order">
                Jobs at once
                <input
                    type="number"
                    data-option="local-jobs-max"
                    min={1}
                    max={256}
                    placeholder={String(view.default_max_parallel)}
                    className="w-16 rounded-sm bg-gray-700 px-1 py-0.5 text-gray-100"
                    value={maxDraft}
                    onChange={(e) => setMaxDraft(e.target.value)}
                />
            </label>
            <label className="flex items-center gap-2" title="Cores (4), a fraction (0.5) or a percentage (50%); empty for every core">
                CPU budget
                <input
                    type="text"
                    data-option="local-jobs-cpu"
                    placeholder="all cores"
                    className="w-20 rounded-sm bg-gray-700 px-1 py-0.5 text-gray-100"
                    value={budgetDraft}
                    onChange={(e) => setBudgetDraft(e.target.value)}
                />
                <span className="text-gray-500">{budgetCoresText(view)}</span>
            </label>
            <div className="flex items-center gap-2">
                <button
                    type="button"
                    disabled={isSaving}
                    className="rounded-sm bg-blue-600 px-2 py-0.5 text-white hover:bg-blue-500 disabled:opacity-50"
                    onClick={handleApply}
                >
                    Apply
                </button>
                <span className="text-gray-500">{queueSummary(view)}</span>
            </div>
            {error && <div className="text-red-400">{error}</div>}
        </div>
    );
};

export default LocalJobsOptions;
