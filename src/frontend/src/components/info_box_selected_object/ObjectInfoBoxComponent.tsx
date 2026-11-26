import {PanelCard} from "@/components/ui/PanelFrame";
import React, {useSyncExternalStore} from 'react';
import {ErrorBoundary} from '@/components/common/ErrorBoundary';
import {
    applyProviderLayout,
    listPropertyProviders,
    providersFor,
    subscribePropertyProviders,
    type PropertyProvider,
    type SelectionSnapshot,
} from '@/plugins/propertyProviders';
import {ObjectInfoOptions, type ObjectInfoBodyProps} from './objectInfoOptions';
import {useSelectionSnapshot} from './useSelectionSnapshot';
import {CORE_PROPERTY_PROVIDERS} from './coreSections';

export type {ObjectInfoBodyProps};
export {CORE_PROPERTY_PROVIDERS};

/** `providersFor`'s list, kept to those the shell's `sections` lets through; a throwing filter keeps the section. */
export function visibleSections(
    list: readonly PropertyProvider[],
    sel: SelectionSnapshot,
    sections: ObjectInfoBodyProps["sections"],
): PropertyProvider[] {
    if (!sections) return [...list];
    return list.filter((p) => {
        try {
            return sections(p.id, sel);
        } catch (err) {
            console.warn(`[properties] sections filter threw for "${p.id}" -- kept`, err);
            return true;
        }
    });
}

/**
 * The Selected Object Info panel's content: the selection's name, actions and
 * every section that describes it -- core's and any a plugin registered
 * (`registerPropertyProvider`), each in its own error boundary. Its frame (the
 * card and heading) is `ObjectInfoBox` below; another host may draw its own.
 */
export const ObjectInfoBody: React.FC<ObjectInfoBodyProps> = ({onShowInData, sections, providers, flat, builderSummary = true, summary = "full", selectionSource = "any"}) => {
    const sel = useSelectionSnapshot(selectionSource);
    const plugins = useSyncExternalStore(subscribePropertyProviders, listPropertyProviders, listPropertyProviders);
    const options = React.useMemo(() => ({onShowInData, flat, summary}), [onShowInData, flat, summary]);
    // `providers` (the shell's static show/hide/order) runs before `sections` (which can see the
    // selection and so expresses what a static layout cannot): passing both is safe. A throw in
    // either falls back to the unfiltered list for that one, never drops the whole panel.
    let withLayout: PropertyProvider[];
    try {
        withLayout = applyProviderLayout(providersFor(sel, CORE_PROPERTY_PROVIDERS, plugins), providers);
    } catch (err) {
        console.warn("[properties] applyProviderLayout threw -- shown unfiltered", err);
        withLayout = providersFor(sel, CORE_PROPERTY_PROVIDERS, plugins);
    }
    return (
        <ObjectInfoOptions.Provider value={options}>
            {visibleSections(withLayout, sel, sections)
                .filter((p) => builderSummary || p.id !== "selection-summary" || (sel.kind !== "cell" && sel.kind !== "ref"))
                .map((p) => (
                    <ErrorBoundary key={p.id} label={p.owner ? `${p.owner} (${p.id})` : p.id}>
                        {p.render(sel)}
                    </ErrorBoundary>
                ))}
        </ObjectInfoOptions.Provider>
    );
};

/** The classic frame: the menu-column card, capped in height so a long record scrolls inside. */
const ObjectInfoBox = () => (
    <PanelCard className="min-w-80 max-h-[80svh] overflow-y-auto" heading="Selected Object Info">
        <ObjectInfoBody/>
    </PanelCard>
);

export default ObjectInfoBox;
