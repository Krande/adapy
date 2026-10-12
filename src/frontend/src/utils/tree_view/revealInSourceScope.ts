// A source node shown in the Sources tree, in whichever scope it lives.
//
// The tree shows the CURRENT scope, and a file an export wrote usually sits in another one than the
// scope that was searched -- the export goes wherever the user chose. So the scope is switched
// first when the node's differs (`decideScope`, `@/assets/sourceJump`), the way opening an export
// folder in the gallery switches it (`services/galleryFolder.ts`): the scope's file listing is asked
// for again, and the models already in the scene stay -- the user is looking at the exported one.
// Waiting for the switch to land is `revealSearchHit`'s own wait: it reads the scope from the store
// AFTER the switch and waits until the Sources tab has the collections of exactly that scope.
//
// A scope the user cannot open is not switched to: the reason is answered as a `not-found` outcome,
// the shape every caller already shows inline.

import {decideScope} from "@/assets/sourceJump";
import type {SearchHit} from "@/services/api/attributeSearch";
import {getSingletonViewerStores} from "@/state/AdaViewerContext";
import {scopeUrlPart} from "@/state/scopeStore";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";

import {revealSearchHit, type RevealOutcome} from "./revealSearchHit";

export async function revealInSourceScope(
    hit: SearchHit,
    sourceScope: string | null | undefined,
    opts: {load?: boolean} = {},
): Promise<RevealOutcome> {
    const scopes = getSingletonViewerStores().useScopeStore.getState();
    const decision = decideScope(sourceScope, scopeUrlPart(scopes.current), scopes.available, scopeUrlPart);
    if (decision.kind === "refuse") return {status: "not-found", message: decision.reason};
    if (decision.kind === "switch") {
        scopes.setCurrent(decision.to);
        void request_list_of_files_from_server();
    }
    return revealSearchHit(hit, opts);
}
