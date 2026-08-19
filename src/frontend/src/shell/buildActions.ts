import {needsPreviewCompile, useCellBuilderStore} from "@/state/cellBuilderStore";

// Build-mode rail actions.
//
// Same discipline as inspectActions and resultsActions: every one delegates to the
// cellbuilder store's own action, so the rail is a second entry point and never a second
// implementation. The undo stack in particular must have exactly one owner —
// utils/cellbuilder/history.ts, via the store — or a rail undo and a keyboard undo will
// drift apart in ways that are very hard to reason about mid-edit.

export function undo(): void {
    useCellBuilderStore.getState().undo();
}

export function redo(): void {
    useCellBuilderStore.getState().redo();
}

/**
 * Run the preview compile — the same thing ⇧↵ and the panel's Compile button do.
 *
 * Gated on `needsPreviewCompile` for the same reason the button is: on an unchanged model
 * whose results are already in the scene it is a no-op, and firing a worker job for a
 * no-op is worse than a disabled control.
 */
export function compilePreview(): void {
    const s = useCellBuilderStore.getState();
    if (!s.active || !needsPreviewCompile(s)) return;
    void s.compilePreview();
}

/** Is there a procedural model open at all? Drives honest disabling in the rail. */
export function builderActive(): boolean {
    return useCellBuilderStore.getState().active !== null;
}
