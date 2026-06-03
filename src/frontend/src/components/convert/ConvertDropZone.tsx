import React, {useCallback, useRef, useState} from "react";
import {uploadFile, uploadAcceptAttr} from "@/utils/scene/handlers/upload_source_file";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useConvertPageStore} from "@/state/convertPageStore";

// Drop-or-pick zone for the /convert page. Wraps the existing
// `uploadFile` helper but pins `autoConvert: false` — the page wants
// the user to pick the target format explicitly, not be surprised by a
// background `.glb` bake firing the moment a file lands. The upload
// itself (presigned PUT, optional gzip, scope namespacing) is shared
// verbatim with the viewer's auto-upload path.

const ConvertDropZone: React.FC = () => {
    const [dragging, setDragging] = useState(false);
    const [uploading, setUploading] = useState<string | null>(null);
    const [progress, setProgress] = useState(0);
    const [err, setErr] = useState<string | null>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const addRow = useConvertPageStore((s) => s.addRow);
    const current = useScopeStore((s) => s.current);

    const onFile = useCallback(async (file: File) => {
        if (!current) {
            setErr("No scope selected — refresh the page once you're signed in.");
            return;
        }
        setErr(null);
        setUploading(file.name);
        setProgress(0);
        try {
            await uploadFile(file, {
                autoConvert: false,
                scope: scopeUrlPart(current),
                onProgress: (loaded, total) => {
                    setProgress(total > 0 ? loaded / total : 0);
                },
            });
            addRow({
                sourceKey: file.name,
                sizeBytes: file.size,
                addedAt: Date.now(),
                target: null,
            });
        } catch (e) {
            setErr((e as Error).message || "upload failed");
        } finally {
            setUploading(null);
            setProgress(0);
        }
    }, [addRow, current]);

    const onDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragging(false);
        const file = e.dataTransfer.files?.[0];
        if (file) void onFile(file);
    }, [onFile]);

    const onPick = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (file) void onFile(file);
        e.target.value = "";
    }, [onFile]);

    const pct = Math.round(progress * 100);

    return (
        <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => inputRef.current?.click()}
            className={
                "rounded-md border-2 border-dashed cursor-pointer p-8 text-center transition-colors " +
                (dragging
                    ? "border-blue-400 bg-blue-500/10 text-blue-100"
                    : "border-gray-600 bg-gray-800/40 text-gray-300 hover:border-gray-500 hover:bg-gray-800/60")
            }
        >
            <input
                ref={inputRef}
                type="file"
                accept={uploadAcceptAttr()}
                className="hidden"
                onChange={onPick}
            />
            {uploading ? (
                <div className="space-y-2">
                    <div className="text-sm">Uploading <span className="font-mono">{uploading}</span> — {pct}%</div>
                    <div className="h-1 bg-gray-700 rounded-sm overflow-hidden max-w-md mx-auto">
                        <div
                            className="h-full bg-blue-500 transition-all"
                            style={{width: `${Math.max(pct, 4)}%`}}
                        />
                    </div>
                </div>
            ) : (
                <div className="space-y-2">
                    <div className="text-base font-medium">
                        Drop a CAD or FEA file here
                    </div>
                    <div className="text-xs text-gray-400">
                        or click to pick — accepts CAD (.step .ifc .glb …) and FEA
                        (.rmed .sif …) sources advertised by the live workers
                    </div>
                </div>
            )}
            {err && (
                <div className="mt-3 text-xs text-red-400" role="alert">
                    {err}
                </div>
            )}
        </div>
    );
};

export default ConvertDropZone;
