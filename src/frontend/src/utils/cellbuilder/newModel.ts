// Starting a procedural model: ask for a name, create it in a scope's storage, open it in the
// builder. The storage browser's "+" menu and core's `core:builder-new` command both go through here.

import {alertDialog, errorText, promptDialog} from "@/plugins/dialogs";
import {viewerApi} from "@/services/viewerApi";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

/**
 * Ask for a name, create the model in scope `scopeKey` (default: the active scope) and open it.
 * `beforeOpen` runs once it is created and before it opens (the storage list refreshes, so the
 * panel never shows the model as unsaved). Resolves false when the user cancelled or it failed.
 */
export async function createAndOpenProceduralModel(scopeKey?: string, beforeOpen?: () => Promise<void> | void): Promise<boolean> {
    const name = await promptDialog({
        title: "New procedural model",
        label: "Name for the new procedural model",
        confirmLabel: "Create",
    });
    if (!name || !name.trim()) return false;
    try {
        const key = scopeKey ?? scopeUrlPart(useScopeStore.getState().current);
        const detail = await viewerApi.createProceduralModel(key, name.trim());
        await beforeOpen?.();
        useCellBuilderStore.getState().open(detail.id, detail.name, detail.revision, detail.doc);
        return true;
    } catch (e) {
        void alertDialog({title: "Failed to create procedural model", body: errorText(e), tone: "danger"});
        return false;
    }
}
