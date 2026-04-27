import React, {useState} from "react";
import {useScopeStore, ScopeOption} from "@/state/scopeStore";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";

// Compact scope selector. Lives in the menu bar to the left of the
// user menu in REST mode. Hidden when only one scope is available
// (e.g. before /api/me has resolved, or in single-scope deployments).
const ScopePicker: React.FC = () => {
    const {current, available, setCurrent} = useScopeStore();
    const [open, setOpen] = useState(false);

    if (available.length <= 1) return null;
    const label = current?.name ?? "Select scope";

    const choose = (s: ScopeOption) => {
        setCurrent(s);
        setOpen(false);
        // The server file list is scope-bound; re-request now that we
        // switched. Errors are non-fatal — the worst case is a stale
        // list that updates on the next refresh-button press.
        void request_list_of_files_from_server();
    };

    return (
        <div className="relative">
            <button
                className="bg-blue-700 hover:bg-blue-700/50 text-white px-3 py-2 rounded text-xs"
                onClick={() => setOpen((v) => !v)}
                title={`Current scope: ${label}`}
            >
                {label} ▾
            </button>
            {open && (
                <div className="absolute left-0 mt-1 min-w-[10rem] rounded bg-gray-800 text-white text-xs shadow-lg z-20">
                    {available.map((s) => {
                        const active =
                            current && current.kind === s.kind && current.id === s.id;
                        return (
                            <button
                                key={`${s.kind}:${s.id ?? ""}`}
                                className={`block w-full text-left px-3 py-2 hover:bg-gray-700 ${
                                    active ? "bg-gray-700/60" : ""
                                }`}
                                onClick={() => choose(s)}
                            >
                                {s.name}
                                <span className="ml-1 text-[10px] uppercase text-gray-400">
                                    {s.kind}
                                </span>
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default ScopePicker;
