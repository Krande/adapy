import React from "react";

import {useLabelsStore} from "@/state/labelsStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import {nameLabels, showNameLabels, type NameLabel} from "@/utils/viewer/nameLabels";
import {followActiveViewport, onModelLoaded, type ViewportApi} from "@/viewport/api/viewportApi";

// Headless: labels each selected object with its name while the "Object names" switch is on
// (`useLabelsStore.names`), as billboards of whichever viewport is on screen. The name is the Scene
// tree row's; the viewport says where the object is. Renders nothing.
const NameLabelController: React.FC = () => {
    React.useEffect(() => followActiveViewport(init), []);
    return null;
};

function init(viewport: ViewportApi): () => void {
    const layer = viewport.billboards("__name_labels__");
    const shown = new Map<string, {handle: number; label: NameLabel}>();

    const refresh = () => {
        // Off, which is the usual state, costs a selection change one flag read.
        if (!useLabelsStore.getState().names) {
            if (shown.size) showNameLabels(layer, shown, []);
            return;
        }
        const tree = useTreeViewStore.getState();
        showNameLabels(
            layer,
            shown,
            nameLabels(
                viewport.selection(),
                (ref) => tree.findNodeByRangeId(ref.model, ref.id)?.name,
                (ref) => viewport.objectsCentre([ref]),
            ),
        );
    };

    refresh();
    const offs = [
        useLabelsStore.subscribe((s, prev) => {
            if (s.names !== prev.names) refresh();
        }),
        viewport.onSelectionChange(refresh),
        // A newly loaded model starts with nothing selected, and its rows (the names) arrive after it.
        onModelLoaded(refresh),
        useTreeViewStore.subscribe((s, prev) => {
            if (s.treeData !== prev.treeData && useLabelsStore.getState().names) refresh();
        }),
    ];
    return () => {
        for (const off of offs) off();
        layer.dispose();
    };
}

export default NameLabelController;
