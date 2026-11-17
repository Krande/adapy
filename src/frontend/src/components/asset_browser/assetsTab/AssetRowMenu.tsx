// A row's right-click menu in the Sources tab.

import React from "react";
import type { AssetView } from "@/assets/assetView";
import { providerIdTitle } from "@/assets/providerNames";
import { rowFacts } from "@/assets/rowFacts";
import PositionedMenu, { type KebabMenuItem } from "@/components/common/PositionedMenu";
import { useProviderName } from "@/state/providerNamesStore";
import { formatRevision } from "../format";
import { NOTHING_TO_LOAD, NOTHING_TO_LOAD_ANY, loadGroups, loadSelection, plural, requestLoadsFor, useAssetLoads, type NodeRequestControl } from "./loads";
import { useJointsCheck } from "./useJointsCheck";
import { REQUEST_TITLE } from "./RequestControls";

/** Right-click menu for a tree row -- or for the selection it is part of (`ids`, its topmost rows):
 *  every action then applies to all of them. */
export const AssetRowMenu: React.FC<{
    view: AssetView;
    /** The row right-clicked. */
    id: string;
    /** What the actions apply to: `[id]`, or the selection it is part of. */
    ids: readonly string[];
    scope: string;
    x: number;
    y: number;
    requests: readonly NodeRequestControl[];
    /** Several requestable providers: the menu item opens the picker here instead of asking one. */
    onPickRequests: (x: number, y: number) => void;
    /** The out-of-scope switch for these rows, or null where it does not apply. */
    scopeItem: KebabMenuItem | null;
    /** "Add to set" / "Remove from set", one per set of the collection. */
    setItems: readonly KebabMenuItem[];
    onClose: () => void;
}> = ({ view, id, ids, scope, x, y, requests, onPickRequests, scopeItem, setItems, onClose }) => {
    const many = ids.length > 1;
    const pn = useProviderName();
    const loads = useAssetLoads(view, ids, scope);
    const onDemand = requestLoadsFor(view, ids, loads, requests);
    const joints = useJointsCheck(view, id, scope);
    const items: KebabMenuItem[] = [];
    // A provider that can be asked on demand loads from here too: confirm, request, load.
    for (const r of onDemand) {
        const from = loads.length + onDemand.length > 1 ? ` · ${pn(r.provider)}` : "";
        items.push({
            key: `request-load:${r.provider}`,
            label: r.busy ? `Requesting…${from}` : `Load into scene${from}`,
            disabled: r.busy || !!r.blocked,
            title: `${r.blocked ?? `Not published here yet — requests it from ${pn(r.provider)} first, then loads it`}\n${providerIdTitle(r.provider)}`,
            onClick: r.run,
        });
    }
    if (!loads.length && !onDemand.length) {
        items.push({ key: "load", label: "Load into scene", disabled: true, onClick: () => {}, title: many ? NOTHING_TO_LOAD_ANY : NOTHING_TO_LOAD });
    }
    if (many) {
        const groups = loadGroups(loads);
        for (const g of groups) {
            const from = groups.length > 1 ? ` · ${pn(g.provider)}` : "";
            items.push({
                key: `load:${g.provider}`,
                label: g.toLoad.length ? `Load ${plural(g.toLoad.length, "model")} into scene${from}` : `All loaded${from}`,
                disabled: !g.toLoad.length,
                title: g.busy ? `${g.busy} still loading` : undefined,
                onClick: () => void loadSelection(scope, view.collection, g.toLoad),
            });
            if (g.loaded.length) {
                items.push({
                    key: `unload:${g.provider}`,
                    label: `Unload ${plural(g.loaded.length, "model")} from scene${from}`,
                    onClick: () => g.loaded.forEach((c) => c.unload()),
                });
            }
        }
    }
    const named = loads.length > 1;
    for (const load of many ? [] : loads) {
        const from = named ? ` · ${pn(load.provider)}` : "";
        if (load.loaded) {
            items.push({ key: `reveal:${load.provider}`, label: `Reveal in Scene${from}`, disabled: !load.root, onClick: load.reveal });
            items.push({ key: `unload:${load.provider}`, label: `Unload from scene${from}`, onClick: load.unload });
        } else {
            items.push({
                key: `load:${load.provider}`,
                label: load.busy ? `Loading…${from}` : `Load into scene${from}`,
                disabled: load.busy,
                title: `From ${pn(load.provider)} @ ${formatRevision(load.badge.revision)}\n${providerIdTitle(load.provider)}`,
                onClick: () => void load.load(),
            });
        }
    }
    if (requests.length === 1) {
        const request = requests[0];
        items.push({
            key: "request",
            label: request.busy ? "Requesting…" : request.label,
            disabled: request.busy || !!request.blocked,
            title: request.blocked ?? REQUEST_TITLE,
            onClick: request.run,
        });
    } else if (requests.length > 1) {
        items.push({
            key: "request",
            label: many ? `Request geometry for ${ids.length}…` : "Request geometry…",
            disabled: requests.every((r) => !!r.blocked),
            title: REQUEST_TITLE,
            onClick: () => onPickRequests(x, y),
        });
    }
    if (scopeItem) items.push(scopeItem);
    items.push(...setItems);
    // One node's analysis: offered for the row clicked, not for a selection.
    if (joints && !many) {
        items.push({
            key: "joints",
            label: "Check for joints",
            separatorBefore: true,
            disabled: joints.busy,
            title: "Identify the joints in this node's published source",
            onClick: joints.run,
        });
    }
    const label = many ? `${ids.length} selected` : (rowFacts(view, id)?.node.label ?? id);
    return <PositionedMenu items={items} anchor={{ kind: "point", x, y }} onClose={onClose} header={label} />;
};
