import {useScopeStore, type ScopeOption} from "@/state/scopeStore";
import {useServerInfoStore} from "@/state/serverInfoStore";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";

/**
 * Switch the active scope, tearing down everything that belonged to the outgoing one.
 *
 * Extracted from RestSection's ScopeSelector so the classic Options drawer and the
 * shell's title bar drive the SAME teardown. The teardown is the part that matters and
 * is easy to get wrong by reimplementing: without it the user sees the previous
 * project's files still listed and a stale 3D scene from a project they are no longer
 * in — which looks like a data-leak bug rather than a missing refresh.
 *
 * The refresh is deliberately not awaited. The response lands through the ordinary
 * LIST_FILE_OBJECTS handler, and the panel shows its "no files yet" state for the few
 * hundred milliseconds in between.
 */
export function applyScopeChange(picked: ScopeOption): void {
    useServerInfoStore.getState().setServerFileObjects([]);
    useServerInfoStore.getState().setServerFiles([]);
    void clear_loaded_model();
    useScopeStore.getState().setCurrent(picked);
    void request_list_of_files_from_server();
}
