import React from "react";
import type {Corpus} from "@/services/viewerApi";
import {ViewMode, ViewModeToggle} from "./shared";

// Header of the per-corpus files pane: slug + editable name/description on
// the left (the slug is immutable — storage prefix + scope URLs hang off it,
// so only the display fields are editable), view toggle and upload/copy
// buttons on the right.
const CorpusFilesHeader: React.FC<{
    corpus: Corpus;
    editingMeta: boolean;
    metaName: string;
    metaDesc: string;
    metaBusy: boolean;
    onMetaName: (v: string) => void;
    onMetaDesc: (v: string) => void;
    onOpenMetaEdit: () => void;
    onCancelMetaEdit: () => void;
    onSaveMeta: () => Promise<void>;
    viewMode: ViewMode;
    onViewMode: (m: ViewMode) => void;
    uploading: string | null;
    progress: number;
    inputRef: React.RefObject<HTMLInputElement | null>;
    onPickUpload: (e: React.ChangeEvent<HTMLInputElement>) => void;
    onNewFolder: () => void;
    onOpenCopy: () => void;
}> = ({
    corpus,
    editingMeta,
    metaName,
    metaDesc,
    metaBusy,
    onMetaName,
    onMetaDesc,
    onOpenMetaEdit,
    onCancelMetaEdit,
    onSaveMeta,
    viewMode,
    onViewMode,
    uploading,
    progress,
    inputRef,
    onPickUpload,
    onNewFolder,
    onOpenCopy,
}) => (
    <div className="px-3 py-2 border-b border-gray-800 flex items-center justify-between gap-3">
        {!editingMeta ? (
            <div className="text-xs text-gray-300 min-w-0 flex items-start gap-1.5">
                <div className="min-w-0">
                    <div className="font-mono truncate">
                        {corpus.slug}
                        <span className="text-gray-400 font-sans"> · {corpus.name}</span>
                    </div>
                    {corpus.description && (
                        <div className="text-gray-500 truncate">{corpus.description}</div>
                    )}
                </div>
                <button
                    type="button"
                    onClick={onOpenMetaEdit}
                    title="Edit name / description (slug is immutable)"
                    aria-label="Edit corpus name and description"
                    className="shrink-0 text-gray-500 hover:text-gray-200 leading-none px-1"
                >
                    ✎
                </button>
            </div>
        ) : (
            <form
                className="flex flex-col gap-1 min-w-0 flex-1 max-w-md text-xs"
                onSubmit={(e) => {
                    e.preventDefault();
                    void onSaveMeta();
                }}
            >
                <div className="font-mono text-gray-500">{corpus.slug}</div>
                <input
                    type="text"
                    value={metaName}
                    onChange={(e) => onMetaName(e.target.value)}
                    placeholder="Name"
                    autoFocus
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-gray-100"
                />
                <input
                    type="text"
                    value={metaDesc}
                    onChange={(e) => onMetaDesc(e.target.value)}
                    placeholder="Description (optional)"
                    onKeyDown={(e) => {
                        if (e.key === "Escape") onCancelMetaEdit();
                    }}
                    className="bg-gray-900 border border-gray-600 rounded-sm px-2 py-1 text-gray-100"
                />
                <div className="flex gap-2">
                    <button
                        type="submit"
                        disabled={metaBusy}
                        className="bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white px-2 py-0.5 rounded-sm"
                    >
                        {metaBusy ? "Saving…" : "Save"}
                    </button>
                    <button
                        type="button"
                        onClick={onCancelMetaEdit}
                        disabled={metaBusy}
                        className="text-gray-300 hover:bg-gray-800 px-2 py-0.5 rounded-sm"
                    >
                        Cancel
                    </button>
                </div>
            </form>
        )}
        <div className="flex items-center gap-2 shrink-0">
            <ViewModeToggle mode={viewMode} onChange={onViewMode}/>
            {viewMode === "tree" && (
                <button
                    type="button"
                    onClick={onNewFolder}
                    disabled={!!uploading}
                    className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
                >
                    New folder
                </button>
            )}
            <input
                ref={inputRef}
                type="file"
                multiple
                onChange={onPickUpload}
                className="hidden"
                disabled={!!uploading}
            />
            <button
                type="button"
                onClick={onOpenCopy}
                disabled={!!uploading}
                className="bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
            >
                Copy from scope…
            </button>
            <button
                type="button"
                onClick={() => inputRef.current?.click()}
                disabled={!!uploading}
                className="bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 text-white text-sm px-3 py-1 rounded-sm"
            >
                {uploading
                    ? `Uploading ${Math.round(progress * 100)}%`
                    : "Upload file"}
            </button>
        </div>
    </div>
);

export default CorpusFilesHeader;
