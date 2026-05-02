import React, {useEffect, useRef, useState} from "react";
import {useServerInfoStore, ServerFileEntry} from "@/state/serverInfoStore";
import {useConversionStore} from "@/state/conversionStore";
import {useModelState} from "@/state/modelState";
import {useScopeStore} from "@/state/scopeStore";
import {runtime} from "@/runtime/config";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {overlay_file_in_scene} from "@/utils/scene/handlers/overlay_file_in_scene";
import {unload_source_from_scene} from "@/utils/scene/handlers/unload_source_from_scene";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {uploadAcceptAttr, uploadFile} from "@/utils/scene/handlers/upload_source_file";
import ReloadIcon from "../icons/ReloadIcon";
import UploadIcon from "../icons/UploadIcon";
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

const StorageBrowser: React.FC = () => {
    const files = useServerInfoStore((s) => s.serverFileObjects);
    const conversionJobs = useConversionStore((s) => s.jobs);
    const loadedSourceNames = useModelState((s) => s.loadedSourceNames);
    const anyLoaded = loadedSourceNames.size > 0;
    const currentScope = useScopeStore((s) => s.current);
    const [uploading, setUploading] = useState(false);
    // Upload progress: name = current file (or null), loaded/total in
    // bytes. Total may stay 0 if the browser can't determine it (rare
    // for File uploads); we treat that as indeterminate.
    const [uploadName, setUploadName] = useState<string | null>(null);
    const [uploadLoaded, setUploadLoaded] = useState(0);
    const [uploadTotal, setUploadTotal] = useState(0);
    const [expandedName, setExpandedName] = useState<string | null>(null);
    const [viewingName, setViewingName] = useState<string | null>(null);
    // Sticky 600ms spin window for the Refresh button so a tap is
    // visually acknowledged even though the underlying list-files
    // request is fire-and-forget over websocket. Without this the
    // icon never changes state on mobile and the tap feels dead.
    const [refreshing, setRefreshing] = useState(false);
    const refreshTimerRef = useRef<number | null>(null);
    const onRefresh = () => {
        if (refreshTimerRef.current !== null) {
            window.clearTimeout(refreshTimerRef.current);
            refreshTimerRef.current = null;
        }
        setRefreshing(true);
        void request_list_of_files_from_server();
        refreshTimerRef.current = window.setTimeout(() => {
            setRefreshing(false);
            refreshTimerRef.current = null;
        }, 600);
    };
    // Cancel a pending spin-window callback if the panel unmounts
    // while we're still in the visible-busy hold.
    useEffect(() => () => {
        if (refreshTimerRef.current !== null) {
            window.clearTimeout(refreshTimerRef.current);
        }
    }, []);
    // Source name of the FEA picker modal, or null if closed. Only one
    // picker open at a time matches the file-list interaction model.
    const [pickerName, setPickerName] = useState<string | null>(null);
    // Owned input — clicking it must happen synchronously inside the
    // button's onClick to preserve the user-activation gesture (iOS Safari
    // refuses the file picker otherwise). The previous implementation
    // dispatched a CustomEvent that UploadContextMenu listened for, which
    // broke the gesture chain on mobile.
    const fileInputRef = useRef<HTMLInputElement>(null);

    // Toggle a file in/out of the scene. All adds go through the
    // overlay path so multiple models can coexist; ``Clear`` in
    // the header drops everything if you want a fresh view. The
    // first checked file behaves identically to a normal load
    // (the loader's else branch computes a translation from its
    // bbox); subsequent files reuse that translation so they
    // overlay correctly.
    const onToggle = async (entry: ServerFileEntry, nextChecked: boolean) => {
        if (viewingName) return; // already busy with another file
        setViewingName(entry.name);
        try {
            if (nextChecked) {
                await overlay_file_in_scene(entry.name);
            } else {
                unload_source_from_scene(entry.name);
            }
        } catch (err) {
            console.error("storage toggle failed", err);
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
                <div className="min-w-0 flex-1">
                    <h2 className="font-bold truncate">Storage</h2>
                    {/* Show the active scope so it's clear which space
                        this list reflects. Files uploaded under one
                        scope are invisible to a list query under another
                        — surfacing the name avoids the "I uploaded but
                        nothing shows" confusion when scope drifts. */}
                    <div className="text-[10px] uppercase tracking-wide text-gray-200/80 truncate"
                         title={currentScope?.kind ? `${currentScope.kind}${currentScope.id ? ":" + currentScope.id : ""}` : "shared"}>
                        scope: {currentScope?.name ?? "Shared"}
                    </div>
                </div>
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
                        type="button"
                        className={
                            "bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white rounded " +
                            "flex items-center justify-center " +
                            // 40px+ tap target on mobile per WCAG; tighter
                            // on desktop where the cursor is precise.
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-none focus:ring-2 focus:ring-blue-400"
                        }
                        onClick={onRefresh}
                        title={refreshing ? "Refreshing — tap again to retry" : "Refresh file list"}
                        aria-label="Refresh list"
                        aria-busy={refreshing}
                    >
                        <span className={refreshing ? "animate-spin" : ""}>
                            <ReloadIcon/>
                        </span>
                    </button>
                    {anyLoaded && (
                        <button
                            className="bg-gray-700 hover:bg-gray-600 text-white p-1 rounded text-xs"
                            onClick={() => clear_loaded_model()}
                            title="Clear all models from the scene (unchecks every file)"
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
                    No files yet. Use the Upload button to add one.
                </div>
            ) : (
                <ul className="flex flex-col divide-y divide-gray-500/40 max-h-80 overflow-auto">
                    {files.map((f) => {
                        const isViewing = viewingName === f.name;
                        const otherViewing = viewingName !== null && !isViewing;
                        const isLoaded = loadedSourceNames.has(f.name);
                        const viewJob = isViewing ? conversionJobs[`${f.name}::glb`] : undefined;
                        const viewProgressPct = viewJob
                            ? Math.max(0, Math.min(100, Math.round(viewJob.progress * 100)))
                            : 0;
                        return (
                            <li
                                key={f.name}
                                className="flex flex-col px-1 py-1 text-xs"
                            >
                                <div className="flex items-center justify-between gap-2">
                                    {/* Single checkbox per row drives "is this file
                                        in the scene?" Checking adds via overlay so
                                        multiple models coexist; unchecking removes
                                        only this file's group. ``Clear`` in the
                                        header unchecks all in one go. h-5/w-5 +
                                        outer touch padding keeps the tap target
                                        ≥40 px on mobile. */}
                                    <label
                                        className={
                                            "flex items-center gap-2 cursor-pointer select-none px-1 py-1 -mx-1 -my-1 " +
                                            (isLoaded ? "text-blue-200 font-medium" : "")
                                        }
                                        title={
                                            isViewing
                                                ? "Loading…"
                                                : otherViewing
                                                    ? "Another file is loading"
                                                    : isLoaded
                                                        ? "Loaded in scene — uncheck to remove just this file"
                                                        : "Add to scene (overlays alongside any other loaded files)"
                                        }
                                    >
                                        <input
                                            type="checkbox"
                                            className="h-5 w-5 shrink-0 cursor-pointer disabled:cursor-not-allowed"
                                            checked={isLoaded}
                                            onChange={(e) => onToggle(f, e.target.checked)}
                                            disabled={isViewing || otherViewing}
                                            aria-busy={isViewing || undefined}
                                        />
                                    </label>
                                    {/* min-w-0 lets `truncate` actually clip inside a
                                        flex item. Tap toggles truncated/wrapped so
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
                                        {isViewing && <Spinner/>}
                                        {/* Field picker for FEA result files. Lets
                                            the user pick a non-default (step, field)
                                            and re-render. Only meaningful in REST mode
                                            with convert enabled. */}
                                        {isFEAResult(f.name) && runtime.isRestMode() && runtime.convertEnabled() && (
                                            <button
                                                className="p-1 rounded text-white hover:bg-gray-300/40 disabled:opacity-50 disabled:cursor-not-allowed"
                                                onClick={() => setPickerName(f.name)}
                                                disabled={otherViewing || isViewing}
                                                title="Pick step / field"
                                                aria-label="Pick step / field"
                                            >
                                                <span className="leading-none text-sm font-mono">⇅</span>
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
