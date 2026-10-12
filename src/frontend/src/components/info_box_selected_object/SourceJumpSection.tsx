import React, {useEffect, useMemo, useState} from 'react';

import type {TreeNodeData} from '@/components/tree_view/CustomNode';
import {sourceJumpFor, type TreeStep} from '@/assets/sourceJump';
import {searchExportApi, type FileMeta} from '@/services/api/searchExport';
import {useViewerStores} from '@/state/AdaViewerContext';
import {scopeUrlPart} from '@/state/scopeStore';
import {ancestorChain, buildTreeIndices} from '@/utils/tree_view/treeGraph';
import {revealInSourceScope} from '@/utils/tree_view/revealInSourceScope';

/**
 * "Show in source tree" for an element of an exported file.
 *
 * A search-result export writes each element's source node into the IFC (`ADA_Source`: provider,
 * collection, node id, full name, and the scope the search ran in), and lists those sets by IFC name
 * in the file's provenance record (`/files/meta`). This section finds the selected element's row in
 * the Scene tree, its ancestors up to the file's root row, and the file the root was loaded from;
 * the record of that file says which of those rows came from which source node
 * (`sourceJumpFor`, `@/assets/sourceJump`): the element's own, else its nearest ancestor's, with the
 * path above it built from the ancestors' sets. A click switches to the node's scope when it is not
 * the current one and walks the Sources tree to it (`revealInSourceScope`); why it could not is
 * said inline.
 *
 * Renders nothing for an element of any other file -- one without a record, or a record naming no
 * source node -- so ordinary models show no extra row.
 */

/** Provenance records already asked for, per loaded model (the key it was loaded under + its file):
 *  every selection in one file reads the same record, and after a scope switch the record read in
 *  the file's own scope is still the one that applies. */
const records = new Map<string, Promise<FileMeta | null>>();

function recordFor(scope: string, modelKey: string, file: string): Promise<FileMeta | null> {
    const k = `${modelKey}|${file}`;
    let p = records.get(k);
    if (!p) {
        p = searchExportApi.fileMeta(scope, file).catch(() => null);
        records.set(k, p);
    }
    return p;
}

/** A stored file the record endpoint can answer for -- not a published asset view. */
function isStoredFile(sourceName: string | null | undefined): sourceName is string {
    return !!sourceName && !sourceName.startsWith('assets:') && /\.(ifc|glb|step|stp)$/i.test(sourceName);
}

const SourceJumpSection: React.FC = () => {
    const {useObjectInfoStore, useTreeViewStore, useSelectedObjectStore, useScopeStore} = useViewerStores();
    const selectedNodeId = useObjectInfoStore((s) => s.selectedNodeId);
    const name = useObjectInfoStore((s) => s.name);
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    const treeData = useTreeViewStore((s) => s.treeData);
    const findNodeByRangeId = useTreeViewStore((s) => s.findNodeByRangeId);
    const scope = scopeUrlPart(useScopeStore((s) => s.current));

    const indices = useMemo(() => (treeData ? buildTreeIndices(treeData) : null), [treeData]);

    // The selected row: the one element picked in 3D (by model key + range id, the unique handle),
    // else the row picked in the tree (a container has many ranges, or none).
    const node: TreeNodeData | null = useMemo(() => {
        if (!indices) return null;
        let picked: TreeNodeData | null = null;
        let count = 0;
        for (const [mesh, ranges] of selectedObjects) {
            const key: string | undefined = (mesh as any).unique_key ?? mesh.userData?.unique_hash;
            for (const rid of ranges) {
                count++;
                if (key) picked = findNodeByRangeId(key, rid);
            }
        }
        if (count === 1 && picked) return indices.byId.get(picked.id) ?? picked;
        return selectedNodeId ? indices.byId.get(selectedNodeId) ?? null : null;
    }, [indices, selectedObjects, selectedNodeId, findNodeByRangeId, name]);

    // Its rows up to the file's root row (the one that knows what it was loaded from).
    const {chain, root} = useMemo(() => {
        if (!node || !indices) return {chain: [] as TreeStep[], root: null as TreeNodeData | null};
        const out: TreeStep[] = [];
        let top: TreeNodeData | null = null;
        for (const n of ancestorChain(node, indices)) {
            if (n === indices.root && !n.source_name) break;
            out.push({id: n.id, name: n.name});
            if (n.source_name) {
                top = n;
                break;
            }
        }
        return {chain: out, root: top};
    }, [node, indices]);

    const file = root?.source_name ?? null;
    const modelKey = root?.model_key ?? '';
    const [meta, setMeta] = useState<FileMeta | null>(null);
    useEffect(() => {
        setMeta(null);
        if (!isStoredFile(file)) return;
        let live = true;
        void recordFor(scope, modelKey, file).then((m) => {
            if (live) setMeta(m);
        });
        return () => {
            live = false;
        };
        // The scope is read once per model (cached): a later switch must not re-ask it elsewhere.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [file, modelKey]);

    const jump = useMemo(() => (meta ? sourceJumpFor(chain, meta) : null), [chain, meta]);
    const [note, setNote] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    useEffect(() => setNote(null), [jump?.hit.node_id, node?.id]);

    if (!jump) return null;
    const otherScope = !!jump.scope && jump.scope !== scope;
    const onReveal = async () => {
        setBusy(true);
        setNote(otherScope ? `switching to ${jump.scope}…` : 'opening…');
        try {
            const out = await revealInSourceScope(jump.hit, jump.scope);
            if (out.status === 'not-found') setNote(out.message);
            else if (out.status === 'ancestor') setNote('only an ancestor is in the tree -- shown that instead');
            else setNote(null);
        } catch (e) {
            setNote(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };
    const whose =
        jump.via === 'self' ? 'this element' : jump.via === 'ancestor' ? `its level ${jump.element}` : 'the file';
    return (
        <div className="mt-2 border-t border-gray-500/40 pt-2 text-xs">
            <div className="font-semibold mb-1">Source</div>
            <div className="break-all">
                <span className="opacity-70">{jump.hit.collection} </span>
                <span className="font-mono">{jump.hit.full_name}</span>
            </div>
            {jump.via !== 'self' && <div className="opacity-70">from {whose}</div>}
            <div className="mt-1 flex items-center gap-2">
                <button
                    type="button"
                    disabled={busy}
                    onClick={() => void onReveal()}
                    className="bg-blue-700 hover:bg-blue-600 active:bg-blue-800 disabled:opacity-50 text-white text-[11px] rounded-sm px-2 py-1"
                    title={
                        (otherScope ? `Switch to scope ${jump.scope} and open` : 'Open') +
                        ` the Sources tree at ${jump.hit.full_name} (${jump.hit.provider} / ${jump.hit.collection})`
                    }
                >
                    Show in source tree
                </button>
                {note && (
                    <span className="truncate text-amber-300" title={note}>
                        {note}
                    </span>
                )}
            </div>
        </div>
    );
};

export default SourceJumpSection;
