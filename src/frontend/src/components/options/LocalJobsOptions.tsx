import React from "react";

import {localJobsApi, type LocalJobsView} from "@/services/api/localJobs";

// The local job queue's limits, on a server with no job queue of its own (it runs conversions,
// bakes and checks in its own environment). How many jobs run at once, and the CPU they share:
// each running job is given its share of the budget as its thread count. A change applies to the
// next job that starts.

/** The CPU budget field's text for a view: what was set, or empty for every core. */
export function budgetText(view: LocalJobsView): string {
    return view.cpu_budget ?? "";
}

/** "2 running, 3 queued" -- the queue's state in one line. */
export function queueSummary(view: LocalJobsView): string {
    const parts = [`${view.running.length} running`];
    if (view.queued.length) parts.push(`${view.queued.length} queued`);
    return parts.join(", ");
}

const LocalJobsOptions: React.FC = () => {
    const [view, setView] = React.useState<LocalJobsView | null>(null);
    const [maxDraft, setMaxDraft] = React.useState("");
    const [budgetDraft, setBudgetDraft] = React.useState("");
    const [error, setError] = React.useState<string | null>(null);
    const [saving, setSaving] = React.useState(false);

    const show = React.useCallback((v: LocalJobsView) => {
        setView(v);
        setMaxDraft(String(v.max_parallel));
        setBudgetDraft(budgetText(v));
    }, []);

    React.useEffect(() => {
        let alive = true;
        localJobsApi
            .get()
            .then((v) => alive && show(v))
            .catch((e) => alive && setError(String(e?.message ?? e)));
        return () => {
            alive = false;
        };
    }, [show]);

    const save = async () => {
        setSaving(true);
        setError(null);
        try {
            const max = maxDraft.trim();
            const budget = budgetDraft.trim();
            show(
                await localJobsApi.set({
                    max_parallel: max === "" ? null : Number(max),
                    cpu_budget: budget === "" ? null : budget,
                }),
            );
        } catch (e) {
            setError(String((e as Error)?.message ?? e));
        } finally {
            setSaving(false);
        }
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
                <span className="text-gray-500">
                    {view.cpu_budget_cores.toFixed(view.cpu_budget_cores % 1 ? 1 : 0)} of {view.host_cores} cores
                </span>
            </label>
            <div className="flex items-center gap-2">
                <button
                    type="button"
                    disabled={saving}
                    className="rounded-sm bg-blue-600 px-2 py-0.5 text-white hover:bg-blue-500 disabled:opacity-50"
                    onClick={() => void save()}
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
