import React, {useState} from "react";

import {useSceneRestoreStore} from "@/state/sceneRestoreStore";
import {forgetRestoredScene} from "@/utils/scene/sceneRestore";

const SceneRestoreOptions: React.FC = () => {
    const enabled = useSceneRestoreStore((s) => s.enabled);
    const setEnabled = useSceneRestoreStore((s) => s.setEnabled);
    const progress = useSceneRestoreStore((s) => s.progress);
    const [forgotten, setForgotten] = useState(false);

    return (
        <div className="space-y-2">
            <label className="flex items-start space-x-2">
                <input type="checkbox" className="mt-1" checked={enabled} onChange={() => setEnabled(!enabled)}/>
                <span className="leading-tight">
                    Restore loaded models after reload
                    <span className="block text-xs text-gray-400">
                        Models loaded from the file and asset browsers are remembered per scope in
                        this browser, with the camera, and loaded again when the page reloads.
                    </span>
                </span>
            </label>
            {progress && (
                <div className="text-xs text-gray-300">
                    {progress.running ? "Restoring" : "Restored"} {progress.done - progress.failed} of {progress.total}
                    {progress.failed > 0 && `, ${progress.failed} failed (kept for the next reload; Forget to drop)`}
                </div>
            )}
            <button
                type="button"
                className="bg-gray-700 hover:bg-gray-600 text-white text-xs px-2 py-1 rounded-sm"
                title="Forget the remembered models of this scope; what is loaded now stays"
                onClick={() => {
                    forgetRestoredScene();
                    setForgotten(true);
                }}
            >
                Forget restored scene
            </button>
            {forgotten && <span className="ml-2 text-xs text-gray-400">Forgotten.</span>}
        </div>
    );
};

export default SceneRestoreOptions;
