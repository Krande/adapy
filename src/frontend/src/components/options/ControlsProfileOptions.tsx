import React, {useRef, useState} from "react";

import {useCameraControlsStore} from "@/state/cameraControlsStore";
import {allProfiles, currentKeys, profileById, useControlsProfileStore} from "@/state/controlsProfileStore";
import {layoutStore} from "@/layout/layoutStore";
import {useStore} from "zustand";
import {exportProfiles, profileMatches} from "@/utils/camera/controlProfiles";

const SELECT = "bg-gray-700 text-white text-xs rounded-sm px-1 py-0.5";
const BUTTON = "bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white text-xs rounded-sm px-1.5 py-0.5";
const INPUT = "bg-gray-800 text-white text-xs rounded-sm px-1 py-0.5 border border-gray-600 flex-1 min-w-0";

/** Save `text` as a file named `name` (an anchor click: no dialog of the page's own). */
function saveFile(name: string, text: string): void {
    const url = URL.createObjectURL(new Blob([text], {type: "application/json"}));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
}

const fileNameOf = (name: string) => `${name.replace(/[^\w.-]+/g, "_") || "controls"}.controls.json`;

/** The controls profile: which one is chosen, and making, renaming, deleting, exporting and importing the
 *  user's own. The camera mapping below it edits the chosen profile when it is one of the user's. */
const ControlsProfileOptions: React.FC = () => {
    const custom = useControlsProfileStore((s) => s.custom);
    const activeId = useControlsProfileStore((s) => s.activeId);
    const mapping = useCameraControlsStore((s) => s.mapping);
    const keyOverrides = useStore(layoutStore, (s) => s.prefs.keyOverrides);
    const store = useControlsProfileStore.getState();
    const profiles = allProfiles(custom);
    const active = profileById(activeId, custom);
    const modified = !!active && !profileMatches(active, mapping, keyOverrides);
    const [naming, setNaming] = useState<null | "new" | "rename">(null);
    const [name, setName] = useState("");
    const [withKeys, setWithKeys] = useState(true);
    const [message, setMessage] = useState<string | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    const startNaming = (kind: "new" | "rename") => {
        setNaming(kind);
        setMessage(null);
        setName(kind === "rename" && active ? active.name : active ? `${active.name} (mine)` : "My controls");
    };
    const confirmName = () => {
        if (naming === "new") {
            store.createFromCurrent(name, withKeys);
            setNaming(null);
        } else if (naming === "rename" && active) {
            const why = store.rename(active.id, name);
            setMessage(why);
            if (!why) setNaming(null);
        }
    };
    const onImport = async (file: File | undefined) => {
        if (!file) return;
        try {
            const n = store.importText(await file.text());
            setMessage(`Imported ${n} profile${n === 1 ? "" : "s"}.`);
        } catch (e) {
            setMessage(e instanceof Error ? e.message : String(e));
        }
        if (fileRef.current) fileRef.current.value = "";
    };

    return (
        <div className="space-y-2 text-xs" data-controls-profile="">
            <label className="block space-y-1">
                <span className="text-gray-300">Controls profile</span>
                <select className={`${SELECT} w-full`} data-option="controls-profile" value={activeId}
                    onChange={(e) => { setMessage(null); store.choose(e.target.value); }}>
                    {profiles.map((p) => (
                        <option key={p.id} value={p.id}>{`${p.name}${p.id === activeId && modified ? " (modified)" : ""}`}</option>
                    ))}
                </select>
            </label>
            <div className="text-gray-400">
                {active?.builtIn
                    ? modified
                        ? "Changed from this built-in profile: save it as a new profile to keep the changes under a name."
                        : "A built-in profile. Changing the controls below makes it modified; save it as a new profile to keep it."
                    : active?.keys === undefined
                        ? "Your profile: the camera controls below change it. It does not hold keyboard shortcuts."
                        : "Your profile: the camera controls below and the shortcut keys change it."}
            </div>
            {naming ? (
                <div className="space-y-1">
                    <div className="flex items-center gap-1">
                        <input className={INPUT} aria-label="Profile name" data-option="controls-profile-name" value={name} autoFocus
                            onChange={(e) => setName(e.target.value)}
                            onKeyDown={(e) => { if (e.key === "Enter") confirmName(); if (e.key === "Escape") setNaming(null); }}/>
                        <button type="button" className={BUTTON} onClick={confirmName}>{naming === "new" ? "Save" : "Rename"}</button>
                        <button type="button" className={BUTTON} onClick={() => setNaming(null)}>Cancel</button>
                    </div>
                    {naming === "new" && (
                        <label className="flex items-center space-x-2">
                            <input type="checkbox" data-option="controls-profile-with-keys" checked={withKeys} onChange={(e) => setWithKeys(e.target.checked)}/>
                            <span>Include the keyboard shortcuts</span>
                        </label>
                    )}
                </div>
            ) : (
                <div className="flex flex-wrap gap-1">
                    <button type="button" className={BUTTON} data-profile-action="new" onClick={() => startNaming("new")}>Save as new…</button>
                    <button type="button" className={BUTTON} data-profile-action="rename" disabled={!active || active.builtIn} onClick={() => startNaming("rename")}>Rename…</button>
                    <button type="button" className={BUTTON} data-profile-action="delete" disabled={!active || active.builtIn}
                        onClick={() => { if (active) { store.remove(active.id); setMessage(`Deleted "${active.name}".`); } }}>Delete</button>
                    <button type="button" className={BUTTON} data-profile-action="export" disabled={!active}
                        onClick={() => {
                            if (!active) return;
                            // What is in force, so an edited built-in exports as edited.
                            const current = {...active, mapping, ...(active.keys !== undefined ? {keys: currentKeys()} : {})};
                            saveFile(fileNameOf(active.name), exportProfiles([current]));
                        }}>Export</button>
                    <button type="button" className={BUTTON} data-profile-action="export-all" disabled={!custom.length}
                        onClick={() => saveFile("my.controls.json", exportProfiles(custom))}>Export mine</button>
                    <button type="button" className={BUTTON} data-profile-action="import" onClick={() => fileRef.current?.click()}>Import…</button>
                    <input ref={fileRef} type="file" accept=".json,application/json" className="hidden" data-option="controls-profile-import"
                        onChange={(e) => void onImport(e.target.files?.[0])}/>
                </div>
            )}
            {message && <div className="text-gray-300" data-profile-message="">{message}</div>}
        </div>
    );
};

export default ControlsProfileOptions;
