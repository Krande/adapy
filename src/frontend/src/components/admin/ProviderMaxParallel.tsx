import React, {useCallback, useEffect, useState} from "react";

import type {RunBudget} from "@/services/viewerApi";
import {ApiError, viewerApi} from "@/services/viewerApi";

import {budgetUsage, defaultBudgetLabel, parseMaxParallel} from "./runBudget";

// Admin → Providers → a provider's "max parallel jobs": the most provider jobs ALL its runs may have
// in flight together. Runs of one provider go side by side (one collection is worked on by one run
// at a time); this is the budget they share. Blank: every live worker serving the provider.

const errText = (e: unknown) => (e instanceof ApiError ? e.detail || e.message : e instanceof Error ? e.message : String(e));

const ProviderMaxParallel: React.FC<{provider: string}> = ({provider}) => {
    const [budget, setBudget] = useState<RunBudget | null>(null);
    const [text, setText] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);

    const show = useCallback((b: RunBudget) => {
        setBudget(b);
        setText(b.max_parallel != null ? String(b.max_parallel) : "");
    }, []);

    useEffect(() => {
        let alive = true;
        viewerApi
            .adminAssetRunsMaxParallel(provider)
            .then((b) => alive && show(b))
            .catch((e) => alive && setError(errText(e)));
        return () => {
            alive = false;
        };
    }, [provider, show]);

    const saved = budget?.max_parallel != null ? String(budget.max_parallel) : "";
    const parsed = parseMaxParallel(text);
    const save = async () => {
        if ("error" in parsed) return;
        setBusy(true);
        try {
            show(await viewerApi.adminSetAssetRunsMaxParallel(provider, parsed.value));
            setError(null);
        } catch (e) {
            setError(errText(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-gray-400" data-testid="provider-max-parallel">
            <label className="inline-flex items-center gap-1" title="The most jobs all of this provider's runs may have in flight together">
                Max parallel jobs
                <input
                    className="w-14 bg-gray-800 border border-gray-700 rounded-sm px-1 py-0.5 text-xs text-gray-100"
                    inputMode="numeric"
                    placeholder="all"
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                />
            </label>
            <span className="text-gray-500">{text.trim() ? "" : `default: ${defaultBudgetLabel(budget)}`}</span>
            {text.trim() !== saved && (
                <button
                    type="button"
                    className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-2 py-0.5 rounded-sm"
                    disabled={busy || "error" in parsed}
                    onClick={() => void save()}
                >
                    {busy ? "Saving…" : "Save"}
                </button>
            )}
            {budget && <span className="text-gray-500">{budgetUsage(budget)}</span>}
            {"error" in parsed && <span className="text-amber-300">{parsed.error}</span>}
            {error && <span className="text-red-300">{error}</span>}
        </div>
    );
};

export default ProviderMaxParallel;
