import React from "react";
import {ErrorBoundary} from "@/components/common/ErrorBoundary";
import {providersFor} from "./propertyProviders";
import {useSelectionSnapshot} from "./useSelectionSnapshot";
import "./registerCoreProviders";
import {EmptyState} from "@/components/ui";

// One panel that follows selection, in every mode.
//
// Mode-independent by contract (see the non-modality note in shell/modeStore.ts):
// clicking a cellbuilder cell while in Results mode fills this panel with that cell.
// Modes change which TOOLS are offered, never what you may inspect.
//
// Composition, not a switch: whatever providers match get rendered in order. Adding a
// new selectable kind means registering a provider, not editing this file — which is
// also how a plugin contributes selection detail.

export default function PropertiesPanel() {
    const selection = useSelectionSnapshot();
    const matched = providersFor(selection);

    if (matched.length === 0) {
        return (
            <EmptyState
                title="Nothing selected"
                hint={
                    selection.hasEntities
                        ? "Click an element in the viewport or the outliner."
                        : "Load a model to inspect its parts."
                }
            />
        );
    }

    return (
        <div className="flex flex-col gap-2 p-2 min-w-0">
            {matched.map((p) => (
                // Per-provider isolation: one provider throwing must not blank the whole
                // panel and take the rest of the selection detail with it.
                <ErrorBoundary key={p.id} label={p.owner ? `${p.owner} (${p.id})` : p.id}>
                    {p.render()}
                </ErrorBoundary>
            ))}
        </div>
    );
}
