import {useModelState} from "@/state/modelState";
import {modelKeyMapRef} from "@/state/refs";
import {useScopeStore, scopeUrlPart, type ScopeOption} from "@/state/scopeStore";
import {confirm} from "@/ui/confirm";
import {applyScopeChange} from "./applyScopeChange";
import {scopeChangeConfirmRequest, scopeChangeLoss} from "./scopeChangeRules";

// Switching scope tears the loaded model out of the scene — see applyScopeChange, which
// has to, because the model belongs to the scope you are leaving.
//
// That teardown was silent. In the classic UI the control was buried three clicks deep
// in the Options drawer, so it was hard to hit by accident; the shell moved it into the
// title bar, one click from anywhere, which is right for visibility and wrong for a
// destructive default. Loading a large model is minutes of work and there is no undo.
//
// So: ask first, but only when there is actually something to lose. A confirmation on
// an empty scene is a dialog that teaches people to dismiss dialogs.
//
// The decision and the wording live in scopeChangeRules so they can be tested; this file
// is only the wiring.

export {scopeChangeConfirmRequest, scopeChangeLoss} from "./scopeChangeRules";

/**
 * Switch scope, asking first if that would discard loaded models.
 *
 * Returns false when nothing changed — callers rendering a <select> need this, because
 * the element has already moved to the new option by the time they hear about it and
 * has to be put back.
 */
export async function requestScopeChange(picked: ScopeOption): Promise<boolean> {
    const current = useScopeStore.getState().current;
    // Re-picking the scope you are already in should never prompt, and should never
    // tear anything down.
    if (current && scopeUrlPart(current) === scopeUrlPart(picked)) return false;

    const {willDiscard, names} = scopeChangeLoss({
        sourceNames: useModelState.getState().loadedSourceNames,
        // modelKeyMapRef is what clear_loaded_model actually tears down, so it is the
        // honest answer to "is there anything to lose" regardless of how it got there.
        sceneGroupCount: modelKeyMapRef.current?.size ?? 0,
    });

    if (willDiscard) {
        const ok = await confirm(scopeChangeConfirmRequest(names, picked.name));
        if (!ok) return false;
    }

    applyScopeChange(picked);
    return true;
}
