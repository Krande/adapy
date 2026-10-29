import React, {useState} from 'react';

import {useRelatedActions} from '@/hooks/useRelatedActions';
import {useSelectedTreeRow} from '@/hooks/useSelectedTreeRow';
import {systemKindLabel} from '@/utils/systems/systems';

/**
 * What the selected element is related to, from the relations index: its system and run, and the
 * actions on them (select or load the system or the run, select what it names). Renders nothing for
 * an element without relations. Published on the facade, so a UI shell mounts it in its own panel.
 */
const RelatedSection: React.FC = () => {
    const {row} = useSelectedTreeRow();
    const {actions, loading, related} = useRelatedActions(row);
    const [message, setMessage] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);

    if (loading || !related || !actions.length) return null;
    const run = async (key: string, f: () => Promise<string | void>) => {
        setBusy(key);
        setMessage(null);
        try {
            const said = await f();
            if (said) setMessage(said);
        } catch (e) {
            setMessage(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(null);
        }
    };
    return (
        <div className="mt-2 text-[12px] text-gray-100">
            <div className="font-semibold">Related</div>
            <div className="mt-1 ml-4 table text-xs">
                {related.system && (
                    <div className="table-row">
                        <div className="table-cell w-24 align-top opacity-70">{systemKindLabel(related.system.kind)} system</div>
                        <div className="table-cell w-48 break-all">{related.system.name}</div>
                    </div>
                )}
                {related.run && (
                    <div className="table-row">
                        <div className="table-cell w-24 align-top opacity-70">run</div>
                        <div className="table-cell w-48 break-all">{related.run}</div>
                    </div>
                )}
            </div>
            <div className="mt-1 ml-4 flex flex-wrap gap-1">
                {actions.map((a) => (
                    <button
                        key={a.key}
                        type="button"
                        title={a.title}
                        disabled={a.disabled || busy !== null}
                        onClick={() => void run(a.key, a.run)}
                        className="h-6 px-2 rounded-md text-xs border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50"
                    >
                        {busy === a.key ? '…' : a.label}
                    </button>
                ))}
            </div>
            {message && <p className="mt-1 ml-4 text-xs text-gray-300">{message}</p>}
        </div>
    );
};

export default RelatedSection;
