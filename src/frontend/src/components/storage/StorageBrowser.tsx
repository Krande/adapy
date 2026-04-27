import React, {useRef, useState} from "react";
import {useServerInfoStore, ServerFileEntry} from "@/state/serverInfoStore";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {view_file_object_from_server} from "@/utils/scene/handlers/view_file_object_from_server";
import {ensureConverted, TargetFormat} from "@/services/conversion";
import {uploadAcceptAttr, uploadFile} from "@/utils/scene/handlers/upload_source_file";
import {FileObjectT, FileObject} from "@/flatbuffers/base/file-object";
import * as flatbuffers from "flatbuffers";
import ReloadIcon from "../icons/ReloadIcon";
import ViewIcon from "../icons/ViewIcon";
import {runtime} from "@/runtime/config";
import {viewerApi} from "@/services/viewerApi";

const ADA_LOADABLE_EXTS = new Set([
    ".ifc", ".step", ".stp", ".xml", ".inp", ".fem", ".sat", ".acis",
]);
const GLB_ONLY_EXTS = new Set([
    ".glb", ".gltf", ".obj", ".stl", ".ply", ".dae", ".off",
]);

function extOf(name: string): string {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
}

function viableTargets(name: string): TargetFormat[] {
    const ext = extOf(name);
    if (ADA_LOADABLE_EXTS.has(ext)) return ["glb", "ifc", "xml"];
    if (GLB_ONLY_EXTS.has(ext)) return ["glb"];
    return [];
}

function downloadByKey(key: string, suggestedName?: string) {
    const a = document.createElement("a");
    a.href = viewerApi.blobUrl(key);
    if (suggestedName) a.download = suggestedName;
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

function buildFlatbufferFileObject(entry: ServerFileEntry): FileObject {
    const builder = new flatbuffers.Builder(256);
    const t = new FileObjectT(entry.name, entry.fileType, undefined, entry.filepath || entry.name);
    const offset = t.pack(builder);
    builder.finish(offset);
    return FileObject.getRootAsFileObject(builder.dataBuffer());
}

const StorageBrowser: React.FC = () => {
    const files = useServerInfoStore((s) => s.serverFileObjects);
    const [convertingKey, setConvertingKey] = useState<string | null>(null);
    const [uploading, setUploading] = useState(false);
    const [expandedName, setExpandedName] = useState<string | null>(null);
    // Owned input — clicking it must happen synchronously inside the
    // button's onClick to preserve the user-activation gesture (iOS Safari
    // refuses the file picker otherwise). The previous implementation
    // dispatched a CustomEvent that UploadContextMenu listened for, which
    // broke the gesture chain on mobile.
    const fileInputRef = useRef<HTMLInputElement>(null);

    const onView = (entry: ServerFileEntry) => {
        view_file_object_from_server(buildFlatbufferFileObject(entry));
    };

    const onFilePicked = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        e.target.value = "";
        if (!file) return;
        setUploading(true);
        try {
            await uploadFile(file);
        } catch (err) {
            console.error("upload failed", err);
        } finally {
            setUploading(false);
        }
    };

    const onConvertAndDownload = async (sourceName: string, target: TargetFormat) => {
        const stateKey = `${sourceName}::${target}`;
        setConvertingKey(stateKey);
        try {
            const derivedKey = await ensureConverted(sourceName, target);
            // Suggest the source's basename + new extension as the
            // downloaded filename.
            const base = sourceName.replace(/\.[^./]+$/, "");
            downloadByKey(derivedKey, `${base}.${target}`);
        } catch (err) {
            // ensureConverted already updates the store with error;
            // the conversion progress widget surfaces it.
            console.error("convert+download failed", err);
        } finally {
            setConvertingKey(null);
        }
    };

    return (
        <div
            data-no-upload-menu
            className="bg-gray-800/95 text-gray-100 rounded p-2 mt-2 w-full max-w-md border border-gray-700"
        >
            <div className="flex justify-between items-center mb-2">
                <h2 className="font-bold">Storage</h2>
                <div className="flex items-center gap-1">
                    <input
                        ref={fileInputRef}
                        type="file"
                        accept={uploadAcceptAttr()}
                        style={{display: "none"}}
                        onChange={onFilePicked}
                    />
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white px-2 py-1 rounded text-xs disabled:opacity-60"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={uploading}
                        title="Upload file"
                    >
                        {uploading ? "…" : "+ Upload"}
                    </button>
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white p-1 rounded"
                        onClick={() => request_list_of_files_from_server()}
                        title="Refresh list"
                    >
                        <ReloadIcon/>
                    </button>
                </div>
            </div>
            {files.length === 0 ? (
                <div className="text-xs text-gray-400 italic">
                    No files yet. Use the Upload button (or right-click the viewer) to add one.
                </div>
            ) : (
                <ul className="flex flex-col gap-1 max-h-80 overflow-auto">
                    {files.map((f) => {
                        const targets = viableTargets(f.name);
                        const downloadable = targets.filter((t) => t !== "glb");
                        const stateKey = `${f.name}::`;
                        const busy = convertingKey?.startsWith(stateKey) ?? false;
                        return (
                            <li
                                key={f.name}
                                className="flex items-center justify-between bg-gray-900/60 rounded px-2 py-1 text-xs gap-2"
                            >
                                {/* min-w-0 lets `truncate` actually clip inside a
                                    flex item (default min-width is auto = content).
                                    Tap to toggle between truncated and wrapped so
                                    the full filename is reachable on touch. */}
                                <button
                                    type="button"
                                    onClick={() => setExpandedName(expandedName === f.name ? null : f.name)}
                                    className={`flex-1 min-w-0 text-left ${expandedName === f.name ? 'whitespace-normal break-all' : 'truncate'}`}
                                    title={f.name}
                                >
                                    {f.name}
                                </button>
                                <div className="flex items-center gap-1 shrink-0">
                                    <button
                                        className="p-1 rounded hover:bg-gray-700"
                                        onClick={() => onView(f)}
                                        title="View"
                                    >
                                        <ViewIcon/>
                                    </button>
                                    <button
                                        className="px-2 py-0.5 rounded hover:bg-gray-700 text-[10px] uppercase tracking-wide text-gray-300"
                                        onClick={() => downloadByKey(f.name, f.name)}
                                        title="Download original"
                                    >
                                        DL
                                    </button>
                                    {runtime.convertEnabled() && downloadable.length > 0 && (
                                        <select
                                            disabled={busy}
                                            className="bg-gray-700 hover:bg-gray-600 text-[10px] uppercase rounded px-1 py-0.5 text-gray-300 disabled:opacity-60"
                                            value=""
                                            onChange={(e) => {
                                                const target = e.target.value as TargetFormat | "";
                                                e.target.value = "";
                                                if (target) onConvertAndDownload(f.name, target);
                                            }}
                                            title="Convert and download"
                                        >
                                            <option value="">{busy ? "…" : "as ▾"}</option>
                                            {downloadable.map((t) => (
                                                <option key={t} value={t}>{t.toUpperCase()}</option>
                                            ))}
                                        </select>
                                    )}
                                </div>
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
};

export default StorageBrowser;
