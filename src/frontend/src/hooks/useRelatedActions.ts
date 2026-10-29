import {useEffect, useState} from "react";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {relatedActions, type RelatedAction} from "@/utils/systems/relatedActions";
import {collectionOfRow, currentScope, relatedOf, type Related} from "@/utils/systems/systems";

/** The related actions for a scene-tree row of a published model, read from the relations index:
 *  `loading` while reading, and no actions for a row of anything else or a collection without
 *  relations. Published on the facade so a UI shell can list them in its own menu or panel. */
export function useRelatedActions(row: TreeNodeData | null): {actions: RelatedAction[]; loading: boolean; related: Related | null} {
    const [state, setState] = useState<{related: Related | null; loading: boolean}>({related: null, loading: false});
    useEffect(() => {
        const collection = row ? collectionOfRow(row) : null;
        if (!row || !collection) {
            setState({related: null, loading: false});
            return;
        }
        let live = true;
        setState({related: null, loading: true});
        relatedOf(currentScope(), collection, row.name)
            .then((related) => live && setState({related, loading: false}))
            .catch(() => live && setState({related: null, loading: false}));
        return () => {
            live = false;
        };
    }, [row]);
    const actions = row && state.related ? relatedActions(row.name, state.related) : [];
    return {actions, loading: state.loading, related: state.related};
}
