import React from "react";
import {Select} from "@/components/ui";
import {scopeUrlPart, useScopeStore, type ScopeOption} from "@/state/scopeStore";
import {applyScopeChange} from "@/utils/scope/applyScopeChange";
import {runtime} from "@/runtime/config";

// Which scope you are looking at, in the title bar.
//
// In the classic UI this lived inside the Options drawer — three clicks from the file
// list it governs, and invisible while you were using it. Scope is the single most
// consequential piece of context in a multi-project deployment: every file, conversion
// and job is scoped, and not knowing which one you are in is how work lands in the wrong
// project. It belongs in persistent chrome.
//
// The switch itself goes through applyScopeChange, shared with the classic drawer, so
// both paths perform the same teardown.

export default function ScopePicker() {
    const current = useScopeStore((s) => s.current);
    const available = useScopeStore((s) => s.available);

    // WS/desktop has no scopes at all, and a single-scope deployment has nothing to pick.
    if (!runtime.isRestMode() || available.length <= 1) return null;

    return (
        <label className="flex items-center gap-1.5 shrink-0 text-xs text-content-muted">
            <span className="sr-only">Active scope</span>
            <Select
                fieldSize="sm"
                className="w-auto min-w-32"
                value={current ? scopeUrlPart(current) : ""}
                onChange={(e) => {
                    const picked = available.find((s) => scopeUrlPart(s) === e.target.value);
                    if (picked) applyScopeChange(picked as ScopeOption);
                }}
                title="Active scope — every file, conversion and job belongs to this"
            >
                {available.map((s) => (
                    <option key={scopeUrlPart(s)} value={scopeUrlPart(s)}>
                        {s.name}
                    </option>
                ))}
            </Select>
        </label>
    );
}
