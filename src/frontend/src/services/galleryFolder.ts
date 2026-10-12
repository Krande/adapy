// "Open in gallery" on a folder: the gallery's FILES walk, narrowed to the files under it.
//
// The folder may be in another scope than the one being viewed -- an export writes into whatever
// scope the user chose -- so the scope is switched first (the gallery walks the CURRENT scope's
// listing; switching re-reads it). The listing is asked for again either way: an export that just
// finished has written files the listing read before it does not have.

import {useGalleryStore} from "@/state/galleryStore";
import {scopeFromUrlPart, scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";

export function openFolderInGallery(scope: string, folder: string): void {
    const folderKey = folder.replace(/^\/+|\/+$/g, "");
    const scopes = useScopeStore.getState();
    if (scopeUrlPart(scopes.current) !== scope) {
        const option = scopes.available.find((s) => scopeUrlPart(s) === scope) ?? scopeFromUrlPart(scope);
        scopes.setCurrent(option);
    }
    const gallery = useGalleryStore.getState();
    gallery.setFolderFilter({scope, folder: folderKey});
    gallery.setWalk("files");
    gallery.setEnabled(true);
    void request_list_of_files_from_server();
}
