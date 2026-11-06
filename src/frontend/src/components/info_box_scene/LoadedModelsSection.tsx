import React, {useEffect, useState} from "react";
import {useModelState, loadedSourceGroups} from "@/state/modelState";
import {useSceneModelNames} from "@/hooks/useSceneModelNames";
import {isMirroredWindow, useSceneActions} from "@/layout/sceneActions";
import {onSceneInfoChanged} from "@/layout/sceneInfoSignal";
import {useSceneMirrorStore} from "@/state/sceneMirrorStore";
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

/** `busyName` while Remove all runs: no source is named this. */
const REMOVING_ALL = "\u0000all";

/** `empty`: shown instead of the default line while no model is loaded. */
const LoadedModelsSection: React.FC<{empty?: React.ReactNode}> = ({empty}) => {
    const loadedSourceNames = useModelState((s) => s.loadedSourceNames);
    // Each model as the Scene tree calls it: its root row's label, so the two lists agree and both
    // follow the Names / IDs toggle. A model with no root row there falls back to its source name.
    const modelName = useSceneModelNames();
    // The scene actions: run here, or sent to the main window from a pop-out (which has no scene
    // groups: whether a model is shown is the main window's, mirrored).
    const actions = useSceneActions();
    const mirrored = isMirroredWindow();
    const modelVisible = useSceneMirrorStore((s) => s.modelVisible);
    // group.visible lives outside React — tick forces a re-render
    // after a toggle so the icon state tracks it.
    const [, setTick] = useState(0);
    useEffect(() => onSceneInfoChanged(() => setTick((t) => t + 1)), []);
    const [busyName, setBusyName] = useState<string | null>(null);

    if (loadedSourceNames.size === 0) {
        return empty !== undefined ? <>{empty}</> : <div className="text-xs italic text-gray-400">No models loaded.</div>;
    }

    const toggleVisibility = (name: string) => {
        const shown = mirrored ? modelVisible[name] : loadedSourceGroups.get(name)?.visible;
        if (shown === undefined) return;
        actions.setModelVisible(name, !shown);
        setTick((t) => t + 1);
    };

    const onUnload = async (name: string) => {
        if (busyName) return;
        setBusyName(name);
        try {
            actions.unloadModels([name]);
        } catch (err) {
            console.error("unload failed", name, err);
        } finally {
            setBusyName(null);
        }
    };

    const onRemoveAll = async () => {
        if (busyName) return;
        setBusyName(REMOVING_ALL);
        try {
            await actions.clearScene();
        } catch (err) {
            console.error("remove all failed", err);
        } finally {
            setBusyName(null);
        }
    };

    return (
        <>
        {/* The storage browser's Clear, from here: every model out of the scene. */}
        <div className="flex justify-end pb-1">
            <button
                type="button"
                onClick={() => void onRemoveAll()}
                disabled={busyName !== null}
                aria-busy={busyName === REMOVING_ALL}
                className={
                    "px-2 py-0.5 rounded-sm border border-gray-700 text-xs text-gray-300 cursor-pointer " +
                    "hover:text-red-300 hover:bg-gray-700 disabled:opacity-50"
                }
                title="Unload every model in the scene, and close any open procedural model"
            >
                {busyName === REMOVING_ALL ? "Removing…" : "Remove all"}
            </button>
        </div>
        <ul className="flex flex-col divide-y divide-gray-700/40 text-xs">
            {Array.from(loadedSourceNames).map((name) => {
                const known = mirrored ? modelVisible[name] : loadedSourceGroups.get(name)?.visible;
                const group = known !== undefined;
                const visible = known ?? true;
                const basename = modelName(name);
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
        </>
    );
};

export default LoadedModelsSection;
