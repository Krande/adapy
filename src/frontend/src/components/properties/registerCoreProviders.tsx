import React from "react";
import {useViewerStores} from "@/state/AdaViewerContext";
import {registerPropertyProvider} from "./propertyProviders";
import {ruleFor} from "./coreProviderRules";
import SelectionSummary from "./SelectionSummary";
import ObjectMetadataPanel from "@/components/info_box_selected_object/ObjectMetadataPanel";
import CellBuilderSelectionInfo from "@/components/info_box_selected_object/CellBuilderSelectionInfo";

// Core's three selection-detail providers.
//
// These are the panels that previously composed ObjectInfoBoxComponent by hard
// reference. Their bodies are unchanged — this file only binds each to its rule, so the
// set becomes extensible (a plugin registers alongside them) and each panel's relevance
// becomes data rather than a nested conditional.
//
// The `match` predicates live in coreProviderRules.ts so they can be unit-tested without
// dragging in the viewer; this file is the render half.

/** ObjectMetadataPanel takes its payload as a prop; the store read lives here so the
 *  panel itself stays untouched. */
function ObjectMetadata() {
    const {useObjectInfoStore} = useViewerStores();
    const jsonData = useObjectInfoStore((s) => s.jsonData);
    return <ObjectMetadataPanel data={jsonData} />;
}

const bind = (id: string, render: () => React.ReactNode) => {
    const rule = ruleFor(id);
    if (!rule) {
        console.warn(`[properties] no rule for core provider "${id}" — not registered`);
        return;
    }
    registerPropertyProvider({id: rule.id, order: rule.order, match: rule.match, render});
};

bind("selection-summary", () => <SelectionSummary />);
bind("object-metadata", () => <ObjectMetadata />);
bind("cellbuilder-cell", () => <CellBuilderSelectionInfo />);
