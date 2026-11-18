// The detail block under the Sources tree for the selected row(s).

import React from "react";
import type { AssetView } from "@/assets/assetView";
import { orphanSentence } from "@/assets/orphans";
import { providerIdTitle } from "@/assets/providerNames";
import { rowFacts } from "@/assets/rowFacts";
import { useProviderName } from "@/state/providerNamesStore";
import { formatRevision } from "../format";
import type { NodeRequestControl } from "./loads";
import { CHANGE_STATE_LABEL } from "./chrome";
import { Attributes } from "./Attributes";
import { LoadControls } from "./LoadControls";
import { RequestControls } from "./RequestControls";

/** The selected row's detail: facts and properties in a scrolling body, and the row's actions on
 *  a bottom row that never scrolls. The properties are the provider's and can run to dozens of
 *  lines; with the actions inside the scroll, "Load into scene" was pushed out of sight by them. */
export const Detail: React.FC<{
    view: AssetView;
    id: string;
    /** What the bottom row's actions apply to: `[id]`, or the selection's topmost rows. The facts
     *  above are always `id`'s -- the focused row. */
    ids: readonly string[];
    scope: string;
    requests: readonly NodeRequestControl[];
    actions?: React.ReactNode;
}> = ({ view, id, ids, scope, requests, actions }) => {
    const pn = useProviderName();
    const facts = rowFacts(view, id);
    const orphan = view.orphans.find((o) => o.id === id);
    const resolved = view.resolution.subjects.get(id);
    const manifest = resolved?.revision.manifest ?? null;
    const lines: [string, React.ReactNode][] = [];
    if (facts) {
        lines.push(["Id", <span className="font-mono text-[11px]">{id}</span>]);
        lines.push(["Provider", <span title={providerIdTitle(facts.node.provider)}>{pn(facts.node.provider)}</span>]);
        lines.push(["Claim", facts.node.delivery === "none" ? "none" : facts.node.delivery]);
    }
    if (resolved) lines.push(["Published", `${formatRevision(resolved.revision.revision)}${manifest ? ` · ${manifest.delivery}` : ""}`]);
    else if (facts) lines.push(["Published", "not as a subject of its own"]);
    if (facts?.badge && facts.badge.weight !== "solid") {
        lines.push([facts.badge.weight === "ghost" ? "Covered by" : "Content below", `${facts.badge.at} @ ${formatRevision(facts.badge.revision)}`]);
    }
    if (facts?.freshness?.stale) {
        lines.push(["Drawn from", `${formatRevision(facts.freshness.shownAt)} — now resolves to ${formatRevision(facts.freshness.resolvedAt!)}`]);
    }
    if (facts?.drift) {
        lines.push(["Tree", `published against ${formatRevision(facts.drift.publishedAgainst)}; shown from ${formatRevision(facts.drift.shownFrom)}`]);
    }
    // Behind-upstream is its OWN line, never merged into "Drawn from" above:
    // that line is about OUR spine lagging OUR resolution (fixed by Refresh),
    // this one is about the SOURCE moving past what was published (fixed only
    // by a new export). Same sentence for both would say the wrong fix.
    const change = view.changes.byRoot.get(id);
    if (change && change.state === "behind") {
        lines.push([
            "Behind source",
            `changed ${change.lastChangedAt ?? "—"}${change.lastChangedBy ? ` by ${change.lastChangedBy}` : ""} — after this was published; re-export to catch up`,
        ]);
    } else if (change) {
        lines.push(["Source", CHANGE_STATE_LABEL[change.state]]);
    }
    // Two separately-labelled facts, never merged into one
    // "author" -- `publishedBy` is core-stamped and trustworthy, `sourceActor`
    // is merely relayed by the provider from its own source. Absent is the
    // normal case and renders nothing at all, not a placeholder.
    if (facts?.changeRecord?.publishedBy) {
        const a = facts.changeRecord.publishedBy;
        lines.push(["Published by", a.display ? `${a.display} (${a.id})` : a.id]);
    }
    if (facts?.changeRecord?.sourceActor) {
        const a = facts.changeRecord.sourceActor;
        lines.push(["Source says", a.display ? `${a.display} (${a.id})` : a.id]);
    }
    if (orphan) lines.push(["Not in tree", orphanSentence(orphan, formatRevision)]);
    const err = view.manifestErrors.get(id);
    if (err) lines.push(["Manifest", err]);
    return (
        <div className="border-t border-gray-700/70 bg-gray-900/40 text-xs text-gray-200 shrink-0 max-h-72 flex flex-col" data-testid="asset-detail">
            <div className="min-h-0 overflow-auto scrollbar px-3 pt-2.5 pb-2 space-y-2">
                <div className="flex items-baseline gap-2 min-w-0" title={id}>
                    <span className="font-semibold text-[13px] text-white truncate">{facts?.node.label ?? id}</span>
                    {facts?.node.kind && (
                        <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-gray-400">{facts.node.kind}</span>
                    )}
                </div>
                <dl className="grid grid-cols-[5.5rem_1fr] gap-x-2 gap-y-1 m-0">
                    {lines.map(([k, v]) => (
                        <React.Fragment key={k}>
                            <dt className="text-gray-400">{k}</dt>
                            <dd className="m-0 min-w-0 break-words">{v}</dd>
                        </React.Fragment>
                    ))}
                </dl>
                {facts && (
                    <Attributes
                        scope={scope}
                        provider={facts.node.provider}
                        collection={view.collection}
                        node={id}
                        subject={facts.badge?.at ?? null}
                    />
                )}
            </div>
            {ids.length > 1 && (
                <div
                    className="shrink-0 border-t border-gray-700/70 px-3 pt-1.5 text-gray-400"
                    title={ids.map((n) => rowFacts(view, n)?.node.label ?? n).join("\n")}
                >
                    {ids.length} selected — the actions below apply to all of them
                </div>
            )}
            <div
                className={`shrink-0 flex items-center gap-2 px-3 py-1.5 min-h-[2.5rem] ${ids.length > 1 ? "" : "border-t border-gray-700/70"}`}
                data-testid="asset-detail-actions"
            >
                <LoadControls view={view} ids={ids} scope={scope} requests={requests} />
                {requests.length > 0 && <RequestControls requests={requests} />}
                {actions &&<div className="ml-auto shrink-0 flex items-center">{actions}</div>}
            </div>
        </div>
    );
};
