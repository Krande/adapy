import React, {useEffect, useMemo, useState} from "react";

import {ApiError, viewerApi} from "@/services/viewerApi";
import {lookupOrder, type ExecutionPolicyDoc} from "@/services/compute/executionPolicyModel";
import {fmtTimestamp} from "../scheduleFormat";
import ExecutionPolicyTable from "./ExecutionPolicyTable";
import {policyFromRows, rowErrors, rowsFromPolicy, samePolicy, type PolicyRow} from "./executionPolicyEditor";

// Admin -> Users -> a user: that user's execution-policy override. Each row here
// replaces the global row for the same (operation, qualifier) for this user only;
// everything else follows the global policy (Admin -> Execution). The worker still
// needs the worker pool access set just above.

const UserExecutionOverride: React.FC<{userSub: string}> = ({userSub}) => {
    const [rows, setRows] = useState<PolicyRow[]>([]);
    const [saved, setSaved] = useState<PolicyRow[]>([]);
    const [effective, setEffective] = useState<ExecutionPolicyDoc>({});
    const [meta, setMeta] = useState<{updated_by: string | null; updated_at: string | null}>({
        updated_by: null,
        updated_at: null,
    });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const apply = (body: Awaited<ReturnType<typeof viewerApi.adminGetUserExecutionPolicy>>) => {
        const next = rowsFromPolicy(body.override);
        setRows(next);
        setSaved(next);
        setEffective(body.effective);
        setMeta({updated_by: body.updated_by, updated_at: body.updated_at});
    };

    const run = async (call: () => ReturnType<typeof viewerApi.adminGetUserExecutionPolicy>) => {
        setBusy(true);
        try {
            apply(await call());
            setError(null);
        } catch (e) {
            setError(e instanceof ApiError ? e.detail || e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    useEffect(() => {
        void run(() => viewerApi.adminGetUserExecutionPolicy(userSub));
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [userSub]);

    const errors = useMemo(() => rowErrors(rows), [rows]);
    const dirty = !samePolicy(policyFromRows(rows), policyFromRows(saved));

    return (
        <div className="space-y-2" data-testid={`exec-override-${userSub}`}>
            <p className="text-gray-400">
                Rows here replace the global execution policy for this user only (Admin → Execution has the
                rest).
            </p>
            {error && <div className="text-red-300">{error}</div>}
            <ExecutionPolicyTable
                rows={rows}
                onChange={setRows}
                disabled={busy}
                keepWildcard={false}
                initialFor={(op, qualifier) => lookupOrder(effective, op, qualifier)}
            />
            {errors.length > 0 && (
                <ul className="text-red-300 list-disc pl-5">
                    {errors.map((e) => (
                        <li key={e}>{e}</li>
                    ))}
                </ul>
            )}
            <div className="flex flex-wrap items-center gap-2">
                <button
                    type="button"
                    className="bg-blue-700 hover:bg-blue-600 px-2 py-0.5 rounded-sm disabled:opacity-50"
                    disabled={busy || !dirty || errors.length > 0}
                    onClick={() =>
                        void run(() => viewerApi.adminUpdateUserExecutionPolicy(userSub, policyFromRows(rows)))
                    }
                >
                    Save override
                </button>
                <button
                    type="button"
                    className="bg-gray-700 hover:bg-gray-600 px-2 py-0.5 rounded-sm disabled:opacity-50"
                    disabled={busy || saved.length === 0}
                    onClick={() => void run(() => viewerApi.adminUpdateUserExecutionPolicy(userSub, null))}
                >
                    Clear override
                </button>
                {meta.updated_at && saved.length > 0 && (
                    <span className="text-[11px] text-gray-500">
                        Updated {fmtTimestamp(meta.updated_at)}
                        {meta.updated_by ? ` by ${meta.updated_by}` : ""}
                    </span>
                )}
            </div>
        </div>
    );
};

export default UserExecutionOverride;
