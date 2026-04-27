import React, {useEffect, useRef, useState} from "react";
import {UPLOAD_TRIGGER_EVENT, uploadAcceptAttr, uploadFile} from "@/utils/scene/handlers/upload_source_file";
import {runtime} from "@/runtime/config";

interface MenuPos {
    x: number;
    y: number;
}

const UploadContextMenu: React.FC = () => {
    const [pos, setPos] = useState<MenuPos | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!runtime.isRestMode()) {
            return;
        }
        const onContext = (e: MouseEvent) => {
            // Only intercept right-clicks on the viewer/canvas area, not
            // on form inputs, the file tree, or the menu itself.
            const target = e.target as HTMLElement | null;
            if (target && target.closest("[data-no-upload-menu]")) {
                return;
            }
            e.preventDefault();
            setError(null);
            setPos({x: e.clientX, y: e.clientY});
        };
        const onClickAway = () => setPos(null);
        const onEsc = (e: KeyboardEvent) => {
            if (e.key === "Escape") setPos(null);
        };
        const onTrigger = () => {
            // Mobile/menu-button entry point — open the picker without
            // showing the right-click menu. Same upload flow.
            setPos(null);
            fileInputRef.current?.click();
        };
        window.addEventListener("contextmenu", onContext);
        window.addEventListener("click", onClickAway);
        window.addEventListener("keydown", onEsc);
        window.addEventListener(UPLOAD_TRIGGER_EVENT, onTrigger);
        return () => {
            window.removeEventListener("contextmenu", onContext);
            window.removeEventListener("click", onClickAway);
            window.removeEventListener("keydown", onEsc);
            window.removeEventListener(UPLOAD_TRIGGER_EVENT, onTrigger);
        };
    }, []);

    if (!runtime.isRestMode()) {
        return null;
    }

    const triggerPicker = () => {
        setPos(null);
        fileInputRef.current?.click();
    };

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
                data-no-upload-menu
            />
            {pos && (
                <div
                    data-no-upload-menu
                    className="absolute z-50 bg-gray-900 border border-gray-700 rounded shadow-lg text-gray-100 text-xs min-w-40"
                    style={{left: pos.x, top: pos.y}}
                    onClick={(e) => e.stopPropagation()}
                    onContextMenu={(e) => e.preventDefault()}
                >
                    <button
                        className="w-full text-left px-3 py-2 hover:bg-gray-700"
                        onClick={triggerPicker}
                    >
                        Upload file…
                    </button>
                </div>
            )}
            {(busy || error) && (
                <div
                    data-no-upload-menu
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
