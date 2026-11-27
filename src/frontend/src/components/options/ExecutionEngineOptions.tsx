import React, {useEffect} from "react";

import {allowedEngines, engineOrder} from "@/services/compute/executionPolicy";
import {
    ENGINE_LABELS,
    EXEC_OPERATIONS,
    OPERATION_LABELS,
    type ExecEngine,
} from "@/services/compute/executionPolicyModel";
import {hasAnyWorkerAccess, loadWorkerAccess, useWorkerAccessStore} from "@/services/compute/workerAccess";
import {useExecutionPolicyStore, useExecutionPreferenceStore} from "@/state/executionPolicyStore";

// Where each kind of work runs. An administrator sets the engines each operation
// may use and their order (Admin Panel -> Execution, per user in Admin -> Users);
// here the user may only pick which of the ALLOWED engines goes first. The server
// worker is allowed only with worker pool access.
// One literal: the bundle-provenance check matches bundle strings verbatim
// against src/frontend, and the minifier folds concatenated literals.
const NO_ACCESS_NOTICE =
    "Your account has no worker pool access, so the server worker is not offered. Ask an administrator to enable it in the Admin Panel (Users).";

const ExecutionEngineOptions: React.FC = () => {
    const preferred = useExecutionPreferenceStore((s) => s.preferred);
    const setPreferred = useExecutionPreferenceStore((s) => s.setPreferred);
    const policy = useExecutionPolicyStore((s) => s.policy);
    const access = useWorkerAccessStore((s) => s.policy);
    const loaded = useWorkerAccessStore((s) => s.loaded);
    useEffect(() => {
        void loadWorkerAccess(true);
    }, []);

    return (
        <div className="space-y-2" data-testid="execution-engine-options">
            <p className="text-xs text-gray-400 leading-tight">
                The engines each operation may use and their order are set by an administrator. Pick
                which allowed engine runs first; the others remain fallbacks.
            </p>
            {EXEC_OPERATIONS.map((op) => {
                const allowed = allowedEngines(op, "*", {policy, workerAccess: access});
                const order = engineOrder(op, "*", {policy, workerAccess: access, preference: preferred[op] ?? null});
                const current = preferred[op] && allowed.includes(preferred[op] as ExecEngine) ? preferred[op] : "";
                return (
                    <label key={op} className="flex flex-col gap-0.5 text-sm">
                        <span>{OPERATION_LABELS[op]}</span>
                        <select
                            className="bg-gray-800 border border-gray-700 rounded-sm px-2 py-1 text-white text-xs"
                            data-testid={`execution-engine-${op}`}
                            value={current}
                            disabled={allowed.length < 2}
                            onChange={(e) => setPreferred(op, (e.target.value || null) as ExecEngine | null)}
                        >
                            <option value="">As configured</option>
                            {allowed.map((engine) => (
                                <option key={engine} value={engine}>
                                    {ENGINE_LABELS[engine]} first
                                </option>
                            ))}
                        </select>
                        <span className="text-[11px] text-gray-500">
                            {order.length ? order.map((e) => ENGINE_LABELS[e]).join(" → ") : "no engine allowed"}
                            {" (by default; some file types have their own order)"}
                        </span>
                    </label>
                );
            })}
            {loaded && !hasAnyWorkerAccess(access) && <p className="text-xs text-gray-400">{NO_ACCESS_NOTICE}</p>}
        </div>
    );
};

export default ExecutionEngineOptions;
