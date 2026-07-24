import React from "react";

import {useEquipmentCatalogStore} from "@/state/equipmentCatalogStore";
import type {SystemTemplateType} from "@/services/viewerApi";

// Per-scope system-template catalog admin panel. Reusable service-system
// definitions (a named CoolingWater piping system, a PowerFeed electrical
// system, ...) with category/type/medium/voltage and the routed-segment
// rendering knobs. Feeds the cellbuilder systems inspector.

const btn = "px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50 hover:bg-blue-500";
const btnDanger = "px-1.5 rounded-sm bg-red-700/70 text-white hover:bg-red-600";
const inputCls = "text-gray-100 bg-gray-700 border border-gray-600 rounded-sm px-1 py-0.5 w-full";

const TYPES: SystemTemplateType[] = ["piping", "duct", "cable", "electrical"];

const SystemAdminPanel: React.FC = () => {
    const {systemTemplates, selectedSystemId, systemDraft: draft, systemDirty, systemBusy, systemError} =
        useEquipmentCatalogStore();
    const store = useEquipmentCatalogStore;
    const [newName, setNewName] = React.useState("");

    return (
        <div className="flex flex-col gap-2 text-xs text-white p-2 bg-gray-900/80 rounded-md min-w-[300px] max-w-[380px] pointer-events-auto max-h-[80vh] overflow-y-auto">
            <div className="flex items-center gap-2">
                <span className="font-bold">System catalog</span>
                <button
                    className="ml-auto px-1 rounded-sm hover:bg-gray-500/40"
                    title="Refresh"
                    onClick={() => void store.getState().refreshSystems()}
                >
                    ⟳
                </button>
                <button
                    className="px-1 rounded-sm hover:bg-gray-500/40"
                    title="Close"
                    onClick={() => store.setState({systemPanelOpen: false})}
                >
                    ✕
                </button>
            </div>

            {systemError && <div className="text-red-400">{systemError}</div>}

            <div className="flex gap-1">
                <input
                    className={inputCls}
                    placeholder="New system template name…"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === "Enter" && newName.trim()) {
                            void store.getState().createSystem(newName);
                            setNewName("");
                        }
                    }}
                />
                <button
                    className={btn}
                    disabled={systemBusy || !newName.trim()}
                    onClick={() => {
                        void store.getState().createSystem(newName);
                        setNewName("");
                    }}
                >
                    Add
                </button>
            </div>

            <div className="flex flex-col gap-0.5 max-h-32 overflow-y-auto">
                {systemTemplates.length === 0 && <div className="text-gray-500 italic">No system templates yet.</div>}
                {systemTemplates.map((t) => (
                    <div
                        key={t.id}
                        className={
                            "flex items-center gap-1 px-1.5 py-0.5 rounded-sm cursor-pointer " +
                            (t.id === selectedSystemId ? "bg-blue-800/60" : "hover:bg-gray-700/60")
                        }
                        onClick={() => void store.getState().selectSystem(t.id)}
                    >
                        <span className="truncate flex-1">{t.name}</span>
                        <span className="text-gray-400 font-mono text-[10px]">{t.slug}</span>
                        <button
                            className={btnDanger}
                            title="Delete"
                            onClick={(e) => {
                                e.stopPropagation();
                                void store.getState().deleteSystem(t.id);
                            }}
                        >
                            🗑
                        </button>
                    </div>
                ))}
            </div>

            {draft && (
                <div className="flex flex-col gap-2 border-t border-gray-700 pt-2">
                    <label className="flex flex-col gap-0.5">
                        <span className="text-gray-400">Name</span>
                        <input
                            className={inputCls}
                            value={draft.name}
                            onChange={(e) => store.getState().setSystemMeta({name: e.target.value})}
                        />
                    </label>
                    <label className="flex flex-col gap-0.5">
                        <span className="text-gray-400">Description</span>
                        <input
                            className={inputCls}
                            value={draft.description ?? ""}
                            onChange={(e) => store.getState().setSystemMeta({description: e.target.value})}
                        />
                    </label>
                    <div className="grid grid-cols-2 gap-1">
                        <label className="flex flex-col gap-0.5">
                            <span className="text-gray-400">Type</span>
                            <select
                                className={inputCls}
                                value={draft.doc.type}
                                onChange={(e) => store.getState().setSystemDoc({type: e.target.value as SystemTemplateType})}
                            >
                                {TYPES.map((t) => (
                                    <option key={t} value={t}>
                                        {t}
                                    </option>
                                ))}
                            </select>
                        </label>
                        <label className="flex flex-col gap-0.5">
                            <span className="text-gray-400">Medium</span>
                            <input
                                className={inputCls}
                                value={draft.doc.medium ?? ""}
                                placeholder="water, air, …"
                                onChange={(e) => store.getState().setSystemDoc({medium: e.target.value || null})}
                            />
                        </label>
                    </div>
                    <div className="grid grid-cols-3 gap-1">
                        <label className="flex flex-col gap-0.5">
                            <span className="text-gray-400">Voltage [V]</span>
                            <input
                                type="number"
                                step={10}
                                className={inputCls}
                                value={draft.doc.voltage ?? ""}
                                disabled={draft.doc.type !== "electrical"}
                                onChange={(e) =>
                                    store.getState().setSystemDoc({voltage: e.target.value ? parseInt(e.target.value, 10) : null})
                                }
                            />
                        </label>
                        <label className="flex flex-col gap-0.5">
                            <span className="text-gray-400">Pipe r [m]</span>
                            <input
                                type="number"
                                step={0.01}
                                className={inputCls}
                                value={draft.doc.pipe_radius}
                                onChange={(e) => store.getState().setSystemDoc({pipe_radius: parseFloat(e.target.value) || 0})}
                            />
                        </label>
                        <label className="flex flex-col gap-0.5">
                            <span className="text-gray-400">Pipe wt [m]</span>
                            <input
                                type="number"
                                step={0.001}
                                className={inputCls}
                                value={draft.doc.pipe_wt}
                                onChange={(e) => store.getState().setSystemDoc({pipe_wt: parseFloat(e.target.value) || 0})}
                            />
                        </label>
                    </div>

                    <div className="flex items-center gap-2 border-t border-gray-700 pt-2">
                        <span className="text-gray-400">rev {draft.revision}</span>
                        {systemDirty && <span className="text-amber-400">unsaved</span>}
                        <button
                            className={btn + " ml-auto"}
                            disabled={!systemDirty || systemBusy}
                            onClick={() => void store.getState().saveSystem()}
                        >
                            Save
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};

export default SystemAdminPanel;
