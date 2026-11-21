// Scene panel "Saved states": save what the scene shows -- the models, the elements loaded as a
// group's members, what is hidden and selected, the clash check with its markers, the active detail
// model, the camera -- under a name everyone in the scope sees, and put a saved one back or delete it
// (`@/state/savedSceneStatesStore`, the one restore path `utils/scene/applySceneState`).

import React, {useEffect, useState} from "react";

import {useMeStore} from "@/state/meStore";
import {useSavedSceneStatesStore} from "@/state/savedSceneStatesStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {sceneStateSummary} from "@/utils/scene/savedSceneStates";

const BTN =
    "rounded-sm px-1.5 py-0.5 text-[11px] bg-gray-700 hover:bg-gray-600 text-gray-100 disabled:opacity-50 disabled:cursor-not-allowed";
const BTN_PRIMARY =
    "rounded-sm px-2 py-0.5 text-[11px] bg-blue-700 hover:bg-blue-600 text-gray-100 disabled:opacity-50 disabled:cursor-not-allowed";

const SaveStateForm: React.FC<{scope: string; onDone: () => void}> = ({scope, onDone}) => {
    const [name, setName] = useState("");
    const busy = useSavedSceneStatesStore((s) => s.busyId === "new");
    const save = useSavedSceneStatesStore((s) => s.save);
    const me = useMeStore((s) => s.displayName || s.email);
    return (
        <form
            className="flex items-center gap-1 rounded-sm bg-gray-800/70 p-1.5 text-[11px]"
            onSubmit={(e) => {
                e.preventDefault();
                void save(scope, name, me).then((saved) => saved && onDone());
            }}
        >
            <input
                autoFocus
                type="text"
                aria-label="State name"
                placeholder="State name"
                className="flex-1 min-w-0 rounded-sm bg-gray-700 border border-gray-600 px-1 py-0.5 text-gray-100 text-base sm:text-[11px]"
                value={name}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => e.key === "Escape" && onDone()}
            />
            <button type="submit" className={BTN_PRIMARY} disabled={busy || !name.trim()}>
                {busy ? "saving…" : "Save"}
            </button>
            <button type="button" className={BTN} onClick={onDone}>
                Cancel
            </button>
        </form>
    );
};

const SavedStatesSection: React.FC = () => {
    const scope = scopeUrlPart(useScopeStore((s) => s.current));
    const states = useSavedSceneStatesStore((s) => s.states);
    const listScope = useSavedSceneStatesStore((s) => s.scope);
    const busyId = useSavedSceneStatesStore((s) => s.busyId);
    const error = useSavedSceneStatesStore((s) => s.error);
    const notes = useSavedSceneStatesStore((s) => s.notes);
    const load = useSavedSceneStatesStore((s) => s.load);
    const restore = useSavedSceneStatesStore((s) => s.restore);
    const remove = useSavedSceneStatesStore((s) => s.remove);
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        void load(scope);
    }, [scope, load]);

    const shown = listScope === scope ? states : [];
    return (
        <section className="flex flex-col gap-1 text-[11px]" aria-label="Saved states">
            {saving ? (
                <SaveStateForm scope={scope} onDone={() => setSaving(false)} />
            ) : (
                <div>
                    <button
                        type="button"
                        className={BTN}
                        disabled={busyId !== null}
                        title="Save the loaded models, the hidden and selected objects, the clash check, the detail model and the camera, for everyone in this scope"
                        onClick={() => setSaving(true)}
                    >
                        Save state…
                    </button>
                </div>
            )}
            {error && <div className="text-red-300/80">{error}</div>}
            {notes.map((n) => (
                <div key={n} className="text-amber-200/80">
                    {n}
                </div>
            ))}
            {shown.length === 0 ? (
                <div className="text-gray-500">No saved states in this scope.</div>
            ) : (
                <ul className="max-h-56 overflow-y-auto divide-y divide-gray-800 rounded-sm border border-gray-800">
                    {[...shown].reverse().map((s) => (
                        <li key={s.id} className="flex items-start gap-1 px-2 py-1" data-scene-state={s.id}>
                            <div className="flex-1 min-w-0">
                                <div className="truncate text-gray-200" title={s.name}>
                                    {s.name}
                                </div>
                                <div className="truncate text-gray-500">
                                    {s.created_at.replace("T", " ").slice(0, 16)}
                                    {s.created_by ? ` · ${s.created_by}` : ""} · {sceneStateSummary(s.state)}
                                </div>
                            </div>
                            <button
                                type="button"
                                className={BTN}
                                disabled={busyId !== null}
                                title="Load what this state holds that is not in the scene, and put back its view, clash check, detail model and camera"
                                onClick={() => void restore(scope, s.id)}
                            >
                                {busyId === s.id ? "…" : "Restore"}
                            </button>
                            <button
                                type="button"
                                className={BTN}
                                disabled={busyId !== null}
                                title="Delete this saved state"
                                onClick={() => {
                                    if (!window.confirm(`Delete the saved state "${s.name}"? This cannot be undone.`)) return;
                                    void remove(scope, s.id);
                                }}
                            >
                                Delete
                            </button>
                        </li>
                    ))}
                </ul>
            )}
        </section>
    );
};

export default SavedStatesSection;
