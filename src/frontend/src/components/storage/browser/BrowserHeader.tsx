import React from "react";
import type {ProceduralModelSummary, ProceduralTemplate} from "@/services/viewerApi";
import type {ScopeOption} from "@/state/scopeStore";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import PositionedMenu, {KebabMenuItem} from "@/components/common/PositionedMenu";
import ReloadIcon from "../../icons/ReloadIcon";
import PlusIcon from "../../icons/PlusIcon";
import ExpandIcon from "../../icons/ExpandIcon";
import {uploadAcceptAttr} from "@/utils/scene/handlers/upload_source_file";
import {Spinner} from "./helpers";
import type {useProceduralModels} from "./useProceduralModels";

type Procedural = ReturnType<typeof useProceduralModels>;

// Title row of the storage panel: scope line, "+" menu (upload / new folder /
// new procedural model / import / templates), refresh, clear scene, maximize.
export interface BrowserHeaderProps {
    currentScope: ScopeOption | null;
    hiddenFiles: ServerFileEntry[];
    fileInputRef: React.RefObject<HTMLInputElement | null>;
    importXlsxInputRef: React.RefObject<HTMLInputElement | null>;
    onFilePicked: (e: React.ChangeEvent<HTMLInputElement>) => void;
    uploading: boolean;
    plusOpen: boolean;
    setPlusOpen: React.Dispatch<React.SetStateAction<boolean>>;
    plusBtnRef: React.RefObject<HTMLButtonElement | null>;
    templatesOpen: boolean;
    setTemplatesOpen: (open: boolean) => void;
    templatesBtnRef: React.RefObject<HTMLButtonElement | null>;
    allTemplates: ProceduralTemplate[];
    importPrompt: Procedural["importPrompt"];
    importEngines: Procedural["importEngines"];
    activeProcedural: string | null;
    createProceduralModel: () => Promise<void>;
    createProceduralModelFromTemplate: (tpl: ProceduralTemplate) => Promise<void>;
    setNewFolderAt: (at: string | null) => void;
    onRefresh: () => void;
    refreshing: boolean;
    anyLoaded: boolean;
    bulkBusy: string | null;
    onHideAll: () => Promise<void>;
    maximized: boolean;
    setMaximized: React.Dispatch<React.SetStateAction<boolean>>;
}

const BrowserHeader: React.FC<BrowserHeaderProps> = ({
    currentScope,
    hiddenFiles,
    fileInputRef,
    importXlsxInputRef,
    onFilePicked,
    uploading,
    plusOpen,
    setPlusOpen,
    plusBtnRef,
    templatesOpen,
    setTemplatesOpen,
    templatesBtnRef,
    allTemplates,
    importPrompt,
    importEngines,
    activeProcedural,
    createProceduralModel,
    createProceduralModelFromTemplate,
    setNewFolderAt,
    onRefresh,
    refreshing,
    anyLoaded,
    bulkBusy,
    onHideAll,
    maximized,
    setMaximized,
}) => (
            <div className="flex justify-between items-center gap-2 mb-2">
                <div className="min-w-0 flex-1">
                    <h2 className="font-bold truncate">Storage</h2>
                    {/* Show the active scope so it's clear which space
                        this list reflects. Files uploaded under one
                        scope are invisible to a list query under another
                        — surfacing the name avoids the "I uploaded but
                        nothing shows" confusion when scope drifts. */}
                    <div className="text-[10px] uppercase tracking-wide text-gray-400 truncate"
                         title={currentScope?.kind ? `${currentScope.kind}${currentScope.id ? ":" + currentScope.id : ""}` : "shared"}>
                        scope: {currentScope?.name ?? "Shared"}
                    </div>
                    {/* Say that something is being withheld. A browser that hides
                        files without admitting it is indistinguishable from one
                        that lost them — and a publishing scope can hide thousands. */}
                    {hiddenFiles.length > 0 && (
                        <div
                            className="text-[10px] text-gray-500 truncate"
                            title={
                                `${hiddenFiles.length} published-asset blob(s) under assets/ are not listed here. ` +
                                "They are machine-published datasets, not uploads. The admin Storage tab can show them."
                            }
                        >
                            {hiddenFiles.length.toLocaleString()} published asset
                            {hiddenFiles.length === 1 ? "" : "s"} hidden
                        </div>
                    )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                    <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept={uploadAcceptAttr()}
                        style={{display: "none"}}
                        onChange={onFilePicked}
                    />
                    <input
                        ref={importXlsxInputRef}
                        type="file"
                        accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        style={{display: "none"}}
                        onChange={(e) => {
                            const file = e.target.files?.[0];
                            // Reset so re-picking the same file fires onChange again.
                            e.target.value = "";
                            if (file) void useCellBuilderStore.getState().beginImportFromExcel(file);
                        }}
                    />
                    <button
                        ref={plusBtnRef}
                        type="button"
                        className={
                            "bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white rounded-sm cursor-pointer " +
                            "flex items-center justify-center disabled:opacity-60 " +
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-hidden focus:ring-2 focus:ring-blue-400"
                        }
                        onClick={() => setPlusOpen((v) => !v)}
                        disabled={uploading}
                        title="Add — upload files or create a folder"
                        aria-label="Add"
                        aria-haspopup="menu"
                        aria-expanded={plusOpen}
                    >
                        {/* Fixed 24px icon slot — keeps this button the
                            same size as Refresh/Maximize whether it
                            shows the plus or the busy spinner. */}
                        <span className="inline-flex h-6 w-6 items-center justify-center">
                            {uploading ? <Spinner/> : <PlusIcon width="24px" height="24px"/>}
                        </span>
                    </button>
                    {plusOpen && (
                        <PositionedMenu
                            items={[
                                {
                                    key: "upload",
                                    label: "Upload files…",
                                    onClick: () => fileInputRef.current?.click(),
                                },
                                {
                                    key: "new-folder",
                                    label: "New folder…",
                                    onClick: () => setNewFolderAt(""),
                                },
                                {
                                    key: "new-procedural",
                                    label: "New procedural model…",
                                    onClick: () => void createProceduralModel(),
                                },
                                {
                                    key: "import-xlsx",
                                    label: "Import from Excel…",
                                    // Imports create a new procedural model; the
                                    // owning engine is detected from the file's
                                    // _ADA_META, else the user is prompted.
                                    onClick: () => {
                                        setPlusOpen(false);
                                        importXlsxInputRef.current?.click();
                                    },
                                },
                                {
                                    key: "new-from-template",
                                    label: "New model from template ▸",
                                    // Swap the + menu for the template list,
                                    // anchored off the same + button.
                                    onClick: () => {
                                        setPlusOpen(false);
                                        setTemplatesOpen(true);
                                    },
                                },
                            ]}
                            onClose={() => setPlusOpen(false)}
                            ignoreOutsideRef={plusBtnRef}
                            anchor={{
                                kind: "rect",
                                getRect: () => plusBtnRef.current?.getBoundingClientRect(),
                            }}
                        />
                    )}
                    {templatesOpen && (
                        <PositionedMenu
                            header={
                                <span className="text-[11px] uppercase tracking-wide opacity-60">
                                    Start from template
                                </span>
                            }
                            items={allTemplates.map(
                                (tpl): KebabMenuItem => ({
                                    key: tpl.id,
                                    // Engine in parentheses, per request — e.g.
                                    // "Topside + jacket (adapy-default)".
                                    label: `${tpl.name} (${tpl.engine})`,
                                    onClick: () => void createProceduralModelFromTemplate(tpl),
                                }),
                            )}
                            onClose={() => setTemplatesOpen(false)}
                            ignoreOutsideRef={templatesBtnRef}
                            anchor={{
                                kind: "rect",
                                getRect: () => plusBtnRef.current?.getBoundingClientRect(),
                            }}
                        />
                    )}
                    {importPrompt && (
                        <PositionedMenu
                            header={
                                <span className="text-[11px] uppercase tracking-wide opacity-60">
                                    Import “{importPrompt.name}” as…
                                </span>
                            }
                            items={[
                                ...importEngines.map(
                                    (eng): KebabMenuItem => ({
                                        key: eng.slug,
                                        label: eng.name,
                                        onClick: () =>
                                            void useCellBuilderStore
                                                .getState()
                                                // Pass the prompt captured here at
                                                // render time: the menu dismisses
                                                // (cancelImport) before this fires,
                                                // clearing importPrompt in the store.
                                                .confirmImportEngine(eng.slug, importPrompt),
                                    }),
                                ),
                                {
                                    key: "__cancel",
                                    label: "Cancel",
                                    onClick: () => useCellBuilderStore.getState().cancelImport(),
                                },
                            ]}
                            onClose={() => useCellBuilderStore.getState().cancelImport()}
                            ignoreOutsideRef={plusBtnRef}
                            anchor={{
                                kind: "rect",
                                getRect: () => plusBtnRef.current?.getBoundingClientRect(),
                            }}
                        />
                    )}
                    <button
                        type="button"
                        className={
                            "bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white rounded-sm cursor-pointer " +
                            "flex items-center justify-center " +
                            // 40px+ tap target on mobile per WCAG; tighter
                            // on desktop where the cursor is precise.
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-hidden focus:ring-2 focus:ring-blue-400"
                        }
                        onClick={onRefresh}
                        title={refreshing ? "Refreshing — tap again to retry" : "Refresh file list"}
                        aria-label="Refresh list"
                        aria-busy={refreshing}
                    >
                        <span className={"inline-flex h-6 w-6 items-center justify-center " + (refreshing ? "animate-spin" : "")}>
                            <ReloadIcon/>
                        </span>
                    </button>
                    {/* Clear: unload every loaded source. This is a
                        teardown action (drops the meshes from the
                        scene), distinct from per-element visibility
                        which lives in the Selected Object Info
                        panel. There's no symmetric "Load all" — the
                        user picks the files they want via per-row
                        checkboxes; loading every file at once would
                        rarely be the right thing. */}
                    {(anyLoaded || activeProcedural) && (
                        <button
                            type="button"
                            className={
                                "bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-60 cursor-pointer " +
                                "text-white rounded-sm text-xs whitespace-nowrap " +
                                "px-2 sm:px-2 py-1 min-h-[40px] sm:min-h-0"
                            }
                            onClick={() => void onHideAll()}
                            disabled={bulkBusy !== null}
                            title="Unload every model in the scene, and close any open procedural model"
                            aria-label="Clear scene"
                            aria-busy={bulkBusy === "clear"}
                        >
                            {bulkBusy === "clear" ? "Clearing…" : "Clear"}
                        </button>
                    )}
                    <button
                        type="button"
                        className={
                            "bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white rounded-sm cursor-pointer " +
                            "flex items-center justify-center " +
                            "p-2 sm:p-1 min-h-[40px] min-w-[40px] sm:min-h-0 sm:min-w-0 " +
                            "focus:outline-hidden focus:ring-2 focus:ring-blue-400"
                        }
                        onClick={() => setMaximized((v) => !v)}
                        title={maximized ? "Restore compact panel" : "Maximize"}
                        aria-label={maximized ? "Restore compact panel" : "Maximize"}
                    >
                        <span className="inline-flex h-6 w-6 items-center justify-center">
                            <ExpandIcon expanded={maximized} width="24px" height="24px"/>
                        </span>
                    </button>
                </div>
            </div>
);

export default BrowserHeader;
