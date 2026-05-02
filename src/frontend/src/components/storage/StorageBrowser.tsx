import React, {useRef, useState} from "react";
import {useServerInfoStore, ServerFileEntry} from "@/state/serverInfoStore";
import {useConversionStore} from "@/state/conversionStore";
import {useModelState} from "@/state/modelState";
import {runtime} from "@/runtime/config";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {view_file_object_from_server} from "@/utils/scene/handlers/view_file_object_from_server";
import {overlay_file_in_scene} from "@/utils/scene/handlers/overlay_file_in_scene";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {uploadAcceptAttr, uploadFile} from "@/utils/scene/handlers/upload_source_file";
import {FileObjectT, FileObject} from "@/flatbuffers/base/file-object";
import * as flatbuffers from "flatbuffers";
import ReloadIcon from "../icons/ReloadIcon";
import UploadIcon from "../icons/UploadIcon";
import ViewIcon from "../icons/ViewIcon";
import FieldPickerModal from "./FieldPickerModal";

// Files that carry per-(step, field) result data and benefit from the
// picker UI. SIF is the only one in REST mode today; new formats land
// here when their converter learns to honor (step, field).
function isFEAResult(name: string): boolean {
    return name.toLowerCase().endsWith(".sif");
}

// Small inline CSS spinner. Uses border tricks rather than an SVG so
// it scales with text size and stays crisp at 16px tall icons.
const Spinner: React.FC<{className?: string}> = ({className = ""}) => (
    <span
        className={`inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full animate-spin ${className}`}
        aria-hidden="true"
    />
);

function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
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
    const loadedSourceName = useModelState((s) => s.loadedSourceName);
    const [uploading, setUploading] = useState(false);
    // Upload progress: name = current file (or null), loaded/total in
    // bytes. Total may stay 0 if the browser can't determine it (rare
    // for File uploads); we treat that as indeterminate.
    const [uploadName, setUploadName] = useState<string | null>(null);
    const [uploadLoaded, setUploadLoaded] = useState(0);
    const [uploadTotal, setUploadTotal] = useState(0);
    const [expandedName, setExpandedName] = useState<string | null>(null);
    const [viewingName, setViewingName] = useState<string | null>(null);
    // Source name of the FEA picker modal, or null if closed. Only one
    // picker open at a time matches the file-list interaction model.
    const [pickerName, setPickerName] = useState<string | null>(null);
    // Owned input — clicking it must happen synchronously inside the
    // button's onClick to preserve the user-activation gesture (iOS Safari
    // refuses the file picker otherwise). The previous implementation
    // dispatched a CustomEvent that UploadContextMenu listened for, which
    // broke the gesture chain on mobile.
    const fileInputRef = useRef<HTMLInputElement>(null);

    const onView = async (entry: ServerFileEntry, additive = false) => {
        if (viewingName) return; // already busy with another file
        setViewingName(entry.name);
        try {
            if (additive) {
                // Overlay path: skip the VIEW_FILE_OBJECT roundtrip
                // (which the server hard-codes to REPLACE) and feed
                // the GLB straight into the scene loader.
                await overlay_file_in_scene(entry.name);
            } else {
                await view_file_object_from_server(buildFlatbufferFileObject(entry));
            }
        } catch (err) {
            console.error(additive ? "overlay failed" : "view failed", err);
        } finally {
            setViewingName(null);
        }
    };

    const onFilePicked = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        e.target.value = "";
        if (!file) return;
        setUploading(true);
        setUploadName(file.name);
        setUploadLoaded(0);
        setUploadTotal(file.size);
        try {
            await uploadFile(file, {
                onProgress: (loaded, total) => {
                    setUploadLoaded(loaded);
                    if (total) setUploadTotal(total);
                },
            });
        } catch (err) {
            console.error("upload failed", err);
        } finally {
            setUploading(false);
            setUploadName(null);
            setUploadLoaded(0);
            setUploadTotal(0);
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
                        className="bg-blue-700 hover:bg-blue-600 text-white p-1 rounded disabled:opacity-60 flex items-center justify-center"
                        onClick={() => fileInputRef.current?.click()}
                        disabled={uploading}
                        title="Upload a file to this scope"
                        aria-label="Upload file"
                    >
                        {uploading ? <Spinner/> : <UploadIcon/>}
                    </button>
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white p-1 rounded flex items-center justify-center"
                        onClick={() => request_list_of_files_from_server()}
                        title="Refresh file list"
                        aria-label="Refresh list"
                    >
                        <ReloadIcon/>
                    </button>
                    {loadedSourceName && (
                        <button
                            className="bg-gray-700 hover:bg-gray-600 text-white p-1 rounded text-xs"
                            onClick={() => clear_loaded_model()}
                            title="Clear all models from the scene"
                            aria-label="Clear scene"
                        >
                            Clear
                        </button>
                    )}
                </div>
            </div>
            {uploadName && (
                <div className="mb-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                        <span className="truncate flex-1 min-w-0" title={uploadName}>
                            Uploading {uploadName}
                        </span>
                        <span className="shrink-0 tabular-nums">
                            {uploadTotal > 0
                                ? `${formatBytes(uploadLoaded)} / ${formatBytes(uploadTotal)}`
                                : formatBytes(uploadLoaded)}
                        </span>
                    </div>
                    <div className="mt-1 h-1 w-full bg-gray-300/50 rounded overflow-hidden">
                        {uploadTotal > 0 ? (
                            <div
                                className="h-full bg-blue-600 transition-[width] duration-200"
                                style={{
                                    width: `${Math.max(
                                        0,
                                        Math.min(100, Math.round((uploadLoaded / uploadTotal) * 100)),
                                    )}%`,
                                }}
                            />
                        ) : (
                            <div className="h-full w-1/3 bg-blue-600 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                        )}
                    </div>
                </div>
            )}
            {files.length === 0 ? (
                <div className="text-xs italic">
                    No files yet. Use the Upload button (or right-click the viewer) to add one.
                </div>
            ) : (
                <ul className="flex flex-col divide-y divide-gray-500/40 max-h-80 overflow-auto">
                    {files.map((f) => {
                        const isViewing = viewingName === f.name;
                        const otherViewing = viewingName !== null && !isViewing;
                        const isLoaded = loadedSourceName === f.name;
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
                                        className={`flex-1 min-w-0 text-left ${expandedName === f.name ? 'whitespace-normal break-all' : 'truncate'} ${isLoaded ? 'text-blue-200 font-medium' : ''}`}
                                        title={f.name}
                                    >
                                        {f.name}
                                    </button>
                                    <div className="flex items-center gap-1 shrink-0">
                                        {/* Eye icon. White when not loaded, blue when
                                            this file is the one rendered in the scene
                                            so the row reads as "active" at a glance. */}
                                        <button
                                            className={
                                                "p-1 rounded hover:bg-gray-300/40 disabled:opacity-50 disabled:cursor-not-allowed " +
                                                (isLoaded ? "text-blue-300" : "text-white")
                                            }
                                            // Shift+click overlays instead of replacing — desktop
                                            // power-user shortcut. Mobile users use the explicit
                                            // "+" button next to this one.
                                            onClick={(e) => onView(f, e.shiftKey)}
                                            disabled={isViewing || otherViewing}
                                            title={
                                                isViewing
                                                    ? "Loading…"
                                                    : otherViewing
                                                        ? "Another file is loading"
                                                        : isLoaded
                                                            ? "Currently loaded — click to reload (Shift+click to overlay)"
                                                            : "View (Shift+click to overlay)"
                                            }
                                            aria-pressed={isLoaded}
                                            aria-busy={isViewing || undefined}
                                        >
                                            {isViewing ? <Spinner/> : <ViewIcon/>}
                                        </button>
                                        {/* Touch-friendly overlay button. Adds the file
                                            to the current scene without replacing what's
                                            loaded. Useful for visual diff debugging
                                            (overlay reference + subject GLBs to spot
                                            missing/displaced plates). */}
                                        <button
                                            className="p-1 rounded text-white hover:bg-gray-300/40 disabled:opacity-50 disabled:cursor-not-allowed"
                                            onClick={() => onView(f, true)}
                                            disabled={isViewing || otherViewing}
                                            title="Add to scene (overlay, doesn't replace)"
                                            aria-label="Add to scene"
                                        >
                                            <span className="leading-none text-base font-bold">+</span>
                                        </button>
                                        {/* Field picker for FEA result files. Lets the
                                            user pick a non-default (step, field) and
                                            re-render. Only meaningful in REST mode with
                                            convert enabled. */}
                                        {isFEAResult(f.name) && runtime.isRestMode() && runtime.convertEnabled() && (
                                            <button
                                                className="p-1 rounded text-white hover:bg-gray-300/40 disabled:opacity-50 disabled:cursor-not-allowed"
                                                onClick={() => setPickerName(f.name)}
                                                disabled={otherViewing || isViewing}
                                                title="Pick step / field"
                                                aria-label="Pick step / field"
                                            >
                                                {/* Sliders glyph — discoverable as "tunable" */}
                                                <span className="leading-none text-sm font-mono">⇅</span>
                                            </button>
                                        )}
                                        {isLoaded && (
                                            <button
                                                className="p-1 rounded text-white hover:bg-gray-300/40"
                                                onClick={() => clear_loaded_model()}
                                                title="Remove from scene"
                                                aria-label="Remove from scene"
                                            >
                                                <span className="leading-none text-base">×</span>
                                            </button>
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
            {pickerName && (
                <FieldPickerModal
                    sourceName={pickerName}
                    onClose={() => setPickerName(null)}
                />
            )}
        </div>
    );
};

export default StorageBrowser;
