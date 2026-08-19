import {triggerUploadPicker} from "@/utils/scene/handlers/upload_source_file";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {useLayoutStore} from "./layoutStore";
import {useModeStore} from "./modeStore";

// Data-mode rail actions.
//
// Same discipline as the other three: delegate, never reimplement.

/**
 * Open the file picker.
 *
 * Dispatches the event UploadContextMenu already listens for, rather than owning a
 * second hidden <input>. That indirection exists precisely so several surfaces can ask
 * for the picker without duplicating the upload plumbing — the helper's own comment says
 * "none today", and this is the first caller.
 */
export function openUpload(): void {
    triggerUploadPicker();
}

/** Reveal the Convert panel beside the file list. */
export function openConvert(): void {
    const {mode} = useModeStore.getState();
    useLayoutStore.getState().openPanel(mode, "convert", "right");
}

/** Re-query the scope's file list — the same request the Refresh button makes. */
export function refreshFiles(): void {
    void request_list_of_files_from_server();
}
