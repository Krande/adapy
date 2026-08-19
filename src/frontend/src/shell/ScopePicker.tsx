import React from "react";
import {Select} from "@/components/ui";
import {scopeUrlPart, useScopeStore, type ScopeOption} from "@/state/scopeStore";
import {requestScopeChange} from "@/utils/scope/requestScopeChange";
import {runtime} from "@/runtime/config";

// Which scope you are looking at, in the title bar.
//
// In the classic UI this lived inside the Options drawer — three clicks from the file
// list it governs, and invisible while you were using it. Scope is the single most
// consequential piece of context in a multi-project deployment: every file, conversion
// and job is scoped, and not knowing which one you are in is how work lands in the wrong
// project. It belongs in persistent chrome.
//
// The switch goes through requestScopeChange, shared with the classic drawer, so both
// paths perform the same teardown AND the same "this will unload your model" guard.
// Moving the control here made it one click from anywhere, which is what a destructive
// action least wants to be — hence the guard.

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
                    if (!picked) return;
                    // The select has already moved to the new option; if the user backs
                    // out of the confirmation we have to put it back, or the title bar
                    // claims a scope we are not in.
                    const el = e.currentTarget;
                    void requestScopeChange(picked as ScopeOption).then((switched) => {
                        if (!switched) el.value = current ? scopeUrlPart(current) : "";
                    });
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
