// The Scene menu's "Add to group…": a small prompt at the menu's position that puts the rows the
// menu was opened on into a saved group -- an existing one picked from the list, or a new one
// named here. The menu closes on any click, so the prompt has its own state and host.

import React, {useEffect, useMemo, useRef, useState} from "react";
import {create} from "zustand";

import {AnchoredPopover} from "@/components/common/AnchoredPopover";
import {useMeStore} from "@/state/meStore";
import {useSavedGroupsStore} from "@/state/savedGroupsStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {captureRows} from "@/utils/groups/groupScene";
import type {TreeNodeData} from "./CustomNode";

const BTN =
    "rounded-sm px-1.5 py-0.5 text-[11px] bg-gray-700 hover:bg-gray-600 text-gray-100 disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_PRIMARY =
    "rounded-sm px-2 py-0.5 text-[11px] bg-blue-700 hover:bg-blue-600 text-gray-100 disabled:opacity-50 disabled:cursor-not-allowed";
const FIELD =
    "w-full min-w-0 rounded-sm bg-gray-700 border border-gray-600 px-1 py-0.5 text-gray-100 text-base sm:text-[11px]";

interface PromptState {
    rows: readonly TreeNodeData[];
    x: number;
    y: number;
}

interface AddToGroupPromptStore {
    prompt: PromptState | null;
    /** The last outcome, shown briefly where the prompt was. */
    notice: {text: string; x: number; y: number} | null;
    open: (p: PromptState) => void;
    close: () => void;
    done: (text: string) => void;
}

let noticeTimer: ReturnType<typeof setTimeout> | null = null;

export const useAddToGroupPromptStore = create<AddToGroupPromptStore>()((set, get) => ({
    prompt: null,
    notice: null,
    open: (prompt) => set({prompt, notice: null}),
    close: () => set({prompt: null}),
    done: (text) => {
        const p = get().prompt;
        set({prompt: null, notice: p ? {text, x: p.x, y: p.y} : null});
        if (noticeTimer) clearTimeout(noticeTimer);
        noticeTimer = setTimeout(() => set({notice: null}), 2500);
    },
}));

/** Why the menu item is disabled for `rows`, or null when it can add something. */
export function addToGroupBlocker(rows: readonly TreeNodeData[]): string | null {
    const captured = captureRows(rows);
    return captured.members.length ? null : captured.reason ?? "Nothing in the selection can be stored in a group.";
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

const AddToGroupPrompt: React.FC<PromptState & {onClose: () => void; onDone: (text: string) => void}> = ({
    rows,
    x,
    y,
    onClose,
    onDone,
}) => {
    const scope = scopeUrlPart(useScopeStore((s) => s.current));
    const captured = useMemo(() => captureRows(rows), [rows]);
    const groups = useSavedGroupsStore((s) => s.groups);
    const groupsScope = useSavedGroupsStore((s) => s.scope);
    const loading = useSavedGroupsStore((s) => s.loading);
    const busy = useSavedGroupsStore((s) => s.busy);
    const storeError = useSavedGroupsStore((s) => s.error);
    const load = useSavedGroupsStore((s) => s.load);
    const addMembers = useSavedGroupsStore((s) => s.addMembers);
    const create = useSavedGroupsStore((s) => s.create);
    const me = useMeStore((s) => s.displayName || s.email);

    const [mode, setMode] = useState<"existing" | "new">("existing");
    const [groupId, setGroupId] = useState("");
    const [name, setName] = useState("");
    const [error, setError] = useState<string | null>(null);
    // Set once the list for this scope has been read: until then "no groups" means "not read yet".
    const [loaded, setLoaded] = useState(false);

    useEffect(() => {
        let live = true;
        void load(scope).then(() => live && setLoaded(true));
        return () => {
            live = false;
        };
    }, [scope, load]);

    const sorted = useMemo(
        () => (groupsScope === scope ? [...groups] : []).sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true})),
        [groups, groupsScope, scope],
    );
    // No groups to pick from: straight to naming a new one.
    useEffect(() => {
        if (loaded && sorted.length === 0) setMode("new");
    }, [loaded, sorted.length]);
    useEffect(() => {
        if (!sorted.some((g) => g.id === groupId)) setGroupId(sorted[0]?.id ?? "");
    }, [sorted, groupId]);

    const nameRef = useRef<HTMLInputElement>(null);
    const selectRef = useRef<HTMLSelectElement>(null);
    useEffect(() => {
        (mode === "new" ? nameRef.current : selectRef.current)?.focus();
    }, [mode, loaded]);

    // The anchor the popover hangs off: a zero-size point where the menu was.
    const anchorRef = useRef<HTMLSpanElement>(null);

    const n = captured.members.length;
    const canSubmit =
        !busy && n > 0 && (mode === "new" ? !!name.trim() : !!groupId && sorted.length > 0);

    const submit = async () => {
        if (!canSubmit) return;
        setError(null);
        if (mode === "new") {
            const group = await create(scope, name, captured.members, me);
            if (group) onDone(`Created "${group.name}" with ${plural(n, "member")}.`);
            else setError(useSavedGroupsStore.getState().error ?? "The group could not be saved.");
            return;
        }
        const out = await addMembers(scope, groupId, captured.members);
        if (!out) {
            setError(useSavedGroupsStore.getState().error ?? "The group could not be saved.");
            return;
        }
        onDone(
            out.added === 0
                ? `"${out.group.name}" already holds ${n === 1 ? "this" : "all of these"}.`
                : `Added ${plural(out.added, "member")} to "${out.group.name}"${out.added < n ? ` (${n - out.added} already in it)` : ""}.`,
        );
    };

    // A failed read shows too: a write re-reads first, so it would fail the same way.
    const shownError = error ?? (loaded ? storeError : null);

    const onKeyDown = (e: React.KeyboardEvent) => {
        // Enter on a focused button is that button's click (Cancel must not submit).
        if (e.key === "Enter" && !(e.target instanceof HTMLButtonElement)) {
            e.preventDefault();
            void submit();
        }
        // Escape: AnchoredPopover closes on it.
    };

    return (
        <>
            <span ref={anchorRef} className="fixed w-0 h-0 pointer-events-none" style={{left: x, top: y}}/>
            <AnchoredPopover anchorRef={anchorRef} onClose={onClose} ariaLabel="Add to group">
                <form
                    className="flex w-64 flex-col gap-1 rounded-sm border border-gray-700 bg-gray-800 p-1.5 text-[11px] text-gray-100 shadow-lg"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void submit();
                    }}
                    onKeyDown={onKeyDown}
                    onContextMenu={(e) => e.preventDefault()}
                >
                    <div className="text-gray-300">
                        Add {plural(n, "member")} to a group
                    </div>
                    {captured.skipped.length > 0 && (
                        <div className="text-amber-200">
                            Left out {captured.skipped.length}: {captured.skipped[0].name} — {captured.skipped[0].reason}
                            {captured.skipped.length > 1 ? " (and others)" : ""}.
                        </div>
                    )}
                    <div className="flex gap-3">
                        <label className={`flex items-center gap-1 ${sorted.length ? "cursor-pointer" : "opacity-50"}`}>
                            <input
                                type="radio"
                                name="add-to-group-mode"
                                checked={mode === "existing"}
                                disabled={sorted.length === 0}
                                onChange={() => setMode("existing")}
                            />
                            Existing group
                        </label>
                        <label className="flex items-center gap-1 cursor-pointer">
                            <input
                                type="radio"
                                name="add-to-group-mode"
                                checked={mode === "new"}
                                onChange={() => setMode("new")}
                            />
                            New group
                        </label>
                    </div>
                    {mode === "existing" ? (
                        !loaded && loading ? (
                            <div className="text-gray-400">Reading the saved groups…</div>
                        ) : sorted.length === 0 ? (
                            <div className="text-gray-500">No saved groups yet.</div>
                        ) : (
                            <select
                                ref={selectRef}
                                aria-label="Group"
                                className={FIELD}
                                value={groupId}
                                onChange={(e) => setGroupId(e.target.value)}
                            >
                                {sorted.map((g) => (
                                    <option key={g.id} value={g.id}>
                                        {g.name} ({plural(g.members.length, "member")})
                                    </option>
                                ))}
                            </select>
                        )
                    ) : (
                        <input
                            ref={nameRef}
                            type="text"
                            aria-label="New group name"
                            placeholder="Group name"
                            className={FIELD}
                            value={name}
                            onChange={(e) => setName(e.target.value)}
                        />
                    )}
                    {shownError && <div className="text-red-300 break-words">{shownError}</div>}
                    <div className="flex justify-end gap-1">
                        <button type="button" className={BTN} onClick={onClose}>
                            Cancel
                        </button>
                        <button type="submit" className={BTN_PRIMARY} disabled={!canSubmit}>
                            {busy ? "saving…" : mode === "new" ? "Create" : "Add"}
                        </button>
                    </div>
                </form>
            </AnchoredPopover>
        </>
    );
};

/** The prompt and its short-lived outcome note, rendered once beside the Scene menu. */
export const AddToGroupPromptHost: React.FC = () => {
    const prompt = useAddToGroupPromptStore((s) => s.prompt);
    const notice = useAddToGroupPromptStore((s) => s.notice);
    const close = useAddToGroupPromptStore((s) => s.close);
    const done = useAddToGroupPromptStore((s) => s.done);
    return (
        <>
            {prompt && <AddToGroupPrompt {...prompt} onClose={close} onDone={done}/>}
            {notice && !prompt && (
                <div
                    role="status"
                    className="fixed z-[70] max-w-xs rounded-sm border border-gray-700 bg-gray-800 px-2 py-1 text-[11px] text-green-300 shadow-lg pointer-events-none"
                    style={{
                        left: Math.max(8, Math.min(notice.x, window.innerWidth - 328)),
                        top: Math.max(8, Math.min(notice.y, window.innerHeight - 48)),
                    }}
                >
                    {notice.text}
                </div>
            )}
        </>
    );
};

export default AddToGroupPrompt;
