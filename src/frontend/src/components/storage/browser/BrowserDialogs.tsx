import React from "react";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import PositionedMenu, {KebabMenuItem} from "@/components/common/PositionedMenu";
import FolderPickerModal from "@/components/common/FolderPickerModal";
import FieldPickerModal from "../FieldPickerModal";
import GitHistoryPanel from "../GitHistoryPanel";
import type {FolderPicker} from "./useUploads";

export interface CtxMenuState {
    x: number;
    y: number;
    items: KebabMenuItem[];
    header?: React.ReactNode;
}

// The panel's overlays: right-click context menu, FEA field picker, git
// history, and the folder picker every move / upload flow goes through.
const BrowserDialogs: React.FC<{
    ctxMenu: CtxMenuState | null;
    setCtxMenu: (m: CtxMenuState | null) => void;
    pickerName: string | null;
    setPickerName: (n: string | null) => void;
    gitHistoryOpen: boolean;
    setGitHistoryOpen: (open: boolean) => void;
    files: ServerFileEntry[];
    loadedSourceNames: ReadonlySet<string>;
    viewingName: string | null;
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
    picker: FolderPicker | null;
    setPicker: (p: FolderPicker | null) => void;
    existingFolderPaths: string[];
}> = ({
    ctxMenu,
    setCtxMenu,
    pickerName,
    setPickerName,
    gitHistoryOpen,
    setGitHistoryOpen,
    files,
    loadedSourceNames,
    viewingName,
    onToggle,
    picker,
    setPicker,
    existingFolderPaths,
}) => (
    <>
            {ctxMenu && (
                <PositionedMenu
                    items={ctxMenu.items}
                    header={ctxMenu.header}
                    onClose={() => setCtxMenu(null)}
                    anchor={{kind: "point", x: ctxMenu.x, y: ctxMenu.y}}
                />
            )}
            {pickerName && (
                <FieldPickerModal
                    sourceName={pickerName}
                    onClose={() => setPickerName(null)}
                />
            )}
            {/* FeaStreamingPickerModal retired — streaming sessions
                load with defaults via the toggle and refine via
                SimulationControls. */}
            {gitHistoryOpen && (
                <GitHistoryPanel
                    files={files}
                    loadedSourceNames={loadedSourceNames}
                    busyName={viewingName}
                    onToggle={onToggle}
                    onClose={() => setGitHistoryOpen(false)}
                />
            )}
            <FolderPickerModal
                open={picker !== null}
                title={picker?.title ?? ""}
                existingFolders={existingFolderPaths}
                allowRoot={picker?.allowRoot}
                submitLabel={picker?.submitLabel}
                onCancel={() => setPicker(null)}
                onPick={(folder) => {
                    const action = picker?.onPick;
                    setPicker(null);
                    if (action) void action(folder);
                }}
            />
    </>
);

export default BrowserDialogs;
