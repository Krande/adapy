import React from "react";
import type {Corpus, FileEntry} from "@/services/viewerApi";
import {buildFileTree} from "@/utils/storage/fileTree";
import {formatBytes} from "@/utils/format";
import FolderPickerModal from "@/components/common/FolderPickerModal";
import {DataTable, DataTableColumn} from "@/components/common/DataTable";
import FileTreeView from "../FileTreeView";
import CopyFromScopeModal from "./CopyFromScopeModal";
import CorpusFilesHeader from "./CorpusFilesHeader";
import UploadFailuresDialog from "./UploadFailuresDialog";
import {useCorpusFiles} from "./useCorpusFiles";

// One corpus's files. Each corpus is its own scope (``corpus:<slug>``) — the
// per-scope /api/scopes/{scope}/files endpoints already exist, so file
// management here is just upload / list / delete against a chosen corpus
// slug.
//
// Tree mode carries the storage panel's organize affordances (via the shared
// FileTreeView mutations): rename / move / delete files and folders,
// drag-and-drop moves, client-side pending folders, and a checkbox /
// shift+arrow multi-select feeding a bulk Move/Delete toolbar.

const FLAT_TH = "px-3 py-1 border-b border-gray-800 font-medium text-gray-300";
const FLAT_TD = "px-3 py-1 border-b border-gray-800";

function flatColumns(onDelete: (key: string) => Promise<void>): DataTableColumn<FileEntry>[] {
    return [
        {
            key: "key",
            header: "Key",
            headerClassName: "text-left " + FLAT_TH,
            cellClassName: "font-mono text-gray-200 " + FLAT_TD + " truncate max-w-md",
            cell: (f) => f.key,
        },
        {
            key: "size",
            header: "Size",
            headerClassName: "text-right " + FLAT_TH,
            cellClassName: "text-right text-gray-400 " + FLAT_TD + " font-mono",
            cell: (f) => formatBytes(f.size),
        },
        {
            key: "actions",
            headerClassName: "px-3 py-1 border-b border-gray-800",
            cellClassName: "text-right " + FLAT_TD,
            cell: (f) => (
                <button
                    type="button"
                    onClick={() => void onDelete(f.key)}
                    className="text-red-400 hover:text-red-300 text-xs"
                >
                    delete
                </button>
            ),
        },
    ];
}

const CorpusFiles: React.FC<{
    corpus: Corpus;
    /** Name/description saved — the parent re-fetches the corpora list
     * so the sidebar and this header pick up the new values. */
    onMetaUpdated: () => void;
}> = ({corpus, onMetaUpdated}) => {
    const st = useCorpusFiles(corpus, onMetaUpdated);
    const {files, viewMode, selected, pendingFolders, newFolderAt, err, busy, note} = st;

    return (
        <div className="flex flex-col h-full">
            <CorpusFilesHeader
                corpus={corpus}
                editingMeta={st.editingMeta}
                metaName={st.metaName}
                metaDesc={st.metaDesc}
                metaBusy={st.metaBusy}
                onMetaName={st.setMetaName}
                onMetaDesc={st.setMetaDesc}
                onOpenMetaEdit={st.openMetaEdit}
                onCancelMetaEdit={() => st.setEditingMeta(false)}
                onSaveMeta={st.saveMeta}
                viewMode={viewMode}
                onViewMode={st.setViewMode}
                uploading={st.uploading}
                progress={st.progress}
                inputRef={st.inputRef}
                onPickUpload={st.onPickUpload}
                onNewFolder={() => st.setNewFolderAt("")}
                onOpenCopy={() => st.setCopyOpen(true)}
            />
            {st.copyOpen && (
                <CopyFromScopeModal
                    dstScope={st.scope}
                    dstSlug={corpus.slug}
                    onClose={() => st.setCopyOpen(false)}
                    onCopied={() => void st.reload()}
                />
            )}
            {busy && (
                <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800 bg-blue-900/20 text-xs text-blue-300">
                    <span
                        className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin shrink-0"
                        aria-hidden="true"
                    />
                    <span className="truncate" role="status">{busy}</span>
                </div>
            )}
            {err && (
                <div className="text-xs text-red-400 px-3 py-2">{err}</div>
            )}
            {note && !err && !busy && (
                <div className="text-xs text-emerald-400 px-3 py-2">{note}</div>
            )}
            {viewMode === "tree" && selected.size > 0 && (
                <div className="mx-3 my-2 px-2 py-1.5 rounded-sm border border-gray-700 bg-gray-800/95 flex items-center gap-2 flex-wrap">
                    <span className="text-xs text-white whitespace-nowrap">
                        {selected.size} selected
                    </span>
                    <button
                        type="button"
                        onClick={st.onMoveSelected}
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-xs px-2 py-1 rounded-sm cursor-pointer"
                    >
                        Move…
                    </button>
                    <button
                        type="button"
                        onClick={() => void st.copyToPersonal(Array.from(selected))}
                        title="Server-side copy into your personal scope (same keys; existing files are skipped)"
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-xs px-2 py-1 rounded-sm cursor-pointer"
                    >
                        Copy to my files
                    </button>
                    <button
                        type="button"
                        onClick={() => void st.deleteKeysWithConfirm(Array.from(selected))}
                        className="bg-red-800 hover:bg-red-700 active:bg-red-900 text-white text-xs px-2 py-1 rounded-sm cursor-pointer"
                    >
                        Delete
                    </button>
                    <button
                        type="button"
                        onClick={st.clearSelection}
                        className="ml-auto bg-gray-600 hover:bg-gray-500 text-white text-xs px-2 py-1 rounded-sm cursor-pointer"
                    >
                        Cancel
                    </button>
                </div>
            )}
            <div className="flex-1 min-h-0 overflow-auto">
                {files.length === 0 && !err && pendingFolders.length === 0 && newFolderAt === null && (
                    <div className="text-xs text-gray-500 italic px-3 py-4">
                        No files yet. Upload representative source files (STEP /
                        IFC / RMED / etc.) to drive regression sweeps from the
                        Runs sub-tab.
                    </div>
                )}
                {files.length > 0 && viewMode === "flat" && (
                    <DataTable
                        wrap={false}
                        columns={flatColumns(st.onDelete)}
                        rows={files}
                        rowKey={(f) => f.key}
                        className="w-full text-xs"
                        stickyHeader
                        theadClassName="bg-gray-900"
                        rowClassName="hover:bg-gray-800/40"
                    />
                )}
                {st.showTree && (
                    <div className="px-1 py-1">
                        <FileTreeView
                            nodes={buildFileTree(files, (f) => f.key, pendingFolders)}
                            getKey={(f) => f.key}
                            namespace="corpus"
                            scope={st.scope}
                            selection={{selected, onSelect: st.setSelection}}
                            mutations={st.mutations}
                            extraFileMenuItems={(key) => [{
                                key: "copy-to-personal",
                                label: "Copy to my files",
                                title: "Server-side copy into your personal scope (same key; skipped if it already exists)",
                                onClick: () => void st.copyToPersonal([key]),
                            }]}
                            newFolderAt={newFolderAt}
                            onNewFolderAtChange={st.setNewFolderAt}
                            renderFileTail={(f) => (
                                <span className="text-gray-400 font-mono">{formatBytes(f.size)}</span>
                            )}
                        />
                    </div>
                )}
            </div>
            <FolderPickerModal
                open={st.picker !== null}
                title={st.picker?.title ?? ""}
                existingFolders={st.existingFolderPaths}
                allowRoot={st.picker?.allowRoot}
                submitLabel={st.picker?.submitLabel}
                onCancel={() => st.setPicker(null)}
                onPick={(folder) => {
                    const action = st.picker?.onPick;
                    st.setPicker(null);
                    if (action) void action(folder);
                }}
            />
            {st.uploadFailures && (
                <UploadFailuresDialog
                    uploadFailures={st.uploadFailures}
                    uploading={!!st.uploading}
                    onClose={() => st.setUploadFailures(null)}
                    onRetry={(list, folder) => {
                        st.setUploadFailures(null);
                        void st.uploadFilesTo(list, folder);
                    }}
                />
            )}
        </div>
    );
};

export default CorpusFiles;
