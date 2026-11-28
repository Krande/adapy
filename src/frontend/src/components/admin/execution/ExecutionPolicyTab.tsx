import React, {useEffect, useMemo, useState} from "react";

import {ApiError, viewerApi} from "@/services/viewerApi";
import {defaultExecutionPolicy} from "@/services/compute/executionPolicyModel";
import {loadWorkerAccess} from "@/services/compute/workerAccess";
import {fmtTimestamp} from "../scheduleFormat";
import ExecutionPolicyTable from "./ExecutionPolicyTable";
import {policyFromRows, rowErrors, rowsFromPolicy, samePolicy, type PolicyRow} from "./executionPolicyEditor";

// Admin Panel -> Execution: the global execution policy. Per operation (and
// optionally per source extension, engine slug or plugin id) the ordered engines
// the viewer tries: adacpp's native wasm modules, in-browser Pyodide, the server
// worker pool. Users may reorder among the engines allowed to them; the worker is
// further gated by worker pool access (Admin -> Users), where a per-user override
// of single rows also lives.

const ExecutionPolicyTab: React.FC = () => {
    const [rows, setRows] = useState<PolicyRow[]>([]);
    const [saved, setSaved] = useState<PolicyRow[]>([]);
    const [meta, setMeta] = useState<{stored: boolean; updated_by: string | null; updated_at: string | null}>({
        stored: false,
        updated_by: null,
        updated_at: null,
    });
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const apply = (body: Awaited<ReturnType<typeof viewerApi.adminGetExecutionPolicy>>) => {
        const next = rowsFromPolicy(body.policy);
        setRows(next);
        setSaved(next);
        setMeta({stored: body.stored, updated_by: body.updated_by, updated_at: body.updated_at});
    };

    const reload = async () => {
        setLoading(true);
        try {
            apply(await viewerApi.adminGetExecutionPolicy());
            setError(null);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void reload();
    }, []);

    const errors = useMemo(() => rowErrors(rows), [rows]);
    const dirty = !samePolicy(policyFromRows(rows), policyFromRows(saved));
    const atDefaults = samePolicy(policyFromRows(rows), defaultExecutionPolicy());

    const save = async (policy: ReturnType<typeof policyFromRows> | null) => {
        setSaving(true);
        try {
            apply(await viewerApi.adminUpdateExecutionPolicy(policy));
            setError(null);
            // This admin's own routing follows the new policy straight away.
            void loadWorkerAccess(true);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="h-full overflow-auto px-3 sm:px-4 py-3 text-sm space-y-3" data-testid="admin-execution-tab">
            <div className="space-y-1 text-xs text-gray-400 max-w-3xl">
                <p>
                    Which engine runs each operation, tried in order: when an engine cannot handle the input
                    it is skipped, and when it fails the next one runs. A row for a file extension (or an
                    engine slug, or a plugin id) overrides the operation's “any” row.
                </p>
                <p>
                    Users may move any engine allowed to them to the front in their settings. The server
                    worker is allowed only to users with worker pool access, and the server refuses a worker
                    job the policy does not allow. Work no browser engine can do always runs on the server.
                </p>
            </div>
            {error && <div className="text-red-300 text-xs">{error}</div>}
            <ExecutionPolicyTable rows={rows} onChange={setRows} disabled={saving || loading} testId="exec-global-table"/>
            {errors.length > 0 && (
                <ul className="text-xs text-red-300 list-disc pl-5">
                    {errors.map((e) => (
                        <li key={e}>{e}</li>
                    ))}
                </ul>
            )}
            <div className="flex flex-wrap items-center gap-2 text-xs">
                <button
                    type="button"
                    className="bg-blue-700 hover:bg-blue-600 px-3 py-1 rounded-sm disabled:opacity-50"
                    disabled={saving || !dirty || errors.length > 0}
                    onClick={() => void save(policyFromRows(rows))}
                >
                    {saving ? "Saving…" : "Save"}
                </button>
                <button
                    type="button"
                    className="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded-sm disabled:opacity-50"
                    disabled={saving || !dirty}
                    onClick={() => setRows(saved)}
                >
                    Discard changes
                </button>
                <button
                    type="button"
                    className="bg-gray-700 hover:bg-gray-600 px-3 py-1 rounded-sm disabled:opacity-50"
                    disabled={saving || (!meta.stored && atDefaults)}
                    onClick={() => void save(null)}
                    title="Drop the stored policy and use the built-in defaults"
                >
                    Reset to defaults
                </button>
                <button
                    type="button"
                    className="ml-auto bg-gray-800 hover:bg-gray-700 px-3 py-1 rounded-sm disabled:opacity-50"
                    disabled={loading}
                    onClick={() => void reload()}
                >
                    {loading ? "Loading…" : "Refresh"}
                </button>
            </div>
            <div className="text-[11px] text-gray-500">
                {meta.stored
                    ? `Stored policy, updated ${fmtTimestamp(meta.updated_at)}${meta.updated_by ? ` by ${meta.updated_by}` : ""}.`
                    : "Built-in defaults (nothing stored)."}
            </div>
        </div>
    );
};

export default ExecutionPolicyTab;
