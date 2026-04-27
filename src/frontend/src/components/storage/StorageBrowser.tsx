import React, {useRef, useState} from "react";
import {useServerInfoStore, ServerFileEntry} from "@/state/serverInfoStore";
import {useConversionStore} from "@/state/conversionStore";
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

// Small inline CSS spinner. Uses border tricks rather than an SVG so
// it scales with text size and stays crisp at 16px tall icons.
const Spinner: React.FC<{className?: string}> = ({className = ""}) => (
    <span
        className={`inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin ${className}`}
        aria-hidden="true"
    />
);

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
    const conversionJobs = useConversionStore((s) => s.jobs);
    const [convertingKey, setConvertingKey] = useState<string | null>(null);
    const [uploading, setUploading] = useState(false);
    const [expandedName, setExpandedName] = useState<string | null>(null);
    const [viewingName, setViewingName] = useState<string | null>(null);
    // Owned input — clicking it must happen synchronously inside the
    // button's onClick to preserve the user-activation gesture (iOS Safari
    // refuses the file picker otherwise). The previous implementation
    // dispatched a CustomEvent that UploadContextMenu listened for, which
    // broke the gesture chain on mobile.
    const fileInputRef = useRef<HTMLInputElement>(null);

    const onView = async (entry: ServerFileEntry) => {
        if (viewingName) return; // already busy with another file
        setViewingName(entry.name);
        try {
            await view_file_object_from_server(buildFlatbufferFileObject(entry));
        } catch (err) {
            console.error("view failed", err);
        } finally {
            setViewingName(null);
        }
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
            // Match ObjectInfoBox styling. The viewport-clamped max-width
            // makes the panel self-contain regardless of what the panel-row
            // does — on mobile it stays inside the viewport even if the
            // parent's width hasn't resolved. min-w-0 lets it actually
            // shrink below intrinsic content width so the header buttons
            // don't push past the right edge.
            className="bg-gray-400 bg-opacity-50 rounded p-2 w-full min-w-0 max-w-[calc(100vw-1rem)] md:max-w-md"
        >
            <div className="flex justify-between items-center gap-2 mb-2">
                <h2 className="font-bold truncate">Storage</h2>
                <div className="flex items-center gap-1 shrink-0">
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
                <div className="text-xs italic">
                    No files yet. Use the Upload button (or right-click the viewer) to add one.
                </div>
            ) : (
                <ul className="flex flex-col divide-y divide-gray-500/40 max-h-80 overflow-auto">
                    {files.map((f) => {
                        const targets = viableTargets(f.name);
                        const downloadable = targets.filter((t) => t !== "glb");
                        const stateKey = `${f.name}::`;
                        const busy = convertingKey?.startsWith(stateKey) ?? false;
                        const isViewing = viewingName === f.name;
                        const otherViewing = viewingName !== null && !isViewing;
                        // Progress for the implicit "view" conversion job
                        // (`<name>::glb`) — only shown while we're actively
                        // viewing this file so a stale done/error from a
                        // previous run doesn't render a leftover bar.
                        const viewJob = isViewing ? conversionJobs[`${f.name}::glb`] : undefined;
                        // progress is 0–1 in the store; clamp + percentise.
                        const viewProgressPct = viewJob
                            ? Math.max(0, Math.min(100, Math.round(viewJob.progress * 100)))
                            : 0;
                        return (
                            <li
                                key={f.name}
                                className="flex flex-col px-1 py-1 text-xs"
                            >
                                <div className="flex items-center justify-between gap-2">
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
                                            className="p-1 rounded hover:bg-gray-300/40 disabled:opacity-50 disabled:cursor-not-allowed"
                                            onClick={() => onView(f)}
                                            disabled={isViewing || otherViewing}
                                            title={isViewing ? "Loading…" : otherViewing ? "Another file is loading" : "View"}
                                            aria-busy={isViewing || undefined}
                                        >
                                            {isViewing ? <Spinner/> : <ViewIcon/>}
                                        </button>
                                        <button
                                            className="px-2 py-0.5 rounded hover:bg-gray-300/40 text-[10px] uppercase tracking-wide"
                                            onClick={() => downloadByKey(f.name, f.name)}
                                            title="Download original"
                                        >
                                            DL
                                        </button>
                                        {runtime.convertEnabled() && downloadable.length > 0 && (
                                            <select
                                                disabled={busy}
                                                className="bg-gray-200 hover:bg-gray-300 text-[10px] uppercase rounded px-1 py-0.5 disabled:opacity-60"
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
                                </div>
                                {isViewing && (
                                    <div className="mt-1 h-1 w-full bg-gray-300/50 rounded overflow-hidden">
                                        {viewJob && viewJob.status !== 'queued' ? (
                                            <div
                                                className="h-full bg-blue-600 transition-[width] duration-200"
                                                style={{width: `${viewProgressPct}%`}}
                                            />
                                        ) : (
                                            // Indeterminate slider: a 1/3-width
                                            // bar that pings back and forth so
                                            // the user knows something is
                                            // happening before any % comes back.
                                            <div className="h-full w-1/3 bg-blue-600 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                                        )}
                                    </div>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
};

export default StorageBrowser;
