import React, {useEffect, useRef, useState} from "react";
import {UPLOAD_TRIGGER_EVENT, uploadAcceptAttr, uploadFile} from "@/utils/scene/handlers/upload_source_file";
import {runtime} from "@/runtime/config";

// Global upload picker + status toast. The right-click context menu
// is intentionally *not* wired here: right-click is the camera-pan
// gesture in the 3D viewer, and intercepting it for an upload prompt
// fought with that on every drag.
//
// Upload entry points instead go through:
//   * StorageBrowser's explicit Upload button (its own hidden input)
//   * any UI that dispatches `UPLOAD_TRIGGER_EVENT` (none today;
//     listener kept so future code can wire in a custom button
//     without re-adding contextmenu plumbing)
//
// This component still provides the global busy/error toast so any
// upload helper can surface progress, regardless of which entry
// point started the upload.
const UploadContextMenu: React.FC = () => {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!runtime.isRestMode()) {
            return;
        }
        const onTrigger = () => {
            // Listener for any UI that wants to ask this picker to
            // open without owning its own hidden <input>.
            fileInputRef.current?.click();
        };
        window.addEventListener(UPLOAD_TRIGGER_EVENT, onTrigger);
        return () => {
            window.removeEventListener(UPLOAD_TRIGGER_EVENT, onTrigger);
        };
    }, []);

    if (!runtime.isRestMode()) {
        return null;
    }

    const onFilePicked = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        e.target.value = "";
        if (!file) return;
        setBusy(true);
        setError(null);
        try {
            await uploadFile(file);
        } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            console.error("upload failed", err);
            setError(msg);
        } finally {
            setBusy(false);
        }
    };

    return (
        <>
            <input
                ref={fileInputRef}
                type="file"
                accept={uploadAcceptAttr()}
                style={{display: "none"}}
                onChange={onFilePicked}
            />
            {(busy || error) && (
                <div
                    className="absolute bottom-4 left-4 z-50 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-xs text-gray-100 max-w-sm"
                >
                    {busy && <span>Uploading…</span>}
                    {error && (
                        <div className="flex items-start gap-2">
                            <span className="text-red-400 break-all">{error}</span>
                            <button
                                className="text-gray-400 hover:text-gray-200"
                                onClick={() => setError(null)}
                            >
                                ×
                            </button>
                        </div>
                    )}
                </div>
            )}
        </>
    );
};

export default UploadContextMenu;
