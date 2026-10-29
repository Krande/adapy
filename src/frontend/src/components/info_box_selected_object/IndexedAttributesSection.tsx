import React, {useEffect, useMemo, useState} from 'react';

import {useSelectedTreeRow} from '@/hooks/useSelectedTreeRow';
import {
    attributeLookupFor,
    attributesRequestPermissionFor,
    forgetCollection,
    lookupKey,
    nodeAttributesFor,
} from '@/assets/indexedAttributes';
import {attributeSearchApi, type NodeAttributesResponse} from '@/services/api/attributeSearch';
import {useViewerStores} from '@/state/AdaViewerContext';
import {scopeUrlPart} from '@/state/scopeStore';

/**
 * The selected object's attributes from the attribute index, read for the one selected row of a
 * model loaded from a published asset (`@/assets/indexedAttributes`).
 *
 * Renders nothing when the row has no lookup, the index has no such node, or the read fails. When
 * the collection has no index at all and its provider can stage one for this user, offers to
 * request it; the run shows in the toast, and the row is read again when it ends.
 */
const IndexedAttributesSection: React.FC = () => {
    const {useScopeStore, useAssetBrowserStore} = useViewerStores();
    const loaded = useAssetBrowserStore((s) => s.loaded);
    const scope = scopeUrlPart(useScopeStore((s) => s.current));

    // The one element picked in 3D (by model key + range id), else the row picked in the tree.
    const {row: node, indices} = useSelectedTreeRow();

    const lookup = useMemo(
        () => (node && indices ? attributeLookupFor(node, indices, loaded) : null),
        [node, indices, loaded],
    );
    const key = lookup ? lookupKey(scope, lookup) : null;

    // Bumped when a requested index lands, to read the row again.
    const [generation, setGeneration] = useState(0);
    const [answer, setAnswer] = useState<{key: string; res: NodeAttributesResponse | null} | null>(null);
    useEffect(() => {
        if (!lookup || !key) return;
        let live = true;
        void nodeAttributesFor(scope, lookup).then((res) => {
            if (live) setAnswer({key, res});
        });
        return () => {
            live = false;
        };
        // `key` stands for `lookup` and `scope`.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key, generation]);
    const res = answer && answer.key === key ? answer.res : null;

    const notIndexed = !!lookup && !!res && !res.indexed;
    const [canRequest, setCanRequest] = useState<{provider: string; ok: boolean} | null>(null);
    useEffect(() => {
        if (!notIndexed || !lookup) return;
        let live = true;
        void attributesRequestPermissionFor(scope, lookup.provider).then((p) => {
            if (live) setCanRequest({provider: lookup.provider, ok: !!p && p.available && p.allowed});
        });
        return () => {
            live = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [notIndexed, lookup?.provider, scope]);

    const [expanded, setExpanded] = useState(false);
    // Collections requested in this panel's lifetime, so a second click does not start a second run.
    const [requested, setRequested] = useState<ReadonlySet<string>>(new Set());
    const [note, setNote] = useState<string | null>(null);
    useEffect(() => setNote(null), [key]);

    if (!lookup || !res) return null;

    if (res.node) {
        const entries = Object.entries(res.node.attrs).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
        if (!entries.length) return null;
        return (
            <div className="mt-2">
                <button
                    type="button"
                    onClick={() => setExpanded((v) => !v)}
                    className="flex items-center gap-1 text-[12px] text-gray-100 hover:text-white"
                    aria-expanded={expanded}
                    aria-controls="object-indexed-attributes"
                    title={`${res.node.provider} / ${res.node.collection}${res.node.revision ? ` @ ${res.node.revision}` : ''}`}
                >
                    <Chevron open={expanded}/>
                    <span className="font-semibold">Attributes</span>
                    <span className="opacity-70">({entries.length})</span>
                </button>
                {expanded && (
                    <div id="object-indexed-attributes" className="mt-1 ml-4 table text-xs">
                        {res.node.kind && (
                            <div className="table-row">
                                <div className="table-cell w-24 align-top opacity-70">kind</div>
                                <div className="table-cell w-48 break-all">{res.node.kind}</div>
                            </div>
                        )}
                        {entries.map(([k, v]) => (
                            <div key={k} className="table-row">
                                <div className="table-cell w-24 align-top opacity-70 break-all pr-2">{k}</div>
                                <div className="table-cell w-48 break-all select-text">{String(v)}</div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        );
    }

    if (!notIndexed || !canRequest?.ok || canRequest.provider !== lookup.provider) return null;
    const collectionId = `${lookup.provider}/${lookup.collection}`;
    const isRequested = requested.has(collectionId);
    const onRequest = async () => {
        setRequested((s) => new Set(s).add(collectionId));
        setNote(null);
        try {
            const {trackAttributesRequest} = await import('@/services/sweepTracking');
            const run = await attributeSearchApi.requestAttributes(scope, lookup.provider, [lookup.collection]);
            trackAttributesRequest(scope, run.id, `Attributes: ${lookup.collection}`, () => {
                forgetCollection(scope, lookup.provider, lookup.collection);
                setRequested((s) => {
                    const next = new Set(s);
                    next.delete(collectionId);
                    return next;
                });
                setGeneration((g) => g + 1);
            });
        } catch (e) {
            setRequested((s) => {
                const next = new Set(s);
                next.delete(collectionId);
                return next;
            });
            setNote(e instanceof Error ? e.message : String(e));
        }
    };
    return (
        <div className="mt-2 text-xs flex items-center gap-2">
            <span className="opacity-70">No attribute index for {lookup.collection}</span>
            <button
                type="button"
                disabled={isRequested}
                onClick={() => void onRequest()}
                className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 disabled:opacity-50 text-white text-[11px] rounded-sm px-2 py-1"
                title={`Ask ${lookup.provider} to index the attributes of ${lookup.collection}`}
            >
                {isRequested ? 'Requested…' : 'Request attributes'}
            </button>
            {note && (
                <span className="truncate text-amber-300" title={note}>
                    {note}
                </span>
            )}
        </div>
    );
};

const Chevron: React.FC<{open: boolean}> = ({open}) => (
    <svg
        viewBox="0 0 16 16"
        className={'w-3 h-3 transition-transform duration-150 ease-out ' + (open ? 'rotate-90' : 'rotate-0')}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
    >
        <path d="M6 4l4 4-4 4"/>
    </svg>
);

export default IndexedAttributesSection;
