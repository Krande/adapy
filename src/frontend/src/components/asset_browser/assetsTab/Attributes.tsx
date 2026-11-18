// The selected row's attributes, read from the provider that published them.

import React, { useEffect } from "react";
import type { WireNodeAttributes } from "@/assets/types";
import type { ScopeUrl } from "@/services/api/client";
import { fetchAssetAttributes } from "@/services/assets";

/** What the selected node IS -- fetched when it is selected, never before.
 *
 *  Attributes are per-node and most nodes are never selected, so carrying them in the spine would
 *  pay for the whole tree to answer for a handful of rows. This fetches one node, in the
 *  background, and shows nothing at all while it is in flight: a spinner over two lines of
 *  properties is more movement than the information is worth.
 *
 *  `subject` is the covering publish (`badge.at`), which is this row for a node published in its
 *  own right and an ancestor for one covered from above. Without it a covered node asks for a
 *  manifest that does not exist and gets the "nothing recorded" answer while its properties sit in
 *  its ancestor's document.
 *
 *  Absence is silence. A provider that publishes no attributes, a document that does not mention
 *  this node and an unpublished node all render nothing -- a caller asking what something is
 *  cannot act differently on those, and a row that said "no properties" would imply somebody
 *  checked. A real FAILURE is shown, because that is not absence. */
export const Attributes: React.FC<{ scope: string; provider: string; collection: string; node: string; subject: string | null }> = ({
    scope,
    provider,
    collection,
    node,
    subject,
}) => {
    const [state, setState] = React.useState<{ kind: "none" } | { kind: "ok"; body: WireNodeAttributes } | { kind: "error"; message: string }>({ kind: "none" });

    useEffect(() => {
        let live = true;
        setState({ kind: "none" });
        fetchAssetAttributes(scope as ScopeUrl, provider, collection, node, subject ? { subject } : undefined)
            .then((body) => {
                if (!live) return;
                setState(body ? { kind: "ok", body } : { kind: "none" });
            })
            .catch((e) => {
                if (live) setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
            });
        // A selection that moves before the answer lands must not paint the old node's
        // properties under the new node's name.
        return () => {
            live = false;
        };
    }, [scope, provider, collection, node, subject]);

    if (state.kind === "none") return null;
    if (state.kind === "error") {
        return (
            <div className="mt-1 text-red-300 break-words" data-testid="asset-attributes-error">
                properties unavailable: {state.message}
            </div>
        );
    }

    const { own, groups, quantities } = state.body;
    const sections: [string, Readonly<Record<string, unknown>>][] = [
        ...Object.entries(groups),
        ...Object.entries(quantities),
    ];
    return (
        <div className="mt-1 border-t border-gray-800 pt-1" data-testid="asset-attributes">
            {Object.entries(own).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                    <span className="text-gray-400 w-20 shrink-0">{k}</span>
                    <span className="min-w-0 break-words">{String(v)}</span>
                </div>
            ))}
            {sections.map(([name, props]) => (
                <div key={name} className="mt-1">
                    <div className="text-gray-400">{name}</div>
                    {Object.entries(props).map(([k, v]) => (
                        <div key={k} className="flex gap-2 pl-2">
                            <span className="text-gray-400 w-20 shrink-0 truncate" title={k}>
                                {k}
                            </span>
                            <span className="min-w-0 break-words">{String(v)}</span>
                        </div>
                    ))}
                </div>
            ))}
        </div>
    );
};
