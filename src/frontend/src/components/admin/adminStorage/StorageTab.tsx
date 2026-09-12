import React, {useMemo} from "react";
import {collectFolderPaths} from "@/utils/storage/fileTree";
import FolderPickerModal from "@/components/common/FolderPickerModal";
import {DataTable, DataTableColumn} from "@/components/common/DataTable";
import AdminTabShell from "../AdminTabShell";
import OverridesPanel from "./OverridesPanel";
import StorageToolbar from "./StorageToolbar";
import SourceRow from "./SourceRow";
import SourceCard from "./SourceCard";
import {FolderCardRow, FolderTableRow} from "./FolderRows";
import {STORAGE_COLUMNS} from "./useResizableColumns";
import {StorageEntry, useAdminStorage} from "./useAdminStorage";

// Admin-only enriched storage view. Shows source format, size, upload
// time, and the derived blobs already cached for each source. Houses
// the DL/Convert/Delete actions that used to live in the regular
// StorageBrowser — those are admin-power-user features, not
// everyday-user features.

const entryKey = (entry: StorageEntry) =>
    entry.kind === "folder" ? `folder:${entry.folder.path}` : entry.file.key;

const StorageTab: React.FC = () => {
    const st = useAdminStorage();
    const {
        files, expandedFolders, expandedKey, setExpandedKey, selectedKeys, busyKey, colWidths, startResize,
    } = st;

    const folderBusy = (path: string) =>
        busyKey === `__folder_rename__:${path}` || busyKey === `__folder_moveInto__:${path}`;

    // Desktop table. Columns are drag-resizable (grips on the header borders)
    // with widths persisted to localStorage; the table width is the sum of
    // the column widths, so the surrounding overflow-auto gives a horizontal
    // scrollbar whenever the total exceeds the panel rather than squishing
    // columns into unreadable mush. ``table-fixed`` makes the <col> widths
    // authoritative for every body cell.
    const columns = useMemo<DataTableColumn<StorageEntry>[]>(
        () => STORAGE_COLUMNS.map((c, i) => ({
            key: c.key,
            col: {style: {width: colWidths[i]}},
            headerClassName: "relative overflow-hidden px-3 py-2 font-medium text-gray-300 whitespace-nowrap",
            header: (
                <>
                    {c.label}
                    {i < STORAGE_COLUMNS.length - 1 && (
                        <span
                            onMouseDown={startResize(i)}
                            className="absolute top-0 right-0 z-10 h-full w-1.5 cursor-col-resize hover:bg-blue-500/60"
                            title="Drag to resize column"
                        />
                    )}
                </>
            ),
            // Rows are rendered whole by `renderRow`; the cell renderer is unused.
            cell: () => null,
        })),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [colWidths],
    );

    const rowHandlers = {
        busyKey,
        onConvert: st.onConvert,
        onDownload: st.onDownload,
        onDelete: st.onDelete,
        onDeleteDerived: st.onDeleteDerived,
        onDeleteAllDerived: st.onDeleteAllDerived,
        onMoveToFolder: st.onMoveSingleToFolder,
    };

    return (
        <AdminTabShell
            title={
                <StorageToolbar
                    scopeName={st.currentScope?.name ?? "Shared"}
                    fileCount={files.length}
                    overrideOpen={st.overrideOpen}
                    activeOverrides={st.activeOverrides}
                    onToggleOverrides={() => st.setOverrideOpen((v) => !v)}
                    selectedCount={selectedKeys.size}
                    busyKey={busyKey}
                    onMoveSelected={() => void st.onMoveSelectedToFolder()}
                    onClearSelection={st.clearSelection}
                    totalDerived={st.totalDerivedAcrossScope}
                    onClearAllDerived={() => void st.onClearAllDerived()}
                    compressionBusy={st.compressionBusy}
                    compressionMsg={st.compressionMsg}
                    onCompress={() => void st.onCompressUncompressed()}
                    loading={st.loading}
                    onRefresh={() => void st.reload()}
                    showHidden={st.showHidden}
                    onShowHidden={st.setShowHidden}
                    hiddenCount={st.hiddenFiles.length}
                />
            }
            subheader={st.overrideOpen && (
                <OverridesPanel
                    overrides={st.overrides}
                    onChange={(key, v) => st.setOverrides((o) => ({...o, [key]: v}))}
                />
            )}
            error={st.error}
            loadingLabel={null}
        >
            <DataTable
                wrap={false}
                columns={columns}
                rows={st.visibleEntries}
                rowKey={entryKey}
                className="hidden sm:table text-sm table-fixed"
                style={{width: st.tableWidth}}
                stickyHeader
                theadClassName="bg-gray-800 text-left"
                renderRow={(entry) => entry.kind === "folder" ? (
                    <FolderTableRow
                        folder={entry.folder}
                        depth={entry.depth}
                        fileCount={entry.fileCount}
                        expanded={expandedFolders.has(entry.folder.path)}
                        onToggle={() => st.toggleFolder(entry.folder.path)}
                        onRename={() => st.onFolderRenameOrMove(entry.folder.path, "rename")}
                        onMoveInto={() => st.onFolderRenameOrMove(entry.folder.path, "moveInto")}
                        busyMoving={folderBusy(entry.folder.path)}
                    />
                ) : (
                    <SourceRow
                        file={entry.file}
                        depth={entry.depth}
                        scope={st.scope}
                        expanded={expandedKey === entry.file.key}
                        onToggleExpand={() => setExpandedKey(expandedKey === entry.file.key ? null : entry.file.key)}
                        selected={selectedKeys.has(entry.file.key)}
                        onToggleSelected={() => st.toggleKeySelection(entry.file.key)}
                        {...rowHandlers}
                    />
                )}
            />
            {/* Mobile cards */}
            <ul className="sm:hidden divide-y divide-gray-800">
                {st.visibleEntries.map((entry) => {
                    if (entry.kind === "folder") {
                        return (
                            <FolderCardRow
                                key={`folder:${entry.folder.path}`}
                                folder={entry.folder}
                                depth={entry.depth}
                                fileCount={entry.fileCount}
                                expanded={expandedFolders.has(entry.folder.path)}
                                onToggle={() => st.toggleFolder(entry.folder.path)}
                                onRename={() => st.onFolderRenameOrMove(entry.folder.path, "rename")}
                                onMoveInto={() => st.onFolderRenameOrMove(entry.folder.path, "moveInto")}
                                busyMoving={folderBusy(entry.folder.path)}
                            />
                        );
                    }
                    const f = entry.file;
                    return (
                        <SourceCard
                            key={f.key}
                            file={f}
                            depth={entry.depth}
                            expanded={expandedKey === f.key}
                            selected={selectedKeys.has(f.key)}
                            onToggleSelected={() => st.toggleKeySelection(f.key)}
                            onToggleExpand={() => setExpandedKey(expandedKey === f.key ? null : f.key)}
                            {...rowHandlers}
                        />
                    );
                })}
            </ul>
            {!st.loading && files.length === 0 && (
                <div className="px-4 py-8 text-center text-gray-500 text-sm">
                    No files in this scope.
                </div>
            )}
            <FolderPickerModal
                open={st.picker !== null}
                title={st.picker?.title ?? ""}
                existingFolders={collectFolderPaths(files, (f) => f.key)}
                initialNew={st.picker?.initialNew}
                onCancel={() => st.setPicker(null)}
                onPick={(folder) => {
                    const action = st.picker?.onPick;
                    st.setPicker(null);
                    if (action) void action(folder);
                }}
            />
        </AdminTabShell>
    );
};

export default StorageTab;
