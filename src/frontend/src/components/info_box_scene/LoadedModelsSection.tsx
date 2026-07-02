import React, {useState} from "react";
import {useModelState, loadedSourceGroups} from "@/state/modelState";
import {unload_any_source} from "@/utils/scene/handlers/unload_any_source";
import {requestRender} from "@/state/perfStore";
import ViewIcon from "../icons/ViewIcon";
import ViewOffIcon from "../icons/ViewOffIcon";

// Flat list of every loaded model — the "layers panel" answer to
// models living in deeply nested storage folders: whatever the prefix
// depth, they're all toggleable/unloadable from one compact place.
//
// Eye button = scene visibility (group.visible flip; model stays in
// GPU memory). × = full unload (frees the memory, clears tree-view).
// Streaming-FEA models register their replace_model group too, so they
// get the same toggle; the group-less fallback below only applies to a
// source that somehow landed in loadedSourceNames without a group.

const LoadedModelsSection: React.FC = () => {
    const loadedSourceNames = useModelState((s) => s.loadedSourceNames);
    // group.visible lives outside React — tick forces a re-render
    // after a toggle so the icon state tracks it.
    const [, setTick] = useState(0);
    const [busyName, setBusyName] = useState<string | null>(null);

    if (loadedSourceNames.size === 0) {
        return <div className="text-xs italic text-gray-400">No models loaded.</div>;
    }

    const toggleVisibility = (name: string) => {
        const group = loadedSourceGroups.get(name);
        if (!group) return;
        group.visible = !group.visible;
        requestRender();
        setTick((t) => t + 1);
    };

    const onUnload = async (name: string) => {
        if (busyName) return;
        setBusyName(name);
        try {
            await unload_any_source(name);
        } catch (err) {
            console.error("unload failed", name, err);
        } finally {
            setBusyName(null);
        }
    };

    return (
        <ul className="flex flex-col divide-y divide-gray-700/40 text-xs">
            {Array.from(loadedSourceNames).map((name) => {
                const group = loadedSourceGroups.get(name);
                const visible = group ? group.visible : true;
                const basename = name.split("/").pop() ?? name;
                return (
                    <li key={name} className="flex items-center gap-1.5 py-1">
                        {group ? (
                            <button
                                type="button"
                                onClick={() => toggleVisibility(name)}
                                className={
                                    "shrink-0 p-1 rounded-sm cursor-pointer hover:bg-gray-700 " +
                                    (visible ? "text-blue-400" : "text-gray-500")
                                }
                                title={visible
                                    ? "Hide in scene (stays loaded)"
                                    : "Show in scene"}
                                aria-label={visible ? `Hide ${basename}` : `Show ${basename}`}
                            >
                                {visible
                                    ? <ViewIcon width="16px" height="16px"/>
                                    : <ViewOffIcon width="16px" height="16px"/>}
                            </button>
                        ) : (
                            // Fallback: a source with no registered group
                            // (can't flip visibility — only unload).
                            <span className="shrink-0 p-1 text-blue-400" title="Loaded (no visibility toggle)">
                                <ViewIcon width="16px" height="16px"/>
                            </span>
                        )}
                        <span
                            className={"flex-1 min-w-0 truncate " + (visible ? "" : "text-gray-500")}
                            title={name}
                        >
                            {basename}
                        </span>
                        <button
                            type="button"
                            onClick={() => void onUnload(name)}
                            disabled={busyName !== null}
                            className={
                                "shrink-0 px-1.5 py-0.5 rounded-sm cursor-pointer text-gray-400 " +
                                "hover:text-red-300 hover:bg-gray-700 disabled:opacity-50"
                            }
                            title="Unload from scene (frees memory)"
                            aria-label={`Unload ${basename}`}
                        >
                            {busyName === name ? "…" : "×"}
                        </button>
                    </li>
                );
            })}
        </ul>
    );
};

export default LoadedModelsSection;
